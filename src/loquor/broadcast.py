"""Broadcast protocol: every agent hears every message.

All agents share one channel on the local network (UDP multicast). There is
no server and no point-to-point connection; every agent behaves the same way:

- it **advertises** itself every 10 seconds — its unique id (uid),
  name, description and capabilities;
- it **listens** to everything on the channel;
- it **acts only on messages addressed to it** — a request or response whose
  ``recipient`` equals its own uid. Everything else is just overheard.

Three message types travel on the channel::

    advert    sender=<uid>  recipient=None    payload={name, description, capabilities}
    request   sender=<uid>  recipient=<uid>   payload={request_id, question}
    response  sender=<uid>  recipient=<uid>   payload={request_id, answer}

UDP can drop packets, so requests are retried with the same ``request_id``
and responders answer a repeated ``request_id`` from cache instead of
reasoning again (no duplicate answers). Every message is HMAC-signed with the
pairing secret (see :mod:`loquor.identity`); unsigned messages are ignored.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
import uuid
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from loquor.discovery import _primary_ip

PROTOCOL_VERSION = "loquor-broadcast/0.1"
MULTICAST_GROUP = "239.255.76.76"
MULTICAST_PORT = 47600
# Local-network datagrams; keep messages well below this.
MAX_DATAGRAM = 16384


class BroadcastType(str, Enum):
    ADVERT = "advert"
    REQUEST = "request"
    RESPONSE = "response"


class BroadcastMessage(BaseModel):
    protocol: str = PROTOCOL_VERSION
    type: BroadcastType
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sender: str  # uid of the sending agent
    sender_name: str = ""
    recipient: str | None = None  # uid of the addressee; None = everyone
    payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: float = Field(default_factory=time.time)
    signature: str | None = None

    def signing_bytes(self) -> bytes:
        data = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


DatagramHandler = Callable[[bytes], None]


class Bus:
    """Interface: a shared channel where every member hears every message."""

    async def start(self, on_datagram: DatagramHandler) -> None:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class MulticastBus(Bus):
    """UDP multicast on the local network (TTL 1: never leaves the LAN).

    Several agents may run on one machine: the port is shared
    (``SO_REUSEPORT``) and multicast loopback is on, so each process
    receives every message, including from agents on the same host.
    """

    def __init__(
        self,
        group: str = MULTICAST_GROUP,
        port: int = MULTICAST_PORT,
        interface: str = "0.0.0.0",
    ):
        self.group = group
        self.port = port
        self.interface = interface
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self, on_datagram: DatagramHandler) -> None:
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
        if self.interface != "0.0.0.0":
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.interface)
            )
        sock.setblocking(False)

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:
                on_datagram(data)

        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(_Protocol, sock=sock)

    def send(self, data: bytes) -> None:
        if len(data) > MAX_DATAGRAM:
            raise ValueError(f"message too large for one datagram ({len(data)} bytes)")
        assert self._transport is not None, "bus not started"
        self._transport.sendto(data, (self.group, self.port))

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


class LocalBus(Bus):
    """In-process channel for tests: every member hears every message."""

    class _Hub:
        def __init__(self) -> None:
            self.members: list[DatagramHandler] = []

    def __init__(self, hub: LocalBus._Hub | None = None):
        self.hub = hub or LocalBus._Hub()
        self._handler: DatagramHandler | None = None

    def join(self) -> LocalBus:
        """Another member of the same channel."""
        return LocalBus(self.hub)

    async def start(self, on_datagram: DatagramHandler) -> None:
        self._handler = on_datagram
        self.hub.members.append(on_datagram)

    def send(self, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        for member in list(self.hub.members):
            loop.call_soon(member, data)

    async def stop(self) -> None:
        if self._handler in self.hub.members:
            self.hub.members.remove(self._handler)


TRANSPORTS = ("localhost", "wifi", "bluetooth")


def make_bus(
    transport: str = "localhost", *, interface: str | None = None, port: int = MULTICAST_PORT
) -> Bus:
    """The channel for a transport name.

    - ``localhost``: multicast on the loopback interface — agents in separate
      processes on one machine; needs no network at all.
    - ``wifi``: multicast on the LAN interface (Wi-Fi, hotspot or Ethernet) —
      agents on different machines. ``interface`` picks the local IP of the
      interface; by default the one with the default route.
    - ``bluetooth``: planned (see docs/PROTOCOL.md).

    Agents only hear each other on the same transport: a ``localhost`` agent
    and a ``wifi`` agent on one machine use different interfaces.
    """
    if transport == "localhost":
        return MulticastBus(interface="127.0.0.1", port=port)
    if transport == "wifi":
        ip = interface or _primary_ip()
        if ip.startswith("127."):
            raise OSError(
                "no LAN interface found for wifi transport; join a Wi-Fi network "
                "or hotspot, or pass --interface <local-ip>"
            )
        return MulticastBus(interface=ip, port=port)
    if transport == "bluetooth":
        raise NotImplementedError("bluetooth transport is planned; see docs/PROTOCOL.md")
    raise ValueError(f"unknown transport {transport!r}; choose from {', '.join(TRANSPORTS)}")
