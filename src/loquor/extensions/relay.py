"""Relay / gossip (AMP §8): store envelopes for peers that are out of reach.

A node whose card says ``relay: true`` keeps envelopes it overhears that are
addressed to someone else (bounded queue, expired by age and ``ttl``) and
hands them over when it meets the addressee. It never decrypts anything:
it reads only the envelope header, decrements ``ttl`` and forwards the
origin's signature untouched. The addressee verifies with the origin's key,
so it must have handshaken with the origin at some point.

Gossip between relays (exchange ``msg_id`` Bloom filters, send the
difference) has its building blocks here — :func:`ids_bloom` and
:meth:`RelayStore.missing` — but no wire message yet; types 16+ are reserved
for it.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

from loquor.protocol.discovery import Peer
from loquor.protocol.envelope import Envelope
from loquor.protocol.node import Node

GOSSIP_BLOOM_BITS = 256
GOSSIP_BLOOM_K = 3


def ids_bloom(msg_ids: list[bytes] | set[bytes], bits: int = GOSSIP_BLOOM_BITS) -> bytes:
    value = 0
    for msg_id in msg_ids:
        for bit in _bits(msg_id, bits):
            value |= 1 << bit
    return value.to_bytes(bits // 8, "big")


def bloom_has(filter_: bytes, msg_id: bytes) -> bool:
    value = int.from_bytes(filter_, "big")
    return all(value & (1 << bit) for bit in _bits(msg_id, len(filter_) * 8))


def _bits(msg_id: bytes, bits: int) -> list[int]:
    digest = hashlib.sha256(msg_id).digest()
    return [int.from_bytes(digest[2 * i : 2 * i + 2], "big") % bits for i in range(GOSSIP_BLOOM_K)]


class RelayStore:
    """Interface. ``put`` keeps an envelope (already ttl-decremented); ``take`` hands over."""

    def put(self, env: Envelope) -> bool:
        raise NotImplementedError

    def take(self, agent_id: bytes) -> list[Envelope]:
        raise NotImplementedError

    def expire(self) -> int:
        raise NotImplementedError

    def ids(self) -> set[bytes]:
        raise NotImplementedError

    def missing(self, their_bloom: bytes) -> list[Envelope]:
        """Envelopes the other relay (by its Bloom filter) does not have."""
        raise NotImplementedError


class MemoryRelayStore(RelayStore):
    def __init__(self, max_items: int = 256, max_age: float = 600.0):
        self.max_items = max_items
        self.max_age = max_age
        self._items: OrderedDict[bytes, tuple[Envelope, float]] = OrderedDict()

    def put(self, env: Envelope) -> bool:
        if env.msg_id in self._items:
            return False
        self._items[env.msg_id] = (env, time.monotonic())
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return True

    def take(self, agent_id: bytes) -> list[Envelope]:
        self.expire()
        out = [env for env, _ in self._items.values() if env.to == agent_id]
        for env in out:
            del self._items[env.msg_id]
        return out

    def expire(self) -> int:
        cutoff = time.monotonic() - self.max_age
        gone = [k for k, (env, at) in self._items.items() if at < cutoff or env.ttl <= 0]
        for k in gone:
            del self._items[k]
        return len(gone)

    def ids(self) -> set[bytes]:
        return set(self._items)

    def missing(self, their_bloom: bytes) -> list[Envelope]:
        return [env for env, _ in self._items.values() if not bloom_has(their_bloom, env.msg_id)]

    def __len__(self) -> int:
        return len(self._items)


class RelayExtension:
    def __init__(self, node: Node, store: RelayStore | None = None):
        self.node = node
        self.store = store or MemoryRelayStore()
        node.card.relay = True
        node.on_overheard(self._on_overheard)
        node.on_peer(self._on_peer)

    def _on_overheard(self, env: Envelope) -> None:
        if env.is_broadcast or env.to == self.node.agent_id:
            return
        hop = env.relayed()  # ttl - 1, or None when the budget is spent
        if hop is not None:
            self.store.put(hop)

    async def _on_peer(self, peer: Peer) -> None:
        for env in self.store.take(peer.agent_id):
            await self.node.transport.send(peer.address, env.encode())
