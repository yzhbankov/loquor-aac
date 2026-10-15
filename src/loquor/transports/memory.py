"""In-process transport for tests: a hub, and one transport per member.

``broadcast`` reaches every member (the sender included, like multicast
loopback); ``send`` reaches one. Addresses are the members' handles.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loquor.transports.base import Address, OnReceive, Transport


class MemoryHub:
    def __init__(self) -> None:
        self._members: dict[int, OnReceive] = {}
        self._next = 0
        #: Observers called with every datagram sent through the hub.
        self.taps: list[Callable[[bytes], None]] = []

    def transport(self) -> MemoryTransport:
        self._next += 1
        return MemoryTransport(self, self._next)

    def _deliver(self, data: bytes, sender: Address, to: Address | None) -> None:
        loop = asyncio.get_running_loop()
        for tap in self.taps:
            loop.call_soon(tap, data)
        targets = list(self._members.items()) if to is None else [(to, self._members.get(to))]
        for _, on_receive in targets:
            if on_receive is not None:
                loop.call_soon(on_receive, data, sender)


class MemoryTransport(Transport):
    def __init__(self, hub: MemoryHub, address: int):
        self.hub = hub
        self.address: Address = address

    async def start(self, on_receive: OnReceive) -> None:
        self.hub._members[self.address] = on_receive

    async def broadcast(self, data: bytes) -> None:
        self.hub._deliver(data, self.address, None)

    async def send(self, to: Address, data: bytes) -> None:
        self.hub._deliver(data, self.address, to)

    async def stop(self) -> None:
        self.hub._members.pop(self.address, None)
