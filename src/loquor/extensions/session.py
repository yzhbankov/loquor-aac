"""Sessions (AMP §7, types 7 and 8): negotiate a bulk/stream link inside envelopes.

    A ──SESSION_REQ {purpose, hint, nonce}──▶ B
    B ──SESSION_ACK {nonce, transport, params}──▶ A

The responder picks the hinted transport if it has it, else any it has,
else answers ``"none"``: keep using core messaging. Core messaging never
depends on a session. Session transports (TCP/QUIC on the same Wi-Fi,
BLE L2CAP) are an interface here; none is implemented yet, so today every
negotiation ends in ``"none"`` unless a test supplies one.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import cbor2

from loquor.protocol.discovery import Peer
from loquor.protocol.envelope import Envelope, Type
from loquor.protocol.node import Node
from loquor.protocol.transport import Address

NONE = "none"


class SessionTransport:
    """A link for bulk data between two peers, set up out of band of the envelope flow."""

    name: str = ""

    async def offer(self, peer: Peer) -> dict[str, Any]:
        """Prepare to accept ``peer``; return the parameters it needs to connect."""
        raise NotImplementedError


@dataclass
class Session:
    peer: Peer
    purpose: str
    transport: str  # a SessionTransport name, or "none"
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def established(self) -> bool:
        return self.transport != NONE


class SessionExtension:
    def __init__(self, node: Node, transports: dict[str, SessionTransport] | None = None):
        self.node = node
        self.transports = dict(transports or {})
        self._waiting: dict[bytes, asyncio.Future[dict[str, Any]]] = {}
        node.register(Type.SESSION_REQ, self._on_request)
        node.register(Type.SESSION_ACK, self._on_ack)

    async def request(self, peer: Peer, purpose: str, hint: str = "wifi", timeout: float = 2.0) -> Session:
        nonce = os.urandom(8)
        future = self._waiting[nonce] = asyncio.get_running_loop().create_future()
        try:
            await self.node.send_type(Type.SESSION_REQ, cbor2.dumps({"purpose": purpose, "hint": hint, "nonce": nonce}), peer)
            answer = await asyncio.wait_for(future, timeout)
        finally:
            self._waiting.pop(nonce, None)
        return Session(peer, purpose, str(answer.get("transport", NONE)), dict(answer.get("params") or {}))

    def choose(self, hint: str) -> str:
        if hint in self.transports:
            return hint
        return next(iter(self.transports), NONE)

    async def _on_request(self, env: Envelope, peer: Peer, address: Address) -> None:
        try:
            body = cbor2.loads(env.body)
            nonce, hint = bytes(body["nonce"]), str(body.get("hint", ""))
        except Exception:
            return
        name = self.choose(hint)
        params = await self.transports[name].offer(peer) if name != NONE else {}
        await self.node.send_type(Type.SESSION_ACK, cbor2.dumps({"nonce": nonce, "transport": name, "params": params}), peer)

    async def _on_ack(self, env: Envelope, peer: Peer, address: Address) -> None:
        try:
            body = cbor2.loads(env.body)
            nonce = bytes(body["nonce"])
        except Exception:
            return
        future = self._waiting.get(nonce)
        if future is not None and not future.done():
            future.set_result(body)
