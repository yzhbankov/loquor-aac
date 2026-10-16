"""BLE transport: beacon-in-name codec, chunking, and the protocol over a fake radio."""

import asyncio
import os

import pytest

from loquor.protocol import AgentCard, Identity, Node, skill_entry
from loquor.transports.ble import (
    DEFAULT_MTU,
    HEADER,
    INBOX_UUID,
    SERVICE_UUID,
    BleakBackend,
    BleBackend,
    BleTransport,
    Link,
    Reassembler,
    beacon_to_name,
    chunk,
    name_to_beacon,
)

# ------------------------------------------------------------ fake radio


class Air:
    """Every backend on the air hears every advertisement and can connect to any other."""

    def __init__(self):
        self.backends: dict[str, FakeBackend] = {}
        self.writes: list[tuple[str, str, bytes]] = []  # (from, to, chunk)


class FakeLink(Link):
    def __init__(self, air, src, dst, mtu):
        self.air, self.src, self.dst, self.mtu = air, src, dst, mtu
        self.open = True

    async def write(self, data):
        if not self.open or self.dst.address not in self.air.backends:
            raise OSError("link lost")
        self.air.writes.append((self.src, self.dst.address, data))
        asyncio.get_running_loop().call_soon(self.dst.on_write, data)

    async def close(self):
        self.open = False


class FakeDevice:
    def __init__(self, address):
        self.address = address


class FakeBackend(BleBackend):
    def __init__(self, air, address, mtu=DEFAULT_MTU, connect_delay=0.0):
        self.air, self.address, self.mtu, self.connect_delay = air, address, mtu, connect_delay
        self.on_write = None
        self.on_adv = None
        self.name = None
        self.advertised = []
        self.opened = 0

    async def start_server(self, on_write):
        self.on_write = on_write
        self.air.backends[self.address] = self

    async def advertise(self, name):
        self.name = name
        self.advertised.append(name)
        # Real radios repeat advertisements; here each call reaches every scanner once.
        for other in self.air.backends.values():
            if other is not self and other.on_adv is not None:
                asyncio.get_running_loop().call_soon(other.on_adv, FakeDevice(self.address), name)

    async def start_scan(self, on_advertisement):
        self.on_adv = on_advertisement

    async def open(self, device):
        self.opened += 1
        await asyncio.sleep(self.connect_delay)
        target = self.air.backends.get(device.address)
        if target is None:
            raise OSError("no such device")
        return FakeLink(self.air, self.address, target, self.mtu)

    async def stop(self):
        self.air.backends.pop(self.address, None)


# --------------------------------------------------------------- codecs


def test_beacon_rides_in_the_local_name():
    beacon = b"AM\x01" + os.urandom(13)
    name = beacon_to_name(beacon)
    assert len(name) == 22 and name.isascii()
    assert name_to_beacon(name) == beacon
    assert name_to_beacon(None) is None and name_to_beacon("Kitchen Speaker") is None
    assert name_to_beacon(beacon_to_name(b"XX" + bytes(14))) is None  # wrong magic
    assert name_to_beacon("!" * 22) is None


def test_chunking_and_reassembly():
    data = os.urandom(5000)
    pieces = chunk(data, mtu=100)
    assert all(len(p) <= 97 for p in pieces) and len(pieces) == -(-5000 // (97 - HEADER.size))
    r = Reassembler()
    assert all(r.feed(p) is None for p in pieces[:-1])
    assert r.feed(pieces[-1]) == data
    assert chunk(b"", mtu=100) and r.feed(chunk(b"", 100)[0]) == b""

    # Two interleaved streams, out of order, arrive intact.
    a, b = chunk(b"a" * 300, 50), chunk(b"b" * 300, 50)
    out = [r.feed(p) for p in [a[0], b[0], b[1], a[1], *reversed(a[2:]), *b[2:]]]
    assert sorted(x for x in out if x is not None) == [b"a" * 300, b"b" * 300]

    assert r.feed(b"short") is None and r.feed(HEADER.pack(1, 5, 2)) is None  # garbage ignored
    r.ttl = 0
    r.feed(chunk(b"x" * 500, 50)[0])
    assert r.expire() == 1
    with pytest.raises(ValueError):
        chunk(b"x" * 20 * 70000, mtu=23)


# ---------------------------------------------------------- transport


async def test_beacons_are_advertised_and_envelopes_written():
    air = Air()
    a, b = BleTransport(FakeBackend(air, "AA")), BleTransport(FakeBackend(air, "BB", mtu=40))
    got_a, got_b = [], []
    await a.start(lambda data, dev: got_a.append((data, dev)))
    await b.start(lambda data, dev: got_b.append((data, dev)))
    try:
        beacon_a = b"AM\x01" + b"\xaa" * 13
        await a.broadcast(beacon_a)  # advertised; nobody known yet to write to
        await asyncio.sleep(0)
        assert a.backend.name == beacon_to_name(beacon_a)
        assert got_b and got_b[0][0] == beacon_a and got_b[0][1].address == "AA"
        assert "AA" in b._peers and a.interface == "ble"

        # B now knows A: a unicast write, chunked to B's small MTU, arrives whole with no address.
        payload = os.urandom(300)
        await b.send(got_b[0][1], payload)
        await asyncio.sleep(0)
        assert got_a == [(payload, None)]
        assert len([w for w in air.writes if w[1] == "AA"]) == len(chunk(payload, 40))

        # B's beacon broadcast is advertised *and* written to A (connected peers may stop hearing adverts).
        beacon_b = b"AM\x01" + b"\xbb" * 13
        await b.broadcast(beacon_b)
        await asyncio.sleep(0)
        assert (beacon_b, None) in got_a and any(d is not None and d.address == "BB" for _, d in got_a)

        await a.send(None, b"to everyone")  # None address = broadcast
        await asyncio.sleep(0)
        assert (b"to everyone", None) in got_b
    finally:
        await a.stop()
        await b.stop()


async def test_lost_link_is_dropped_and_reopened():
    air = Air()
    a, b = BleTransport(FakeBackend(air, "AA")), BleTransport(FakeBackend(air, "BB"))
    await a.start(lambda *_: None)
    got = []
    await b.start(lambda data, dev: got.append(data))
    try:
        await b.broadcast(b"AM" + bytes(14))
        await asyncio.sleep(0)
        device = a._peers["BB"][0]
        await a.send(device, b"one")
        link = a._links["BB"]
        link.open = False  # the radio dropped it
        with pytest.raises(OSError):
            await a.send(device, b"two")
        assert "BB" not in a._links
        await a.send(device, b"three")  # reconnects
        await asyncio.sleep(0)
        assert got == [b"one", b"three"]
    finally:
        await a.stop()
        await b.stop()


# ------------------------------------------------------- the protocol


def node(air, address, name, *caps, mtu=DEFAULT_MTU):
    card = AgentCard(name=name, skills=[skill_entry(c) for c in caps])
    n = Node(Identity.generate(), BleTransport(FakeBackend(air, address, mtu)), card, beacon_interval=0.05)
    n.received = []
    n.on_message(lambda peer, body: _collect(n, peer, body))
    return n


async def _collect(n, peer, body):
    n.received.append((peer.agent_id, body))


async def test_two_nodes_handshake_and_exchange_over_ble():
    air = Air()
    a, b = node(air, "AA", "Alice", "echo"), node(air, "BB", "Bob", "weather_forecast", mtu=60)
    await a.start()
    await b.start()
    try:
        async def handshaken():
            while not (b.agent_id in a.keys and a.agent_id in b.keys):
                await asyncio.sleep(0.01)
        await asyncio.wait_for(handshaken(), 2)
        peer_b = a.peers.get(b.agent_id)
        assert peer_b.address is not None and peer_b.address.address == "BB"  # learned from the beacon
        assert peer_b.capabilities == ["weather_forecast"]

        # A 2 KB body: chunked over Bob's small MTU, decrypted, acknowledged.
        body = {"blob": "x" * 2000, "n": 1}
        await a.send(peer_b, body, wait_ack=True, timeout=2)
        assert b.received == [(a.agent_id, body)]
        await b.send(b.peers.get(a.agent_id), {"reply": True}, wait_ack=True, timeout=2)
        assert a.received == [(b.agent_id, {"reply": True})]
    finally:
        await a.stop()
        await b.stop()
    assert all(w[2][:2] != b"AM" or len(w[2]) != 16 for w in air.writes)  # beacons on the wire are chunk-framed


def test_make_transport_explains_missing_radio_libraries(monkeypatch):
    import builtins
    from loquor.transports import make_transport

    real_import = builtins.__import__

    def no_bless(name, *args, **kwargs):
        if name == "bless":
            raise ImportError("No module named 'bless'", name="bless")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_bless)
    with pytest.raises(OSError, match=r'pip install -e "\.\[ble\]"'):
        make_transport("bluetooth")


async def test_concurrent_sends_to_one_peer_open_a_single_link():
    """BLE stacks refuse a second connect while one is in progress (br-connection-busy)."""
    air = Air()
    a, b = BleTransport(FakeBackend(air, "AA", connect_delay=0.02)), BleTransport(FakeBackend(air, "BB"))
    got = []
    await a.start(lambda *_: None)
    await b.start(lambda data, dev: got.append(data))
    try:
        await b.broadcast(b"AM" + bytes(14))
        await asyncio.sleep(0)
        device = a._peers["BB"][0]
        await asyncio.gather(a.send(device, b"one"), a.send(device, b"two"), a.send(device, b"three"))
        await asyncio.sleep(0)
        assert a.backend.opened == 1 and sorted(got) == [b"one", b"three", b"two"]
    finally:
        await a.stop()
        await b.stop()


# ------------------------------------------------- the real backend, stubbed


class StubDelegate:
    def __init__(self):
        self.calls = []

    async def stop_advertising(self):
        self.calls.append("stop")

    async def start_advertising(self, data, timeout=2.0):
        name = next(v for k, v in data.items() if str(k) == "kCBAdvDataLocalName")
        self.calls.append(("start", name))


class StubBlessServer:
    instances = []

    def __init__(self, name):
        self.name, self.services, self.chars, self.started = name, [], [], 0
        self.peripheral_manager_delegate = StubDelegate()
        StubBlessServer.instances.append(self)

    async def add_new_service(self, uuid):
        self.services.append(uuid)

    async def add_new_characteristic(self, service, char, props, value, perms):
        self.chars.append(char)

    async def start(self):
        self.started += 1

    async def stop(self):
        pass


async def test_bleak_backend_registers_the_service_once_and_only_swaps_the_name(monkeypatch):
    import types, sys
    stub = types.ModuleType("bless")
    stub.BlessServer = StubBlessServer
    stub.GATTCharacteristicProperties = types.SimpleNamespace(write=1, write_without_response=2)
    stub.GATTAttributePermissions = types.SimpleNamespace(writeable=1)
    monkeypatch.setitem(sys.modules, "bless", stub)
    StubBlessServer.instances.clear()

    backend = BleakBackend()
    received = []
    await backend.start_server(received.append)
    assert backend._server is None  # lazy: nothing hosted until there is a name to advertise

    await backend.advertise("first-beacon-name")
    await backend.advertise("first-beacon-name")  # unchanged: no-op
    await backend.advertise("second-beacon-name")  # changed: swap the advertisement only
    server = backend._server
    assert len(StubBlessServer.instances) == 1 and server.started == 1
    assert server.services == [SERVICE_UUID] and server.chars == [INBOX_UUID]
    assert server.peripheral_manager_delegate.calls == ["stop", ("start", "second-beacon-name")]
    assert backend._name == "second-beacon-name"

    # Writes arrive from the radio thread and are handed to the loop as bytes.
    backend._write_request(None, bytearray(b"hi"))
    await asyncio.sleep(0)
    assert received == [b"hi"]
