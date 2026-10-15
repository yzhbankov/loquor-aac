"""QUERY / OFFER (AMP §4, types 5 and 6): "who offers capability X?"

    A ──QUERY {capability, nonce}──▶ *           broadcast
    B ──OFFER {capability, nonce}──▶ A           unicast, from every node that has it

Only handshaken peers are heard (the node drops the rest), and only
``visible`` and ``quiet`` nodes answer. The beacon's Bloom filter is a hint;
this is the authoritative, on-demand check. Bodies are plaintext: they name
a capability, nothing secret.
"""

from __future__ import annotations

import asyncio
import os

import cbor2

from loquor.protocol.discovery import Peer
from loquor.protocol.envelope import Envelope, Type
from loquor.protocol.node import Node
from loquor.protocol.transport import Address


class QueryExtension:
    def __init__(self, node: Node):
        self.node = node
        self._offers: dict[bytes, list[Peer]] = {}  # nonce -> who offered
        node.register(Type.QUERY, self._on_query)
        node.register(Type.OFFER, self._on_offer)

    async def query(self, capability: str, timeout: float = 1.0) -> list[Peer]:
        """Broadcast a QUERY and collect OFFERs for ``timeout`` seconds.

        Returns peers that offered, plus handshaken peers already known to
        have the capability (no need to ask what we already know).
        """
        nonce = os.urandom(8)
        self._offers[nonce] = []
        try:
            await self.node.send_type(Type.QUERY, cbor2.dumps({"capability": capability, "nonce": nonce}))
            await asyncio.sleep(timeout)
            offered = self._offers[nonce]
        finally:
            self._offers.pop(nonce, None)
        known = self.node.peers.find(capability)
        seen = {p.agent_id for p in offered}
        return offered + [p for p in known if p.agent_id not in seen]

    async def _on_query(self, env: Envelope, peer: Peer, address: Address) -> None:
        if not self.node.visibility.answers_queries:
            return
        try:
            body = cbor2.loads(env.body)
            capability, nonce = str(body["capability"]), bytes(body["nonce"])
        except Exception:
            return
        if capability in self.node.card.capabilities:
            await self.node.send_type(Type.OFFER, cbor2.dumps({"capability": capability, "nonce": nonce}), peer)

    async def _on_offer(self, env: Envelope, peer: Peer, address: Address) -> None:
        try:
            nonce = bytes(cbor2.loads(env.body)["nonce"])
        except Exception:
            return
        offers = self._offers.get(nonce)
        if offers is not None and all(p.agent_id != peer.agent_id for p in offers):
            offers.append(peer)
