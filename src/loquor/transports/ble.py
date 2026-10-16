"""Bluetooth Low Energy transport.

How AMP maps onto BLE, where every node is both a *peripheral* (it
advertises and hosts a GATT "inbox" characteristic) and a *central* (it
scans and connects to the peers it hears):

- **Beacons** travel in the advertisement, as the local name: the 16 bytes
  base64url-encoded to 22 characters. A scan that decodes to a beacon is
  delivered as ``on_receive(beacon, device)``.
- **Envelopes** are written to the peer's inbox characteristic, chunked to
  the link MTU with a 6-byte header ``stream_id | index | total`` and
  reassembled on the far side. Writes carry no central identity on most
  stacks, so an envelope that arrives by write has ``address=None``; the
  protocol then answers at the address it learned from the peer's beacon.
- **broadcast()** writes to every peer heard recently (opening links as
  needed) and, for a beacon, also (re)advertises it. **send(to, …)**
  writes to one peer's link.

The radio is behind a small :class:`BleBackend` seam: :class:`BleakBackend`
uses ``bleak`` (central) and ``bless`` (peripheral); tests use an in-memory
backend. Install the radio libraries with ``pip install -e ".[ble]"``.

Known limits of this first version: no per-central addressing on the
server side, one GATT connection per peer kept open lazily, macOS
advertising only carries the local name (no manufacturer data), and the
real-radio path has not yet been exercised on two devices.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct
import time
from collections.abc import Callable
from typing import Any

from loquor.protocol.transport import Address, OnReceive, Transport

log = logging.getLogger("loquor.ble")

SERVICE_UUID = "6c6f7175-6f72-4d50-b100-000000000001"  # "loquor" in hex, AMP
INBOX_UUID = "6c6f7175-6f72-4d50-b100-000000000002"

BEACON_SIZE = 16
BEACON_MAGIC = b"AM"
HEADER = struct.Struct("!HHH")  # stream_id, index, total
DEFAULT_MTU = 185
MIN_CHUNK = 20
PEER_TTL = 60.0  # forget a peer not advertised for this long
STREAM_TTL = 15.0  # drop a half-received envelope after this long


# ---------------------------------------------------------------- codecs


def beacon_to_name(beacon: bytes) -> str:
    return base64.urlsafe_b64encode(beacon).rstrip(b"=").decode()


def name_to_beacon(name: str | None) -> bytes | None:
    if not name or len(name) != 22:
        return None
    try:
        data = base64.urlsafe_b64decode(name + "==")
    except (ValueError, UnicodeEncodeError):
        return None
    return data if len(data) == BEACON_SIZE and data[:2] == BEACON_MAGIC else None


def is_beacon(data: bytes) -> bool:
    return len(data) == BEACON_SIZE and data[:2] == BEACON_MAGIC


def chunk(data: bytes, mtu: int) -> list[bytes]:
    """Split ``data`` into writes that fit ``mtu`` (ATT payload = mtu - 3)."""
    size = max(MIN_CHUNK, mtu - 3) - HEADER.size
    total = max(1, -(-len(data) // size))
    if total > 0xFFFF:
        raise ValueError("data too large to chunk")
    stream = int.from_bytes(os.urandom(2), "big")
    return [HEADER.pack(stream, i, total) + data[i * size:(i + 1) * size] for i in range(total)]


class Reassembler:
    """Puts chunks back together; streams are keyed by their random id."""

    def __init__(self, ttl: float = STREAM_TTL):
        self.ttl = ttl
        self._streams: dict[int, tuple[int, dict[int, bytes], float]] = {}

    def feed(self, data: bytes) -> bytes | None:
        if len(data) < HEADER.size:
            return None
        stream, index, total = HEADER.unpack_from(data)
        if total == 0 or index >= total:
            return None
        _, parts, _ = self._streams.get(stream, (total, {}, 0.0))
        parts[index] = data[HEADER.size:]
        if len(parts) == total:
            self._streams.pop(stream, None)
            return b"".join(parts[i] for i in range(total))
        self._streams[stream] = (total, parts, time.monotonic())
        return None

    def expire(self) -> int:
        cutoff = time.monotonic() - self.ttl
        gone = [k for k, (_, _, at) in self._streams.items() if at < cutoff]
        for k in gone:
            del self._streams[k]
        return len(gone)


# --------------------------------------------------------------- backend


class Link:
    """One open connection to a peer's inbox."""

    mtu: int = DEFAULT_MTU

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class BleBackend:
    """What the transport needs from a radio stack."""

    async def start_server(self, on_write: Callable[[bytes], None]) -> None:
        """Host the inbox characteristic; call ``on_write`` (in the event loop) per write."""
        raise NotImplementedError

    async def advertise(self, name: str) -> None:
        """Advertise with this local name (restart only if it changed)."""
        raise NotImplementedError

    async def start_scan(self, on_advertisement: Callable[[Any, str | None], None]) -> None:
        """Call ``on_advertisement(device, local_name)`` for every advertisement seen."""
        raise NotImplementedError

    async def open(self, device: Any) -> Link:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class BleakBackend(BleBackend):
    """bleak for scanning/connecting, bless for advertising/hosting."""

    def __init__(self) -> None:
        self._server = None
        self._scanner = None
        self._name: str | None = None
        self._on_write: Callable[[bytes], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start_server(self, on_write: Callable[[bytes], None]) -> None:
        self._on_write = on_write
        self._loop = asyncio.get_running_loop()
        await self._restart_server("loquor")

    async def _restart_server(self, name: str) -> None:
        from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

        if self._server is not None:
            await self._server.stop()
        server = BlessServer(name=name)
        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID, INBOX_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
            None, GATTAttributePermissions.writeable,
        )
        server.write_request_func = self._write_request
        await server.start()
        self._server, self._name = server, name

    def _write_request(self, characteristic, value, **kwargs) -> None:
        # Called from the radio stack's thread; hand the bytes to the loop.
        if self._loop is not None and self._on_write is not None:
            self._loop.call_soon_threadsafe(self._on_write, bytes(value))

    async def advertise(self, name: str) -> None:
        if name != self._name:
            await self._restart_server(name)

    async def start_scan(self, on_advertisement: Callable[[Any, str | None], None]) -> None:
        from bleak import BleakScanner

        self._scanner = BleakScanner(detection_callback=lambda device, adv: on_advertisement(device, adv.local_name))
        await self._scanner.start()

    async def open(self, device: Any) -> Link:
        from bleak import BleakClient

        client = BleakClient(device)
        await client.connect()
        return _BleakLink(client)

    async def stop(self) -> None:
        if self._scanner is not None:
            await self._scanner.stop()
        if self._server is not None:
            await self._server.stop()


class _BleakLink(Link):
    def __init__(self, client):
        self.client = client
        self.mtu = getattr(client, "mtu_size", None) or DEFAULT_MTU

    async def write(self, data: bytes) -> None:
        await self.client.write_gatt_char(INBOX_UUID, data, response=False)

    async def close(self) -> None:
        try:
            await self.client.disconnect()
        except Exception:  # the radio may already be gone
            pass


# ------------------------------------------------------------- transport


class BleTransport(Transport):
    max_payload = 65536

    def __init__(self, backend: BleBackend | None = None, *, connect_timeout: float = 15.0):
        self.backend = backend
        self.connect_timeout = connect_timeout
        self._on_receive: OnReceive | None = None
        self._peers: dict[str, tuple[Any, float]] = {}  # key -> (device, last advertised)
        self._links: dict[str, Link] = {}
        self._inbox = Reassembler()
        self._adv_name: str | None = None

    @property
    def interface(self) -> str:
        return "ble"

    # ----------------------------------------------------------- lifecycle

    async def start(self, on_receive: OnReceive) -> None:
        self._on_receive = on_receive
        if self.backend is None:
            self.backend = BleakBackend()
        await self.backend.start_server(self._on_write)
        await self.backend.start_scan(self._on_advertisement)

    async def stop(self) -> None:
        for link in list(self._links.values()):
            await link.close()
        self._links.clear()
        if self.backend is not None:
            await self.backend.stop()

    # ----------------------------------------------------------- inbound

    def _on_write(self, data: bytes) -> None:
        assembled = self._inbox.feed(data)
        if assembled is not None and self._on_receive is not None:
            self._on_receive(assembled, None)  # writes carry no central identity

    def _on_advertisement(self, device: Any, local_name: str | None) -> None:
        beacon = name_to_beacon(local_name)
        if beacon is None:
            return
        self._peers[_key(device)] = (device, time.monotonic())
        if self._on_receive is not None:
            self._on_receive(beacon, device)

    # ---------------------------------------------------------- outbound

    async def broadcast(self, data: bytes) -> None:
        assert self.backend is not None, "transport not started"
        if is_beacon(data):
            self._adv_name = beacon_to_name(data)
            await self.backend.advertise(self._adv_name)
        self._prune()
        self._inbox.expire()
        for key, (device, _) in list(self._peers.items()):
            try:
                await self._write(key, device, data)
            except Exception as exc:
                log.info("ble: %s unreachable (%s)", key, exc)

    async def send(self, to: Address, data: bytes) -> None:
        if to is None:
            await self.broadcast(data)
            return
        await self._write(_key(to), to, data)

    async def _write(self, key: str, device: Any, data: bytes) -> None:
        link = await self._link(key, device)
        try:
            for piece in chunk(data, link.mtu):
                await link.write(piece)
        except Exception:
            self._links.pop(key, None)
            await link.close()
            raise

    async def _link(self, key: str, device: Any) -> Link:
        link = self._links.get(key)
        if link is None:
            assert self.backend is not None, "transport not started"
            link = await asyncio.wait_for(self.backend.open(device), self.connect_timeout)
            self._links[key] = link
        return link

    def _prune(self) -> None:
        cutoff = time.monotonic() - PEER_TTL
        for key, (_, seen) in list(self._peers.items()):
            if seen < cutoff:
                del self._peers[key]
                link = self._links.pop(key, None)
                if link is not None:
                    asyncio.get_running_loop().create_task(link.close())


def _key(device: Any) -> str:
    return str(getattr(device, "address", device))
