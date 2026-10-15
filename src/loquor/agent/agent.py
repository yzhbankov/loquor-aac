"""The orchestrator: transport in, skills out, nothing else.

An :class:`Agent` is generic plumbing configured by a profile. It:

1. advertises its uid, name, description and the capabilities of its
   skills, every ``advert_interval`` seconds;
2. listens to everything: decodes, verifies the signature, drops
   duplicates, records adverts in the :class:`PeerDirectory`;
3. dispatches a request addressed to its uid to the skill with that
   capability and sends back its output (or an error), idempotently;
4. offers skills ``ask()``: send a request to a peer, retry, return the
   output.

Business logic — what to answer, whom to ask, when — lives in skills.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Iterable

from loquor.agent.context import Context
from loquor.agent.directory import Peer, PeerDirectory
from loquor.agent.skill import Skill
from loquor.agent.tracker import RequestError, RequestTracker
from loquor.protocol import (
    MAX_MESSAGE_SIZE,
    DecodeError,
    Message,
    MessageType,
    Signer,
    advert,
    decode,
    encode,
    error,
    request,
    response,
)
from loquor.reasoning.base import Reasoner
from loquor.transports.base import Address, Transport

log = logging.getLogger("loquor")
# Every message sent and received, one line each. Separate logger so it can
# be filtered (e.g. hide adverts) without hiding the agent's reasoning.
wire = logging.getLogger("loquor.wire")


class Agent:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        skills: Iterable[Skill],
        reasoner: Reasoner,
        transport: Transport,
        pairing_secret: str,
        uid: str | None = None,
        advert_interval: float = 10.0,
        request_timeout: float = 30.0,
        max_attempts: int = 3,
    ):
        self.name = name
        self.description = description
        self.uid = uid or f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
        self.skills = list(skills)
        self.reasoner = reasoner
        self.transport = transport
        self.signer = Signer(pairing_secret)
        self.advert_interval = advert_interval
        self.request_timeout = request_timeout
        self.max_attempts = max_attempts

        self.directory = PeerDirectory(ttl=3 * advert_interval)
        self.tracker = RequestTracker()
        self.context = Context(self)
        self.peer_heard = asyncio.Event()  # set on every advert; skills wait on it

        self._handlers: dict[str, Skill] = {}
        for skill in self.skills:
            if skill.capability:
                if skill.capability in self._handlers:
                    raise ValueError(f"two skills provide capability {skill.capability!r}")
                self._handlers[skill.capability] = skill
        self._seen: OrderedDict[str, None] = OrderedDict()  # msg_ids, for dedupe
        self._tasks: set[asyncio.Task] = set()

    @property
    def capabilities(self) -> list[str]:
        return list(self._handlers)

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.transport.start(self._on_receive)
        self._spawn(self._advertise_forever())
        for skill in self.skills:
            self._spawn(skill.run(self.context))
        log.info("%s started: %s", self.uid, ", ".join(self.capabilities) or "no capabilities")

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.transport.stop()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("%s: background task failed", self.uid, exc_info=task.exception())

    # --------------------------------------------------------------- wire

    async def _send(self, message: Message, to: Address | None = None) -> None:
        signed = self.signer.sign(message)
        data = encode(signed)
        limit = min(MAX_MESSAGE_SIZE, self.transport.max_payload)
        if len(data) > limit:
            if message.type is MessageType.RESPONSE and message.recipient:
                too_large = error(
                    self.uid, self.name, message.recipient, message.request_id,
                    "too_large", f"response is {len(data)} bytes; limit {limit}",
                )
                await self._send(too_large, to)
            else:
                log.error("%s: %s message too large (%d > %d bytes), not sent",
                          self.uid, message.type.value, len(data), limit)
            return
        if to is None:
            await self.transport.broadcast(data)
        else:
            await self.transport.send(to, data)
        _log_wire("SEND", signed, len(data))

    def _on_receive(self, data: bytes, address: Address) -> None:
        try:
            message = decode(data)
        except DecodeError as exc:
            wire.info("DROP  %d bytes: %s", len(data), exc)
            return
        if message.sender == self.uid:
            return  # our own message, looped back by the transport
        if not self.signer.verify(message):
            _log_wire("DROP", message, len(data), "bad signature")
            return
        if message.msg_id in self._seen:
            wire.debug("DROP  duplicate msg %s", message.msg_id[:8])
            return
        self._seen[message.msg_id] = None
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)

        addressed = message.recipient in (None, self.uid)
        _log_wire("RECV", message, len(data), None if addressed else "overheard, not for me")

        if message.type is MessageType.ADVERT:
            self._on_advert(message, address)
        elif not addressed:
            return
        elif message.type is MessageType.REQUEST:
            self._spawn(self._handle_request(message, address))
        elif message.type is MessageType.RESPONSE:
            self.tracker.resolve(message.request_id, dict(message.payload.get("output") or {}))
        elif message.type is MessageType.ERROR:
            code = str(message.payload.get("code", "error"))
            detail = str(message.payload.get("detail", ""))
            self.tracker.fail(message.request_id, RequestError(code, detail))

    # ------------------------------------------------------------ advertise

    async def _advertise_forever(self) -> None:
        while True:
            await self._send(advert(self.uid, self.name, self.description, self.capabilities))
            for peer in self.directory.prune():
                log.info("%s lost %s (no advert for %.0fs)", self.uid, peer.uid, self.directory.ttl)
            await asyncio.sleep(self.advert_interval)

    def _on_advert(self, message: Message, address: Address) -> None:
        payload = message.payload
        new = self.directory.update(
            uid=message.sender,
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            capabilities=[str(c) for c in payload.get("capabilities", [])],
            address=address,
        )
        if new:
            log.info("%s heard advert from %s: %s %s", self.uid, message.sender,
                     payload.get("description", ""), payload.get("capabilities", []))
        self.peer_heard.set()

    # ------------------------------------------------------------ requester

    async def ask(
        self, peer: Peer, capability: str, input: dict, timeout: float | None = None
    ) -> dict:
        """Send a request to one peer and wait for its output.

        Retries with the same ``request_id``; the responder answers repeats
        from cache, so a retry never produces a second, different answer.
        """
        timeout = timeout or self.request_timeout
        request_id = uuid.uuid4().hex
        future = self.tracker.open(request_id)
        try:
            for _ in range(self.max_attempts):
                await self._send(
                    request(self.uid, self.name, peer.uid, request_id, capability, input),
                    to=peer.address,
                )
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future), timeout / self.max_attempts
                    )
                except asyncio.TimeoutError:
                    continue
            raise TimeoutError(f"no response from {peer.uid}")
        finally:
            self.tracker.close(request_id)

    # ------------------------------------------------------------ responder

    async def _handle_request(self, message: Message, address: Address) -> None:
        key = (message.sender, message.request_id)
        cached = self.tracker.cached(key)
        if cached is not None:  # retried request: resend the same answer
            await self._send(cached, to=address)
            return
        if not self.tracker.begin(key):
            return  # still answering the first copy
        reply = None
        try:
            reply = await self._answer(message)
        finally:
            self.tracker.finish(key, reply)
        await self._send(reply, to=address)

    async def _answer(self, message: Message) -> Message:
        payload = message.payload
        capability = str(payload.get("capability", ""))
        rid = message.request_id

        def fail(code: str, detail: str) -> Message:
            return error(self.uid, self.name, message.sender, rid, code, detail)

        skill = self._handlers.get(capability)
        if skill is None:
            return fail("no_such_capability", f"{self.uid} has no capability {capability!r}")
        try:
            output = await skill.handle(dict(payload.get("input") or {}), self.context)
        except Exception as exc:
            log.error("%s: skill %r failed", self.uid, capability, exc_info=exc)
            return fail("skill_failed", f"{type(exc).__name__}: {exc}")
        if not isinstance(output, dict):
            return fail("skill_failed", f"skill returned {type(output).__name__}, not a dict")
        return response(self.uid, self.name, message.sender, rid, output)


def _log_wire(direction: str, message: Message, size: int, note: str | None = None) -> None:
    """``SEND  advert   weather-1 -> *  [msg 1a2b3c4d, 210 B] {...payload...}``"""
    wire.info(
        "%-4s  %-8s %s -> %s  [msg %s, %d B]%s %s",
        direction,
        message.type.value,
        message.sender,
        message.recipient or "*",
        message.msg_id[:8],
        size,
        f" ({note})" if note else "",
        json.dumps(message.payload, ensure_ascii=False),
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
