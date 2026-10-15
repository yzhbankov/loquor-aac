"""UDP multicast on one interface: ``localhost`` (loopback) or ``wifi`` (LAN).

TTL 1, so packets never leave the local network. Several agents may run on
one machine: the port is shared (``SO_REUSEPORT``) and multicast loopback is
on, so every process receives every packet, including its own.

``send(to, …)`` is a broadcast here. Processes on one host all bind the
same port, so a unicast to that address would reach only one of them; the
multicast group is the only address that reaches everyone, and the message
carries the recipient's uid.
"""

from __future__ import annotations

import asyncio
import socket
import struct

from loquor.transports.base import Address, OnReceive, Transport

MULTICAST_GROUP = "239.255.76.76"
MULTICAST_PORT = 47600


class MulticastTransport(Transport):
    max_payload = 16384

    def __init__(
        self,
        interface: str = "127.0.0.1",
        port: int = MULTICAST_PORT,
        group: str = MULTICAST_GROUP,
    ):
        self.interface = interface
        self.port = port
        self.group = group
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self, on_receive: OnReceive) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", self.port))
        membership = struct.pack(
            "4s4s", socket.inet_aton(self.group), socket.inet_aton(self.interface)
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.interface)
        )
        sock.setblocking(False)

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:
                on_receive(data, addr)

        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(_Protocol, sock=sock)

    async def broadcast(self, data: bytes) -> None:
        if len(data) > self.max_payload:
            raise ValueError(f"datagram too large ({len(data)} > {self.max_payload} bytes)")
        assert self._transport is not None, "transport not started"
        self._transport.sendto(data, (self.group, self.port))

    async def send(self, to: Address, data: bytes) -> None:
        await self.broadcast(data)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


def primary_ip() -> str:
    """The local IP of the interface with the default route (no packet is sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
