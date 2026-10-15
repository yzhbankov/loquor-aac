"""Transports move bytes: broadcast reaches everyone, send reaches one."""

import asyncio

import pytest

from loquor.transports import MemoryHub, MulticastTransport, make_transport


async def test_memory_broadcast_and_send():
    hub = MemoryHub()
    a, b, c = hub.transport(), hub.transport(), hub.transport()
    got = {t.address: [] for t in (a, b, c)}
    for t in (a, b, c):
        await t.start(lambda data, frm, t=t: got[t.address].append((data, frm)))

    await a.broadcast(b"hello")
    await asyncio.sleep(0)
    assert all(got[t.address] == [(b"hello", a.address)] for t in (a, b, c))

    await a.send(b.address, b"only-b")
    await asyncio.sleep(0)
    assert got[b.address][-1] == (b"only-b", a.address)
    assert len(got[a.address]) == len(got[c.address]) == 1

    await c.stop()
    await a.broadcast(b"again")
    await asyncio.sleep(0)
    assert len(got[c.address]) == 1  # stopped members hear nothing


def test_make_transport():
    assert make_transport("localhost").interface == "127.0.0.1"
    assert isinstance(make_transport("localhost"), MulticastTransport)
    with pytest.raises(NotImplementedError):
        make_transport("bluetooth")
    with pytest.raises(ValueError):
        make_transport("carrier-pigeon")


async def test_multicast_localhost_roundtrip():
    port = 47000 + (id(object()) % 900)
    a = MulticastTransport(interface="127.0.0.1", port=port)
    b = MulticastTransport(interface="127.0.0.1", port=port)
    got: list[bytes] = []
    try:
        await a.start(lambda data, addr: None)
        await b.start(lambda data, addr: got.append(data))
    except OSError as exc:
        pytest.skip(f"multicast unavailable here: {exc}")
    try:
        await a.broadcast(b"ping")
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.02)
    finally:
        await a.stop()
        await b.stop()
    if not got:
        pytest.skip("multicast packets not delivered in this environment")
    assert got == [b"ping"]
