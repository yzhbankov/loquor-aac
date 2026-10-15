"""AMP v1 — the Agent Mesh Protocol core.

- :mod:`identity`  — Ed25519 keypair, ``agent_id`` (§1)
- :mod:`envelope`  — the CBOR envelope, signatures, ttl, dedup (§2)
- :mod:`discovery` — beacons, Bloom filter, visibility, peer directory (§3)
- :mod:`card`      — the capability document carried in HELLO (§6)
- :mod:`crypto`    — X25519 pair key, ChaCha20-Poly1305 bodies (§5)
- :mod:`node`      — ties them together over a :mod:`transport` (§4)
- :mod:`trust`     — trust levels (§9)

Message types 1-4 are implemented here; 5-8 live in :mod:`loquor.extensions`.
"""

from loquor.protocol.card import AgentCard, skill_entry
from loquor.protocol.discovery import Beacon, Peer, PeerDirectory, Visibility, bloom, bloom_may_contain
from loquor.protocol.envelope import (
    BROADCAST,
    MAX_ENVELOPE_SIZE,
    VERSION,
    DecodeError,
    Dedup,
    Envelope,
    Type,
    type_name,
)
from loquor.protocol.identity import Identity, agent_id_of
from loquor.protocol.node import Node
from loquor.protocol.transport import Address, OnReceive, Transport
from loquor.protocol.trust import Trust

__all__ = [
    "BROADCAST",
    "MAX_ENVELOPE_SIZE",
    "VERSION",
    "Address",
    "AgentCard",
    "Beacon",
    "DecodeError",
    "Dedup",
    "Envelope",
    "Identity",
    "Node",
    "OnReceive",
    "Peer",
    "PeerDirectory",
    "Transport",
    "Trust",
    "Type",
    "Visibility",
    "agent_id_of",
    "bloom",
    "bloom_may_contain",
    "skill_entry",
    "type_name",
]
