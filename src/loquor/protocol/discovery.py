"""Discovery (AMP §3): beacons, the capability Bloom filter, visibility, peers.

A **beacon** is 16 bytes, small enough for a BLE advertisement::

    "AM" | ver (1) | agent_id[:8] | bloom (4) | seq (1)

It says "someone with this id prefix, who may offer these capabilities, is
here". It is not signed and carries no name; the full, signed capability
document arrives with HELLO. ``seq`` changes when the capabilities change,
so a peer knows to ask for a fresh HELLO.

The **Bloom filter** is 32 bits with k = 2: bits ``SHA-256(cap)[0] % 32`` and
``SHA-256(cap)[1] % 32``. At 4 capabilities the false-positive rate is about
5 %, at 8 about 15 %: a hit means "ask", a miss means "don't bother".

**Visibility** is per agent: ``visible`` sends beacons and answers queries;
``quiet`` answers queries (and HELLO) but never beacons; ``silent`` only
initiates — it ignores queries and unsolicited HELLOs.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loquor.protocol.identity import AGENT_ID_SIZE
from loquor.protocol.trust import Trust

BEACON_MAGIC = b"AM"
BEACON_VERSION = 1
BEACON_SIZE = 16
PREFIX_SIZE = 8
BLOOM_SIZE = 4
BLOOM_BITS = BLOOM_SIZE * 8
BLOOM_K = 2


# ------------------------------------------------------------------ bloom


def _bloom_bits(capability: str) -> list[int]:
    digest = hashlib.sha256(capability.encode()).digest()
    return [digest[i] % BLOOM_BITS for i in range(BLOOM_K)]


def bloom(capabilities: list[str]) -> bytes:
    value = 0
    for capability in capabilities:
        for bit in _bloom_bits(capability):
            value |= 1 << bit
    return value.to_bytes(BLOOM_SIZE, "big")


def bloom_may_contain(filter_: bytes, capability: str) -> bool:
    value = int.from_bytes(filter_, "big")
    return all(value & (1 << bit) for bit in _bloom_bits(capability))


# ----------------------------------------------------------------- beacon


@dataclass
class Beacon:
    prefix: bytes  # agent_id[:8]
    bloom: bytes  # 4 bytes
    seq: int  # 0-255, bumps on capability change
    ver: int = BEACON_VERSION

    def encode(self) -> bytes:
        data = BEACON_MAGIC + bytes([self.ver]) + self.prefix + self.bloom + bytes([self.seq & 0xFF])
        assert len(data) == BEACON_SIZE
        return data

    @classmethod
    def decode(cls, data: bytes) -> Beacon:
        if not is_beacon(data):
            raise ValueError("not a beacon")
        ver = data[2]
        if ver != BEACON_VERSION:
            raise ValueError(f"unsupported beacon version {ver}")
        return cls(prefix=data[3:11], bloom=data[11:15], seq=data[15], ver=ver)


def is_beacon(data: bytes) -> bool:
    return len(data) == BEACON_SIZE and data[:2] == BEACON_MAGIC


# ------------------------------------------------------------- visibility


class Visibility(str, Enum):
    VISIBLE = "visible"
    QUIET = "quiet"
    SILENT = "silent"

    @property
    def beacons(self) -> bool:
        return self is Visibility.VISIBLE

    @property
    def answers_queries(self) -> bool:
        return self is not Visibility.SILENT

    @property
    def answers_hello(self) -> bool:
        return self is not Visibility.SILENT


# ------------------------------------------------------------------ peers


@dataclass
class Peer:
    agent_id: bytes
    address: Any = None  # where it was last heard; handed back to the transport
    public_key: bytes | None = None  # known after HELLO
    card: dict[str, Any] = field(default_factory=dict)  # capability document (§6)
    bloom: bytes = bytes(BLOOM_SIZE)
    seq: int | None = None  # last beacon seq
    trust: int = Trust.HEARD
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def handshaken(self) -> bool:
        return self.public_key is not None

    @property
    def name(self) -> str:
        return str(self.card.get("name", ""))

    @property
    def description(self) -> str:
        return str(self.card.get("description", ""))

    @property
    def capabilities(self) -> list[str]:
        return [str(s.get("id", "")) for s in self.card.get("skills", [])]

    @property
    def uid(self) -> str:
        return self.agent_id.hex()

    @property
    def label(self) -> str:
        """Readable in logs: ``weather-forecaster-1a2b3c4d``."""
        return f"{slug(self.name) or 'agent'}-{self.agent_id[:4].hex()}"

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def may_have(self, capability: str) -> bool:
        """Beacon-level hint; HELLO is the authoritative answer."""
        return self.has(capability) or bloom_may_contain(self.bloom, capability)

    def touch(self, address: Any = None) -> None:
        self.last_seen = time.monotonic()
        if address is not None:
            self.address = address


class PeerDirectory:
    def __init__(self, ttl: float):
        self.ttl = ttl  # seconds without hearing a peer before it is dropped
        self._peers: dict[bytes, Peer] = {}

    def get(self, agent_id: bytes) -> Peer | None:
        return self._peers.get(agent_id)

    def by_prefix(self, prefix: bytes) -> Peer | None:
        for peer in self._peers.values():
            if peer.agent_id.startswith(prefix):
                return peer
        return None

    def heard_beacon(self, beacon: Beacon, address: Any) -> tuple[Peer | None, bool]:
        """Refresh the peer with this prefix. Returns (peer or None, seq changed)."""
        peer = self.by_prefix(beacon.prefix)
        if peer is None:
            return None, False
        changed = peer.seq is not None and peer.seq != beacon.seq
        peer.bloom, peer.seq = beacon.bloom, beacon.seq
        peer.touch(address)
        return peer, changed

    def heard_hello(self, agent_id: bytes, public_key: bytes, card: dict, address: Any) -> tuple[Peer, bool]:
        """Add or refresh a peer from its HELLO. Returns (peer, is new)."""
        peer = self._peers.get(agent_id)
        new = peer is None
        if peer is None:
            peer = Peer(agent_id)
            self._peers[agent_id] = peer
        peer.public_key, peer.card = public_key, card
        peer.touch(address)
        return peer, new

    def find(self, capability: str | None = None, *, handshaken: bool = True) -> list[Peer]:
        peers = [p for p in self._peers.values() if p.handshaken or not handshaken]
        if capability is None:
            return peers
        return [p for p in peers if p.has(capability)]

    def prune(self) -> list[Peer]:
        cutoff = time.monotonic() - self.ttl
        gone = [p for p in self._peers.values() if p.last_seen < cutoff]
        for peer in gone:
            del self._peers[peer.agent_id]
        return gone

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self._peers

    def __len__(self) -> int:
        return len(self._peers)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def prefix_of(agent_id: bytes) -> bytes:
    assert len(agent_id) == AGENT_ID_SIZE
    return agent_id[:PREFIX_SIZE]
