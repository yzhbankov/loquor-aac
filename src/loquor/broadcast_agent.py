"""A self-describing agent on the broadcast channel.

Every :class:`BroadcastAgent` behaves the same way — only its
:class:`AgentProfile` (and its reasoner) differ:

1. Every ``advert_interval`` seconds it advertises its uid, name,
   description and capabilities to everyone.
2. It listens to all messages. Adverts from others go into its directory.
3. If its profile has a ``goal``, it asks its reasoner — once per newly
   seen agent — whether that agent can help, and if so what to ask it.
   It then sends a request addressed to that agent's uid, waits for the
   response, and reasons over the answer to complete its goal.
4. A request addressed to its own uid is answered by its reasoner, guided
   by the profile's system prompt (its knowledge and behaviour). A
   ``{random_number}`` placeholder in that prompt is replaced by a fresh
   0-100 value on every request.

An agent without a goal only advertises and answers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from loquor.broadcast import Bus, BroadcastMessage, BroadcastType, MulticastBus
from loquor.identity import PairingIdentity
from loquor.reasoning import Reasoner

log = logging.getLogger("loquor")
# Every message sent and received, one line each. Separate logger so it can
# be filtered (e.g. hide adverts) without hiding the agent's reasoning.
wire = logging.getLogger("loquor.wire")

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "useful": {"type": "boolean"},
        "reason": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["useful", "reason", "question"],
}


@dataclass
class AgentProfile:
    """What an agent is: identity, advertised capabilities, and behaviour."""

    name: str
    description: str
    capabilities: list[str]
    system_prompt: str
    goal: str | None = None  # agents without a goal only advertise and answer
    temperature: float = 0.2  # used when answering requests
    model: str | None = None  # local LLM (Ollama model name); None = default

    @classmethod
    def from_file(cls, path: str | Path) -> AgentProfile:
        data = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        missing = sorted(
            f.name for f in fields(cls) if f.default is MISSING and f.name not in data
        )
        if unknown or missing:
            raise ValueError(
                f"invalid profile {path}: "
                + "; ".join(
                    part
                    for part in (
                        unknown and f"unknown fields {unknown}",
                        missing and f"missing fields {missing}",
                    )
                    if part
                )
            )
        return cls(**data)


@dataclass
class PeerAdvert:
    uid: str
    name: str
    description: str
    capabilities: list[str]
    last_seen: float = field(default_factory=time.monotonic)


ResultCallback = Callable[[dict[str, Any]], Awaitable[None]]


class BroadcastAgent:
    def __init__(
        self,
        profile: AgentProfile,
        reasoner: Reasoner,
        *,
        pairing_secret: str,
        bus: Bus | None = None,
        uid: str | None = None,
        advert_interval: float = 10.0,
        request_timeout: float = 30.0,
        max_attempts: int = 3,
    ):
        self.profile = profile
        self.reasoner = reasoner
        self.uid = uid or f"{_slug(profile.name)}-{uuid.uuid4().hex[:8]}"
        self.identity = PairingIdentity(self.uid, pairing_secret)
        self.bus = bus or MulticastBus()
        self.advert_interval = advert_interval
        self.request_timeout = request_timeout
        self.max_attempts = max_attempts

        self.directory: dict[str, PeerAdvert] = {}
        self.result: dict[str, Any] | None = None
        self.done = asyncio.Event()
        self._result_callbacks: list[ResultCallback] = []

        self._seen: OrderedDict[str, None] = OrderedDict()  # dedupe msg_ids
        self._evaluated: set[str] = set()  # peers already considered for the goal
        self._goal_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[str]] = {}  # request_id -> answer
        self._answers: dict[tuple[str, str], BroadcastMessage] = {}
        self._answering: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ lifecycle

    def on_result(self, callback: ResultCallback) -> None:
        self._result_callbacks.append(callback)

    async def start(self) -> None:
        await self.bus.start(self._on_datagram)
        self._spawn(self._advertise_forever())
        log.info("%s started: %s", self.uid, ", ".join(self.profile.capabilities))

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.bus.stop()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("%s: background task failed", self.uid, exc_info=task.exception())

    # --------------------------------------------------------------- wire

    def _send(self, type_: BroadcastType, recipient: str | None, payload: dict) -> None:
        message = BroadcastMessage(
            type=type_,
            sender=self.uid,
            sender_name=self.profile.name,
            recipient=recipient,
            payload=payload,
        )
        self._send_message(message)

    def _send_message(self, message: BroadcastMessage) -> None:
        signed = self.identity.sign(message)
        data = signed.model_dump_json().encode()
        self.bus.send(data)
        _log_wire("SEND", signed, len(data))

    def _on_datagram(self, data: bytes) -> None:
        try:
            message = BroadcastMessage.model_validate_json(data)
        except ValidationError:
            wire.info("DROP  %d bytes: not a loquor message", len(data))
            return
        if message.sender == self.uid:
            return  # our own message, looped back by multicast
        if not self.identity.verify(message):
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

        if message.type is BroadcastType.ADVERT:
            self._on_advert(message)
        elif not addressed:
            return
        elif message.type is BroadcastType.REQUEST:
            self._spawn(self._answer(message))
        elif message.type is BroadcastType.RESPONSE:
            future = self._pending.get(str(message.payload.get("request_id")))
            if future is not None and not future.done():
                future.set_result(str(message.payload.get("answer", "")))

    # ------------------------------------------------------------ advertise

    async def _advertise_forever(self) -> None:
        while True:
            self._send(
                BroadcastType.ADVERT,
                None,
                {
                    "name": self.profile.name,
                    "description": self.profile.description,
                    "capabilities": self.profile.capabilities,
                },
            )
            self._prune_directory()
            await asyncio.sleep(self.advert_interval)

    def _prune_directory(self) -> None:
        cutoff = time.monotonic() - 3 * self.advert_interval
        for uid, peer in list(self.directory.items()):
            if peer.last_seen < cutoff:
                del self.directory[uid]
                log.info("%s lost %s (no advert for %.0fs)", self.uid, uid, 3 * self.advert_interval)

    def _on_advert(self, message: BroadcastMessage) -> None:
        payload = message.payload
        known = message.sender in self.directory
        self.directory[message.sender] = PeerAdvert(
            uid=message.sender,
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            capabilities=[str(c) for c in payload.get("capabilities", [])],
        )
        if not known:
            log.info(
                "%s heard advert from %s: %s %s",
                self.uid,
                message.sender,
                payload.get("description", ""),
                payload.get("capabilities", []),
            )
        if self.profile.goal and not self.done.is_set() and message.sender not in self._evaluated:
            self._evaluated.add(message.sender)
            self._spawn(self._consider(self.directory[message.sender]))

    # ------------------------------------------------------ goal (requester)

    async def _consider(self, peer: PeerAdvert) -> None:
        """Reason about an advertised agent; if useful, ask it and act."""
        async with self._goal_lock:  # one line of reasoning at a time
            if self.done.is_set():
                return
            decision = await self._decide(peer)
            if decision is None:
                self._evaluated.discard(peer.uid)  # undecidable: retry on next advert
                return
            log.info(
                "%s reasoning about %s: useful=%s — %s",
                self.uid, peer.uid, decision["useful"], decision["reason"],
            )
            if not decision["useful"]:
                return

            question = decision["question"].strip() or await self._write_question(peer)
            try:
                answer = await self.request(peer.uid, question)
            except TimeoutError:
                log.warning("%s: %s did not answer; will retry on its next advert", self.uid, peer.uid)
                self._evaluated.discard(peer.uid)
                return

            outcome = await self.reasoner.complete(
                system=self.profile.system_prompt,
                prompt=(
                    f"Your goal: {self.profile.goal}\n"
                    f"You asked {peer.name} ({peer.description}): {question}\n"
                    f"It answered: {answer}\n\n"
                    "Now complete your goal based on this answer."
                ),
            )
            self.result = {
                "goal": self.profile.goal,
                "asked": {"uid": peer.uid, "name": peer.name, "question": question},
                "answer": answer,
                "outcome": outcome.strip(),
                "reasoning": decision["reason"],
            }
            self.done.set()
            for callback in self._result_callbacks:
                await callback(self.result)

    async def _decide(self, peer: PeerAdvert) -> dict[str, Any] | None:
        raw = await self.reasoner.complete(
            system=f"{self.profile.system_prompt}\n\nYour goal: {self.profile.goal}",
            prompt=(
                "Another agent on the local network advertised itself:\n"
                f"- name: {peer.name}\n"
                f"- description: {peer.description}\n"
                f"- capabilities: {', '.join(peer.capabilities)}\n\n"
                "Can this agent give you information you need for your goal? "
                "Reply in JSON with keys:\n"
                '- "useful": true or false\n'
                '- "reason": one sentence explaining your decision\n'
                '- "question": if useful, the short question you will send to this '
                "agent to get the missing information (addressed to it, about what "
                'it provides — not your own goal); otherwise ""'
            ),
            temperature=0,
            schema=DECISION_SCHEMA,
        )
        try:
            decision = json.loads(raw)
            return {
                "useful": bool(decision["useful"]),
                "reason": str(decision.get("reason", "")),
                "question": str(decision.get("question", "")),
            }
        except (ValueError, KeyError, TypeError):
            log.warning("%s: unparseable decision from reasoner: %r", self.uid, raw[:200])
            return None

    async def _write_question(self, peer: PeerAdvert) -> str:
        """Small models often decide well but leave the JSON question empty."""
        question = await self.reasoner.complete(
            system=f"{self.profile.system_prompt}\n\nYour goal: {self.profile.goal}",
            prompt=(
                f"You will ask {peer.name} ({peer.description}) for the information "
                "you are missing. Write only that one short question, nothing else."
            ),
            temperature=0,
        )
        return question.strip().strip('"') or f"Can you help with: {self.profile.goal}"

    async def request(self, recipient: str, question: str) -> str:
        """Send a question to one agent (by uid) and wait for its answer.

        Retries with the same ``request_id``; the responder answers repeats
        from cache, so a retry never produces a second, different answer.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            for _ in range(self.max_attempts):
                self._send(
                    BroadcastType.REQUEST,
                    recipient,
                    {"request_id": request_id, "question": question},
                )
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future), self.request_timeout / self.max_attempts
                    )
                except asyncio.TimeoutError:
                    continue
            raise TimeoutError(f"no response from {recipient}")
        finally:
            self._pending.pop(request_id, None)

    # ----------------------------------------------------- answer (responder)

    @staticmethod
    def _fill_placeholders(prompt: str) -> str:
        """``{random_number}`` in a system prompt becomes a fresh 0-100 value.

        Language models are poor sources of randomness (they repeat their most
        likely answer), so a profile that must vary can take entropy from here.
        """
        return prompt.replace("{random_number}", str(random.randint(0, 100)))

    async def _answer(self, message: BroadcastMessage) -> None:
        key = (message.sender, str(message.payload.get("request_id", message.msg_id)))
        if key in self._answers:  # retried request: resend the same answer
            self._send_message(self._answers[key])
            return
        if key in self._answering:
            return  # still reasoning about the first copy
        self._answering.add(key)
        try:
            question = str(message.payload.get("question", ""))
            answer = await self.reasoner.complete(
                system=self._fill_placeholders(self.profile.system_prompt),
                prompt=f"Agent {message.sender_name} ({message.sender}) asks: {question}",
                temperature=self.profile.temperature,
            )
            reply = BroadcastMessage(
                type=BroadcastType.RESPONSE,
                sender=self.uid,
                sender_name=self.profile.name,
                recipient=message.sender,
                payload={"request_id": key[1], "answer": answer.strip()[:4000]},
            )
            self._answers[key] = reply
            self._send_message(reply)
        finally:
            self._answering.discard(key)


def _log_wire(
    direction: str, message: BroadcastMessage, size: int, note: str | None = None
) -> None:
    """``SEND advert   weather-1 -> *  [msg 1a2b3c4d, 210 B] {...payload...}``"""
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
