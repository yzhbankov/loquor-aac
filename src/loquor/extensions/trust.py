"""Trust levels (AMP §9): how a peer rises above "heard".

- :class:`TrustStore` — interface: the level this store vouches for.
- :class:`PinnedKeys` — level 1 (*verified*): public keys confirmed out of
  band (QR code, read-out fingerprint), persisted as JSON.
- :class:`ProximityProvider` — interface for level 2 (*proximate*): a UWB
  ranging stub; nothing implements it yet.
- :class:`TrustExtension` — sets ``peer.trust`` whenever a peer handshakes.

The protocol only carries the number; the agent enforces a skill's
``min_trust`` at dispatch.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from loquor.protocol.discovery import Peer
from loquor.protocol.node import Node
from loquor.protocol.trust import Trust


class TrustStore:
    def level(self, agent_id: bytes, public_key: bytes) -> int:
        return Trust.HEARD


class PinnedKeys(TrustStore):
    """``{agent_id_hex: public_key_hex}`` on disk; a match is level 1."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else None
        self._pins: dict[bytes, bytes] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self._pins = {bytes.fromhex(k): bytes.fromhex(v) for k, v in data.items()}

    def pin(self, agent_id: bytes, public_key: bytes) -> None:
        self._pins[agent_id] = public_key
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({k.hex(): v.hex() for k, v in self._pins.items()}, indent=2))

    def level(self, agent_id: bytes, public_key: bytes) -> int:
        return Trust.VERIFIED if self._pins.get(agent_id) == public_key else Trust.HEARD

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self._pins


class ProximityProvider:
    """Level 2 comes from distance bounding (UWB). Stub: nobody is proximate."""

    def is_proximate(self, agent_id: bytes) -> bool:
        return False


class TrustExtension:
    def __init__(self, node: Node, stores: Iterable[TrustStore] = (), proximity: ProximityProvider | None = None):
        self.node = node
        self.stores = list(stores)
        self.proximity = proximity
        node.on_peer(self._on_peer)

    def level_of(self, peer: Peer) -> int:
        if peer.public_key is None:
            return Trust.HEARD
        level = max((s.level(peer.agent_id, peer.public_key) for s in self.stores), default=Trust.HEARD)
        if self.proximity is not None and self.proximity.is_proximate(peer.agent_id):
            level = max(level, Trust.PROXIMATE)
        return int(level)

    async def _on_peer(self, peer: Peer) -> None:
        peer.trust = self.level_of(peer)
