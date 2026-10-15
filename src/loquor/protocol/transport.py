"""The transport port: what the protocol needs from any link.

A transport carries opaque bytes between nodes in range. Two shapes, because
real media come in both:

- ``broadcast``: everyone in range hears it (beacons, HELLO to unknowns).
  Native on multicast and radio; emulated by writing to each connection on BLE.
- ``send(to, …)``: one peer. Native on BLE and point-to-point links; on
  multicast it is another broadcast — the envelope carries the recipient's
  agent id anyway.

An :data:`Address` is whatever the transport uses to reach a peer — an IP
tuple, a BLE connection handle. The protocol stores it with the peer it
heard and hands it back; it never interprets it. No protocol logic depends
on a specific radio.

Implementations live in :mod:`loquor.transports`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Address = Any
# Called for every datagram that arrives: the bytes and where they came from.
# Must not block.
OnReceive = Callable[[bytes, Address], None]


class Transport:
    #: Largest payload one call can carry. A transport with a smaller limit
    #: than the envelope cap (64 KB) chunks internally or lowers this.
    max_payload: int = 65536

    async def start(self, on_receive: OnReceive) -> None:
        raise NotImplementedError

    async def broadcast(self, data: bytes) -> None:
        raise NotImplementedError

    async def send(self, to: Address, data: bytes) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
