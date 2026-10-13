"""Transport interface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loquor.discovery import PeerAddress
from loquor.messages import Envelope

# The server-side handler an agent registers with its transport:
# it receives a validated inbound envelope and returns the reply envelope.
MessageHandler = Callable[[Envelope], Awaitable[Envelope]]


class PeerUnreachable(Exception):
    """The peer cannot be reached (disconnected, out of range, gone)."""


class Transport:
    """Request/response exchange of envelopes with peers."""

    async def start(self, handler: MessageHandler) -> int:
        """Start serving inbound messages. Returns the bound port."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def send(self, peer: PeerAddress, envelope: Envelope) -> Envelope:
        """Deliver an envelope to a peer and return its reply envelope.

        Raises :class:`PeerUnreachable` when the peer cannot be contacted.
        """
        raise NotImplementedError
