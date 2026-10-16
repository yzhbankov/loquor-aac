"""The orchestrator: a protocol node in, skills out, nothing else.

An :class:`Agent` is generic plumbing configured by a profile. It:

1. builds its capability document from its skills and runs a
   :class:`~loquor.protocol.node.Node` with it (beacons, handshakes,
   encryption are the node's business);
2. dispatches a request body to the skill with that capability — after
   checking the requester's trust level — and sends back its output (or an
   error), idempotently;
3. offers skills ``ask()``: send a request to a peer, retry, return the
   output.

Business logic — what to answer, whom to ask, when — lives in skills.

Request bodies travel inside encrypted MSG envelopes::

    {"kind": "request",  "request_id": str, "capability": str, "input": {...}}
    {"kind": "response", "request_id": str, "output": {...}}
    {"kind": "error",    "request_id": str, "code": str, "detail": str}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Iterable
from typing import Any

from loquor.agent.context import Context
from loquor.agent.sensors import SensorHub
from loquor.agent.skill import Skill
from loquor.agent.tracker import RequestError, RequestTracker
from loquor.agent.triggers import TriggerHub
from loquor.extensions.query import QueryExtension
from loquor.extensions.relay import RelayExtension
from loquor.extensions.session import SessionExtension
from loquor.extensions.trust import PinnedKeys, TrustExtension
from loquor.protocol.card import AgentCard, skill_entry
from loquor.protocol.discovery import Peer, Visibility
from loquor.protocol.identity import Identity
from loquor.protocol.node import Node
from loquor.protocol.transport import Transport
from loquor.reasoning.base import Reasoner
from loquor.sensors.base import Sensor, describe_readings
from loquor.triggers.base import Trigger

log = logging.getLogger("loquor")
wire = logging.getLogger("loquor.wire")

Body = dict[str, Any]


class Agent:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        skills: Iterable[Skill],
        reasoner: Reasoner,
        transport: Transport,
        identity: Identity | None = None,
        visibility: Visibility = Visibility.VISIBLE,
        beacon_interval: float = 10.0,
        request_timeout: float = 30.0,
        max_attempts: int = 3,
        pinned_keys: str | None = None,  # path of the level-1 trust store (§9)
        relay: bool = False,  # store and forward for peers out of reach (§8)
        sensors: Iterable[Sensor] = (),
        triggers: Iterable[Trigger] = (),
    ):
        self.name = name
        self.description = description
        self.skills = list(skills)
        self.reasoner = reasoner
        self.identity = identity or Identity.generate()
        self.request_timeout = request_timeout
        self.max_attempts = max_attempts

        self._handlers: dict[str, Skill] = {}
        for skill in self.skills:
            if skill.capability:
                if skill.capability in self._handlers:
                    raise ValueError(f"two skills provide capability {skill.capability!r}")
                self._handlers[skill.capability] = skill

        self.sensors = SensorHub(sensors)
        self.triggers = TriggerHub(triggers, reasoner)
        for skill in self.skills:  # a skill naming something the agent lacks fails now, not at runtime
            for kind, hub in (("sensor", self.sensors), ("trigger", self.triggers)):
                missing = [n for n in getattr(skill, f"{kind}s", ()) if n not in hub]
                if missing:
                    raise ValueError(
                        f"skill {type(skill).__name__} uses {kind}s {missing} the agent does not have "
                        f"(have: {hub.names() or 'none'})"
                    )

        self.node = Node(self.identity, transport, self.card(), visibility=visibility,
                         beacon_interval=beacon_interval)
        self.node.on_message(self._on_body)
        self.query = QueryExtension(self.node)
        self.sessions = SessionExtension(self.node)
        self.trust = TrustExtension(self.node, stores=[PinnedKeys(pinned_keys)] if pinned_keys else [])
        self.relay = RelayExtension(self.node) if relay else None
        self.tracker = RequestTracker()
        self.context = Context(self)
        self._tasks: set[asyncio.Task] = set()

    def card(self) -> AgentCard:
        """The capability document (AMP §6) built from this agent's parts."""
        llm = {"model": getattr(self.reasoner, "model", None)} if getattr(self.reasoner, "model", None) else None
        return AgentCard(
            name=self.name,
            description=self.description,
            skills=[
                skill_entry(s.capability, description=s.description, input_schema=s.input_schema,
                            output_schema=s.output_schema, min_trust=s.min_trust, extra=s.advertise(self))
                for s in self._handlers.values()
            ],
            llm=llm,
        )

    @property
    def uid(self) -> str:
        return self.node.label

    @property
    def agent_id(self) -> bytes:
        return self.identity.agent_id

    @property
    def capabilities(self) -> list[str]:
        return list(self._handlers)

    @property
    def transport(self) -> Transport:
        return self.node.transport

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.node.start()
        self.sensors.start(self._spawn)
        for skill in self.skills:
            self._spawn(skill.run(self.context))

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.node.stop()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("%s: background task failed", self.uid, exc_info=task.exception())

    # ------------------------------------------------------------ requester

    async def ask(self, peer: Peer, capability: str, input: Body, timeout: float | None = None) -> Body:
        """Send a request to one peer and wait for its output.

        Retries with the same ``request_id``; the responder answers repeats
        from cache, so a retry never produces a second, different answer.
        """
        timeout = timeout or self.request_timeout
        request_id = uuid.uuid4().hex
        body = {"kind": "request", "request_id": request_id, "capability": capability, "input": input}
        future = self.tracker.open(request_id)
        log.info("%s: asking %s for %s: %s", self.uid, peer.label, capability, _brief(input))
        try:
            for attempt in range(self.max_attempts):
                await self._send(peer, body)
                try:
                    output = await asyncio.wait_for(asyncio.shield(future), timeout / self.max_attempts)
                except asyncio.TimeoutError:
                    if attempt + 1 < self.max_attempts:
                        log.info("%s: no answer from %s yet; asking again", self.uid, peer.label)
                    continue
                log.info("%s: %s answered: %s", self.uid, peer.label, _brief(output))
                return output
            raise TimeoutError(f"no response from {peer.label}")
        finally:
            self.tracker.close(request_id)

    async def _send(self, peer: Peer, body: Body) -> None:
        wire.info("BODY  %-8s -> %s %s", body.get("kind"), peer.label, _summary(body))
        await self.node.send(peer, body)

    # ------------------------------------------------------------ responder

    async def _on_body(self, peer: Peer, body: Body) -> None:
        kind = body.get("kind")
        request_id = str(body.get("request_id", ""))
        wire.info("BODY  %-8s <- %s %s", kind, peer.label, _summary(body))
        if kind == "request":
            self._spawn(self._handle_request(peer, body))
        elif kind == "response":
            self.tracker.resolve(request_id, dict(body.get("output") or {}))
        elif kind == "error":
            self.tracker.fail(request_id, RequestError(str(body.get("code", "error")), str(body.get("detail", ""))))
        else:
            log.debug("%s: ignoring body of kind %r from %s", self.uid, kind, peer.label)

    async def _handle_request(self, peer: Peer, body: Body) -> None:
        key = (peer.agent_id, str(body.get("request_id", "")))
        cached = self.tracker.cached(key)
        if cached is not None:  # retried request: resend the same answer
            await self._send(peer, cached)
            return
        if not self.tracker.begin(key):
            return  # still answering the first copy
        reply = None
        try:
            reply = await self._answer(peer, body)
        finally:
            self.tracker.finish(key, reply)
        try:
            await self._send(peer, reply)
        except ValueError as exc:  # too large for the envelope or transport
            await self._send(peer, _error(key[1], "too_large", str(exc)))

    async def _answer(self, peer: Peer, body: Body) -> Body:
        capability = str(body.get("capability", ""))
        request_id = str(body.get("request_id", ""))
        skill = self._handlers.get(capability)
        input = dict(body.get("input") or {})
        log.info("%s: %s asks for %s: %s", self.uid, peer.label, capability or "?", _brief(input))
        if skill is None:
            log.info("%s: refused %s: no capability %r", self.uid, peer.label, capability)
            return _error(request_id, "no_such_capability", f"{self.uid} has no capability {capability!r}")
        if peer.trust < skill.min_trust:
            log.info("%s: refused %s: %s needs trust %d, peer has %d",
                     self.uid, peer.label, capability, skill.min_trust, peer.trust)
            return _error(request_id, "insufficient_trust",
                          f"{capability!r} needs trust level {skill.min_trust}, you are at {peer.trust}")
        try:
            output = await skill.handle(input, self.context)
        except Exception as exc:
            log.error("%s: skill %r failed", self.uid, capability, exc_info=exc)
            return _error(request_id, "skill_failed", f"{type(exc).__name__}: {exc}")
        if not isinstance(output, dict):
            return _error(request_id, "skill_failed", f"skill returned {type(output).__name__}, not a dict")
        log.info("%s: answered %s: %s", self.uid, peer.label, _brief(output))
        return {"kind": "response", "request_id": request_id, "output": output}


def _error(request_id: str, code: str, detail: str) -> Body:
    return {"kind": "error", "request_id": request_id, "code": code, "detail": detail}


def _summary(body: Body) -> str:
    shown = {k: v for k, v in body.items() if k not in ("kind", "request_id")}
    return json.dumps(shown, ensure_ascii=False, default=str)[:300]


def _brief(data: Body, width: int = 160) -> str:
    """A request's input or a response's output in one short line for the story log."""
    if set(data) == {"question"} or set(data) == {"answer"}:
        text = " ".join(str(next(iter(data.values()))).split())
    elif set(data) == {"readings"} and isinstance(data["readings"], dict):
        text = "readings " + describe_readings(data["readings"])
    elif not data:
        text = "(no input)"
    else:
        text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= width else text[: width - 1] + "…"
