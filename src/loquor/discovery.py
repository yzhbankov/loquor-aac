"""Peer discovery.

Two implementations behind one interface:

- :class:`ZeroconfDiscovery` — mDNS/DNS-SD (``_loquor._tcp.local.``) via the
  ``zeroconf`` library. This is how two laptops on the same Wi-Fi (or a
  laptop-hosted hotspot) find each other with no configuration and no
  internet access. Each agent advertises its id and capabilities in the
  service TXT records.
- :class:`StaticDiscovery` — a fixed peer list (``host:port`` addresses) for
  environments where multicast is unavailable (containers, CI, some VPNs)
  and for deterministic tests.

Discovery is deliberately independent of the agent layer: an agent takes any
``Discovery`` implementation, just as it takes any transport.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PeerAddress:
    agent_id: str
    host: str
    port: int
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


PeerCallback = Callable[[PeerAddress], Awaitable[None]]

SERVICE_TYPE = "_loquor._tcp.local."


class Discovery:
    """Interface: advertise ourselves, learn about peers."""

    def set_callbacks(
        self,
        on_discovered: PeerCallback | None = None,
        on_lost: PeerCallback | None = None,
    ) -> None:
        self._on_discovered = on_discovered
        self._on_lost = on_lost

    async def start(self, agent_id: str, port: int, capabilities: list[str]) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class StaticDiscovery(Discovery):
    """Pre-configured peers, announced immediately on start."""

    def __init__(self, peers: list[PeerAddress] | None = None):
        self._peers = list(peers or [])
        self._on_discovered: PeerCallback | None = None
        self._on_lost: PeerCallback | None = None

    async def start(self, agent_id: str, port: int, capabilities: list[str]) -> None:
        if self._on_discovered:
            for peer in self._peers:
                await self._on_discovered(peer)

    async def add_peer(self, peer: PeerAddress) -> None:
        self._peers.append(peer)
        if self._on_discovered:
            await self._on_discovered(peer)

    async def stop(self) -> None:
        pass


class ZeroconfDiscovery(Discovery):
    """mDNS/DNS-SD discovery for real local networks."""

    def __init__(self) -> None:
        self._on_discovered: PeerCallback | None = None
        self._on_lost: PeerCallback | None = None
        self._zc = None
        self._browser = None
        self._info = None
        self._agent_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, agent_id: str, port: int, capabilities: list[str]) -> None:
        from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

        self._agent_id = agent_id
        self._loop = asyncio.get_running_loop()
        self._zc = Zeroconf()

        host_ip = _primary_ip()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{agent_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(host_ip)],
            port=port,
            properties={
                "agent_id": agent_id,
                "capabilities": json.dumps(capabilities),
            },
        )
        await self._loop.run_in_executor(None, self._zc.register_service, self._info)
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, handlers=[self._on_change])

    def _on_change(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange

        info = zeroconf.get_service_info(service_type, name, timeout=2000)
        if info is None or self._loop is None:
            return
        props = {
            k.decode(): v.decode() if isinstance(v, bytes) else v
            for k, v in (info.properties or {}).items()
            if v is not None
        }
        agent_id = props.get("agent_id", name.split(".")[0])
        if agent_id == self._agent_id:
            return  # ignore our own advertisement
        addresses = info.parsed_addresses()
        if not addresses:
            return
        peer = PeerAddress(
            agent_id=agent_id,
            host=addresses[0],
            port=info.port or 0,
            capabilities=tuple(json.loads(props.get("capabilities", "[]"))),
        )
        if state_change is ServiceStateChange.Removed:
            if self._on_lost:
                asyncio.run_coroutine_threadsafe(self._on_lost(peer), self._loop)
        elif self._on_discovered:
            asyncio.run_coroutine_threadsafe(self._on_discovered(peer), self._loop)

    async def stop(self) -> None:
        if self._zc is not None:
            loop = asyncio.get_running_loop()
            if self._info is not None:
                await loop.run_in_executor(None, self._zc.unregister_service, self._info)
            await loop.run_in_executor(None, self._zc.close)
            self._zc = None


def _primary_ip() -> str:
    """Best-effort LAN IP (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))  # TEST-NET address; connect() just picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
