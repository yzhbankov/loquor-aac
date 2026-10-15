"""Transports move bytes; they know nothing about messages or agents.

- :class:`MulticastTransport` — UDP multicast: ``localhost`` (loopback) and
  ``wifi`` (the LAN interface)
- :class:`MemoryTransport` — in-process, for tests
- ``bluetooth`` — planned

``make_transport(name)`` builds the one named in a profile or on the
command line.
"""

from __future__ import annotations

from loquor.transports.base import Address, OnReceive, Transport
from loquor.transports.memory import MemoryHub, MemoryTransport
from loquor.transports.multicast import MULTICAST_PORT, MulticastTransport, primary_ip

TRANSPORTS = ("localhost", "wifi", "bluetooth")


def make_transport(
    name: str = "localhost", *, interface: str | None = None, port: int = MULTICAST_PORT
) -> Transport:
    """The transport for a name.

    - ``localhost``: multicast on the loopback interface — agents in separate
      processes on one machine; needs no network at all.
    - ``wifi``: multicast on the LAN interface (Wi-Fi, hotspot or Ethernet) —
      agents on different machines. ``interface`` picks the local IP of the
      interface; by default the one with the default route.
    - ``bluetooth``: planned (see docs/ARCHITECTURE.md).

    Agents only hear each other on the same transport.
    """
    if name == "localhost":
        return MulticastTransport(interface="127.0.0.1", port=port)
    if name == "wifi":
        ip = interface or primary_ip()
        if ip.startswith("127."):
            raise OSError(
                "no LAN interface found for wifi transport; join a Wi-Fi network "
                "or hotspot, or pass --interface <local-ip>"
            )
        return MulticastTransport(interface=ip, port=port)
    if name == "bluetooth":
        raise NotImplementedError("bluetooth transport is planned; see docs/ARCHITECTURE.md")
    raise ValueError(f"unknown transport {name!r}; choose from {', '.join(TRANSPORTS)}")


__all__ = [
    "TRANSPORTS",
    "Address",
    "MemoryHub",
    "MemoryTransport",
    "MulticastTransport",
    "OnReceive",
    "Transport",
    "make_transport",
]
