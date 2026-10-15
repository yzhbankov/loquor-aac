"""Transport interface.

A transport carries opaque bytes between agents in range. It offers two
shapes because real media come in both:

- ``broadcast``: everyone in range hears it (adverts). Native on multicast
  and radio; emulated by writing to each connection on BLE.
- ``send(to, …)``: one peer (requests and responses). Native on BLE and
  point-to-point links; on multicast it is simply another broadcast, since
  the message carries the recipient's uid anyway.

An :data:`Address` is whatever the transport uses to reach a peer — an IP
tuple, a BLE connection handle. The agent stores it with the peer it heard
and hands it back; it never interprets it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Address = Any
# Called for every datagram that arrives: the bytes and where they came from.
# Must not block.
OnReceive = Callable[[bytes, Address], None]


class Transport:
    #: Largest payload one call can carry. The protocol caps messages lower
    #: (``MAX_MESSAGE_SIZE``); a transport with a smaller limit chunks
    #: internally or lowers this and the agent refuses larger messages.
    max_payload: int = 65507

    async def start(self, on_receive: OnReceive) -> None:
        raise NotImplementedError

    async def broadcast(self, data: bytes) -> None:
        raise NotImplementedError

    async def send(self, to: Address, data: bytes) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
