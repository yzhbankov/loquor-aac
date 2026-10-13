"""Transport adapters.

The agent layer is transport-agnostic: it hands a signed
:class:`~loquor.messages.Envelope` to a transport and gets a signed envelope
back. The PoC ships an HTTP-over-Wi-Fi/IP transport; BLE (and later UWB or
mesh) are planned as additional adapters behind the same interface, so the
same agent application runs over any of them by swapping adapters.
"""

from loquor.transports.base import PeerUnreachable, Transport
from loquor.transports.http import HttpTransport

__all__ = ["Transport", "PeerUnreachable", "HttpTransport"]
