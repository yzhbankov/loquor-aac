"""The Agent: capabilities, peers, and the collaboration protocol.

An :class:`Agent` ties together the independent layers — discovery,
transport, identity, and the persistent task store — and implements the
deterministic task state machine on both sides of a collaboration:

Requester side (``ask``)::

    peers = await agent.find_peers(capability="maintenance_diagnostics")
    result = await agent.ask(peers[0], "diagnose", data=sensor_observations)

Server side (``add_capability``)::

    agent = Agent("pump-01", pairing_secret=secret)
    agent.add_capability("machine_diagnostics", handle_diagnosis)
    agent.on_peer_discovered(handle_peer)
    await agent.start()

A capability handler receives a :class:`TaskContext` (observations plus any
evidence gathered so far) and returns either a final ``dict`` result or a
:class:`NeedInfo` asking the requester for more measurements — giving the
multi-turn negotiate/collect-evidence/conclude loop of the PoC scenario.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loquor import messages
from loquor.discovery import Discovery, PeerAddress, StaticDiscovery
from loquor.identity import PairingIdentity
from loquor.messages import Envelope, MessageType, TaskState
from loquor.store import TaskStore
from loquor.transports import HttpTransport, PeerUnreachable, Transport

log = logging.getLogger("loquor")


@dataclass(frozen=True)
class PeerInfo:
    """A discovered, reachable peer."""

    agent_id: str
    address: PeerAddress

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.address.capabilities


@dataclass
class TaskContext:
    """Everything a capability handler knows about a task so far."""

    task_id: str
    capability: str
    requester: str
    observations: dict[str, Any]
    measurements: dict[str, Any] = field(default_factory=dict)


@dataclass
class NeedInfo:
    """Handler verdict: evidence is missing; ask the requester for it."""

    needed: list[str]
    reason: str


CapabilityHandler = Callable[[TaskContext], Awaitable[dict[str, Any] | NeedInfo]]
InfoProvider = Callable[[str, list[str], str], Awaitable[dict[str, Any]]]
PeerDiscoveredCallback = Callable[[PeerInfo], Awaitable[None]]


class Agent:
    def __init__(
        self,
        agent_id: str,
        *,
        pairing_secret: str,
        transport: Transport | None = None,
        discovery: Discovery | None = None,
        store_path: str = ":memory:",
        max_info_rounds: int = 5,
    ):
        self.agent_id = agent_id
        self.identity = PairingIdentity(agent_id, pairing_secret)
        self.transport = transport or HttpTransport()
        self.discovery = discovery or StaticDiscovery()
        self.store = TaskStore(store_path)
        self.port: int | None = None
        self.max_info_rounds = max_info_rounds

        self._capabilities: dict[str, CapabilityHandler] = {}
        self._peers: dict[str, PeerInfo] = {}
        self._peer_event = asyncio.Event()
        self._peer_callbacks: list[PeerDiscoveredCallback] = []
        # Default provider used to answer a peer's info_request when `ask`
        # (or a resume) isn't given an explicit one.
        self.info_provider: InfoProvider | None = None

    # ---------------------------------------------------------------- setup

    def add_capability(self, name: str, handler: CapabilityHandler) -> None:
        self._capabilities[name] = handler

    def on_peer_discovered(self, callback: PeerDiscoveredCallback) -> None:
        self._peer_callbacks.append(callback)

    async def start(self) -> None:
        self.port = await self.transport.start(self._handle_envelope)
        if isinstance(self.transport, HttpTransport):
            self.transport.set_capabilities_doc(
                {"agent_id": self.agent_id, "capabilities": sorted(self._capabilities)}
            )
        self.discovery.set_callbacks(self._peer_found, self._peer_lost)
        await self.discovery.start(self.agent_id, self.port, sorted(self._capabilities))
        log.info("%s listening on port %s", self.agent_id, self.port)

    async def stop(self) -> None:
        await self.discovery.stop()
        await self.transport.stop()
        self.store.close()

    # ---------------------------------------------------------------- peers

    async def _peer_found(self, address: PeerAddress) -> None:
        if address.agent_id == self.agent_id:
            return
        peer = PeerInfo(agent_id=address.agent_id, address=address)
        is_new = address.agent_id not in self._peers
        self._peers[address.agent_id] = peer
        self._peer_event.set()
        if is_new:
            log.info(
                "%s discovered peer %s (capabilities: %s)",
                self.agent_id,
                peer.agent_id,
                ", ".join(peer.capabilities) or "none advertised",
            )
            for callback in self._peer_callbacks:
                await callback(peer)

    async def _peer_lost(self, address: PeerAddress) -> None:
        if self._peers.pop(address.agent_id, None):
            log.info("%s lost peer %s", self.agent_id, address.agent_id)

    @property
    def peers(self) -> list[PeerInfo]:
        return list(self._peers.values())

    async def find_peers(
        self, capability: str | None = None, timeout: float = 10.0
    ) -> list[PeerInfo]:
        """Peers advertising ``capability``, waiting up to ``timeout`` for one."""

        def matching() -> list[PeerInfo]:
            return [
                p
                for p in self._peers.values()
                if capability is None or capability in p.capabilities
            ]

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            found = matching()
            if found:
                return found
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return []
            self._peer_event.clear()
            try:
                await asyncio.wait_for(self._peer_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return []

    # ------------------------------------------------------- requester side

    async def ask(
        self,
        peer: PeerInfo,
        capability: str,
        data: dict[str, Any],
        *,
        on_info_request: InfoProvider | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a task on a peer, answering its evidence requests, until done.

        Task state is persisted at every step; if the peer disconnects the
        task is marked ``interrupted`` and :meth:`resume_pending` can finish
        it after reconnection using the same ``task_id`` (the peer answers
        idempotently, so no duplicate results are produced).
        """
        provider = on_info_request or self.info_provider
        task_id = task_id or messages.new_task_id()
        self.store.upsert(
            task_id,
            role="requester",
            state=TaskState.IN_PROGRESS,
            peer_id=peer.agent_id,
            capability=capability,
            data={"observations": data},
        )

        outbound = messages.task_request(self.agent_id, capability, data, task_id=task_id)
        try:
            for _ in range(self.max_info_rounds + 1):
                reply = await self._send(peer, outbound)

                if reply.type is MessageType.TASK_RESULT:
                    result = reply.payload.get("result", {})
                    self.store.upsert(
                        task_id,
                        role="requester",
                        state=TaskState.COMPLETED,
                        data={"result": result},
                    )
                    return result

                if reply.type is MessageType.INFO_REQUEST:
                    needed = list(reply.payload.get("needed", []))
                    reason = str(reply.payload.get("reason", ""))
                    log.info(
                        "%s: peer %s needs more evidence: %s (%s)",
                        self.agent_id,
                        peer.agent_id,
                        ", ".join(needed),
                        reason,
                    )
                    if provider is None:
                        raise RuntimeError(
                            f"peer requested {needed} but no info provider is set"
                        )
                    self.store.upsert(
                        task_id, role="requester", state=TaskState.AWAITING_INFO
                    )
                    measurements = await provider(task_id, needed, reason)
                    self.store.upsert(
                        task_id,
                        role="requester",
                        state=TaskState.IN_PROGRESS,
                        data={"measurements": measurements},
                    )
                    outbound = messages.info_response(self.agent_id, task_id, measurements)
                    continue

                if reply.type is MessageType.ERROR:
                    self.store.upsert(task_id, role="requester", state=TaskState.FAILED)
                    raise RuntimeError(f"peer error: {reply.payload}")

                raise RuntimeError(f"unexpected reply type {reply.type}")

            self.store.upsert(task_id, role="requester", state=TaskState.FAILED)
            raise RuntimeError("too many info rounds without a result")
        except PeerUnreachable:
            self.store.upsert(task_id, role="requester", state=TaskState.INTERRUPTED)
            log.warning(
                "%s: peer %s unreachable, task %s saved for resume",
                self.agent_id,
                peer.agent_id,
                task_id,
            )
            raise

    async def resume_pending(
        self, peer: PeerInfo, *, on_info_request: InfoProvider | None = None
    ) -> list[dict[str, Any]]:
        """Finish interrupted tasks with ``peer`` after reconnection."""
        results = []
        for task in self.store.by_state(
            "requester", TaskState.INTERRUPTED, TaskState.AWAITING_INFO
        ):
            if task["peer_id"] != peer.agent_id:
                continue
            log.info("%s resuming task %s with %s", self.agent_id, task["task_id"], peer.agent_id)
            results.append(
                await self.ask(
                    peer,
                    task["capability"],
                    task["data"].get("observations", {}),
                    on_info_request=on_info_request,
                    task_id=task["task_id"],
                )
            )
        return results

    async def _send(self, peer: PeerInfo, envelope: Envelope) -> Envelope:
        reply = await self.transport.send(peer.address, self.identity.sign(envelope))
        if not self.identity.verify(reply):
            raise RuntimeError(f"reply from {peer.agent_id} failed signature verification")
        return reply

    # ---------------------------------------------------------- server side

    async def _handle_envelope(self, envelope: Envelope) -> Envelope:
        """Inbound message entry point: authenticate, then run the task FSM."""
        if not self.identity.verify(envelope):
            log.warning("%s rejected unauthenticated message from %s", self.agent_id, envelope.sender)
            return self.identity.sign(
                messages.error(
                    self.agent_id, envelope.task_id, "unauthenticated",
                    "signature missing or invalid",
                )
            )
        try:
            reply = await self._process(envelope)
        except Exception as exc:  # never leak an unhandled error as a hang
            log.exception("%s failed processing %s", self.agent_id, envelope.task_id)
            self.store.upsert(envelope.task_id, role="server", state=TaskState.FAILED)
            reply = messages.error(
                self.agent_id, envelope.task_id, "processing_failed", str(exc)
            )
        return self.identity.sign(reply)

    async def _process(self, envelope: Envelope) -> Envelope:
        task_id = envelope.task_id
        stored = self.store.get(task_id)

        # Idempotency: a completed task is answered from the store, so a
        # retried request after a disconnect never produces duplicate work.
        if stored and stored["state"] is TaskState.COMPLETED and stored["role"] == "server":
            return messages.task_result(self.agent_id, task_id, stored["data"]["result"])

        if envelope.type is MessageType.TASK_REQUEST:
            capability = envelope.capability or ""
            if capability not in self._capabilities:
                return messages.error(
                    self.agent_id, task_id, "unknown_capability", capability
                )
            self.store.upsert(
                task_id,
                role="server",
                state=TaskState.IN_PROGRESS,
                peer_id=envelope.sender,
                capability=capability,
                data={"observations": envelope.payload.get("observations", {})},
            )
        elif envelope.type is MessageType.INFO_RESPONSE:
            if stored is None:
                return messages.error(self.agent_id, task_id, "unknown_task", task_id)
            measurements = dict(stored["data"].get("measurements", {}))
            measurements.update(envelope.payload.get("measurements", {}))
            self.store.upsert(
                task_id,
                role="server",
                state=TaskState.IN_PROGRESS,
                data={"measurements": measurements},
            )
        else:
            return messages.error(
                self.agent_id, task_id, "unexpected_message", envelope.type.value
            )

        task = self.store.get(task_id)
        assert task is not None
        context = TaskContext(
            task_id=task_id,
            capability=task["capability"],
            requester=task["peer_id"],
            observations=task["data"].get("observations", {}),
            measurements=task["data"].get("measurements", {}),
        )
        verdict = await self._capabilities[task["capability"]](context)

        if isinstance(verdict, NeedInfo):
            self.store.upsert(task_id, role="server", state=TaskState.AWAITING_INFO)
            return messages.info_request(self.agent_id, task_id, verdict.needed, verdict.reason)

        self.store.upsert(
            task_id, role="server", state=TaskState.COMPLETED, data={"result": verdict}
        )
        return messages.task_result(self.agent_id, task_id, verdict)
