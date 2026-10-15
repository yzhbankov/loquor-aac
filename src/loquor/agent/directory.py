"""Who has been heard: adverts in, peers out, forgotten when they go quiet."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loquor.transports.base import Address


@dataclass
class Peer:
    uid: str
    name: str
    description: str
    capabilities: list[str]
    address: Address  # where its last advert came from; handed back to the transport
    last_seen: float = field(default_factory=time.monotonic)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


class PeerDirectory:
    def __init__(self, ttl: float):
        self.ttl = ttl  # seconds without an advert before a peer is dropped
        self._peers: dict[str, Peer] = {}

    def update(
        self, uid: str, name: str, description: str, capabilities: list[str], address: Address
    ) -> bool:
        """Add or refresh a peer. Returns True if it was not known before."""
        new = uid not in self._peers
        self._peers[uid] = Peer(uid, name, description, capabilities, address)
        return new

    def get(self, uid: str) -> Peer | None:
        return self._peers.get(uid)

    def find(self, capability: str | None = None) -> list[Peer]:
        peers = list(self._peers.values())
        if capability is None:
            return peers
        return [p for p in peers if p.has(capability)]

    def prune(self) -> list[Peer]:
        """Drop peers not heard within ``ttl``; returns them."""
        cutoff = time.monotonic() - self.ttl
        gone = [p for p in self._peers.values() if p.last_seen < cutoff]
        for peer in gone:
            del self._peers[peer.uid]
        return gone

    def __contains__(self, uid: object) -> bool:
        return uid in self._peers

    def __len__(self) -> int:
        return len(self._peers)
