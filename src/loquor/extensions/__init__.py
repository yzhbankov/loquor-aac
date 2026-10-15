"""AMP extensions: optional message types 5-8 and the trust/relay interfaces.

Each extension is a class that takes a :class:`~loquor.protocol.node.Node`
and registers handlers for its envelope types. Core messaging (types 1-4)
never depends on any of them.

- :mod:`query`   — QUERY / OFFER: "who offers capability X?" (§4, types 5-6)
- :mod:`session` — SESSION_REQ / SESSION_ACK negotiation (§7, types 7-8)
- :mod:`relay`   — store-and-forward for envelopes addressed to others (§8)
- :mod:`trust`   — stores that raise a peer's trust level (§9)
"""

from loquor.extensions.query import QueryExtension
from loquor.extensions.relay import MemoryRelayStore, RelayExtension, RelayStore
from loquor.extensions.session import Session, SessionExtension, SessionTransport
from loquor.extensions.trust import PinnedKeys, ProximityProvider, TrustExtension, TrustStore

__all__ = [
    "MemoryRelayStore",
    "PinnedKeys",
    "ProximityProvider",
    "QueryExtension",
    "RelayExtension",
    "RelayStore",
    "Session",
    "SessionExtension",
    "SessionTransport",
    "TrustExtension",
    "TrustStore",
]
