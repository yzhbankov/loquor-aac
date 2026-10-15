"""§4-5 Node: HELLO/HELLO_ACK happy path, encryption, ACK, rejections, policies."""

import asyncio

import cbor2
import pytest

from loquor.protocol import AgentCard, Envelope, Identity, Node, Type, Visibility, skill_entry
from loquor.protocol.crypto import DecryptError, decrypt, encrypt, pair_key
from loquor.protocol.discovery import is_beacon
from loquor.transports import MemoryHub


def card(name, *caps):
    return AgentCard(name=name, description=f"I am {name}.", skills=[skill_entry(c) for c in caps])


def node(hub, name, *caps, visibility=Visibility.VISIBLE):
    n = Node(Identity.generate(), hub.transport(), card(name, *caps), visibility=visibility, beacon_interval=0.05)
    n.received = []
    n.on_message(lambda peer, body: _collect(n, peer, body))
    return n


async def _collect(n, peer, body):
    n.received.append((peer.agent_id, body))


async def handshaken(a, b, timeout=1.0):
    async def both():
        while not (b.agent_id in a.keys and a.agent_id in b.keys):
            await asyncio.sleep(0.01)
    await asyncio.wait_for(both(), timeout)


async def running(*nodes):
    for n in nodes:
        await n.start()


async def stopped(*nodes):
    for n in nodes:
        await n.stop()


async def test_handshake_then_encrypted_message_with_ack():
    hub = MemoryHub()
    a, b = node(hub, "Alice", "echo"), node(hub, "Bob", "weather_forecast")
    taps = []
    hub.taps.append(taps.append)
    await running(a, b)
    try:
        await handshaken(a, b)
        assert a.keys[b.agent_id] == b.keys[a.agent_id]  # same pair key on both sides
        peer_b = a.peers.get(b.agent_id)
        assert peer_b.handshaken and peer_b.name == "Bob" and peer_b.capabilities == ["weather_forecast"]
        assert peer_b.public_key == b.identity.public_key
        assert b.peers.get(a.agent_id).capabilities == ["echo"]

        await a.send(peer_b, {"hello": "bob", "n": 1}, wait_ack=True, timeout=1)
        assert b.received == [(a.agent_id, {"hello": "bob", "n": 1})]
    finally:
        await stopped(a, b)
    # Nothing readable crossed the wire: the MSG body was ciphertext.
    msgs = [Envelope.decode(d) for d in taps if not is_beacon(d)]
    assert any(e.type == Type.MSG for e in msgs) and any(e.type == Type.ACK for e in msgs)
    assert not any(b"bob" in e.body for e in msgs if e.type == Type.MSG)


async def test_pair_key_and_aead_bind_body_to_header():
    a, b = Identity.generate(), Identity.generate()
    key = pair_key(a, b.public_key, b.agent_id)
    assert key == pair_key(b, a.public_key, a.agent_id)
    env = Envelope(type=Type.MSG, from_id=a.agent_id, to=b.agent_id)
    env.body = encrypt(key, env, b"secret")
    assert decrypt(key, env) == b"secret"
    moved = Envelope(type=Type.MSG, from_id=a.agent_id, to=b.agent_id, body=env.body)  # new msg_id
    with pytest.raises(DecryptError):
        decrypt(key, moved)
    with pytest.raises(DecryptError):
        decrypt(pair_key(a, Identity.generate().public_key, b.agent_id), env)
    with pytest.raises(DecryptError, match="too short"):
        decrypt(key, Envelope(type=Type.MSG, from_id=a.agent_id, body=b"x"))


async def test_tampered_and_unknown_sender_envelopes_are_dropped():
    hub = MemoryHub()
    a, b = node(hub, "Alice"), node(hub, "Bob")
    mallory = Identity.generate()
    await running(a, b)
    try:
        await handshaken(a, b)
        peer_a = b.peers.get(a.agent_id)
        key = b.keys[a.agent_id]

        # 1. Forged: claims to be from Bob but signed by Mallory.
        forged = Envelope(type=Type.MSG, from_id=b.agent_id, to=a.agent_id)
        forged.body = encrypt(key, forged, cbor2.dumps({"forged": True}))
        forged.sig = mallory.sign(forged.signed_bytes())
        await b.transport.send(peer_a.address, forged.encode())

        # 2. Signed by Bob, then the body flipped in flight.
        real = Envelope(type=Type.MSG, from_id=b.agent_id, to=a.agent_id)
        real.body = encrypt(key, real, cbor2.dumps({"real": True}))
        real.sign(b.identity)
        real.body = real.body[:-1] + bytes([real.body[-1] ^ 1])
        await b.transport.send(peer_a.address, real.encode())

        # 3. Valid signature from a stranger who never said HELLO.
        stranger = Envelope(type=Type.MSG, from_id=mallory.agent_id, to=a.agent_id, body=b"hi").sign(mallory)
        await b.transport.send(peer_a.address, stranger.encode())

        # 4. HELLO whose public key does not hash to its from id.
        bad_hello = Envelope(type=Type.HELLO, from_id=mallory.agent_id,
                             body=cbor2.dumps({"pk": b.identity.public_key, "card": card("X").to_dict(), "ext": []}))
        bad_hello.sign(mallory)
        await b.transport.broadcast(bad_hello.encode())

        await asyncio.sleep(0.1)
        assert a.received == []
        assert mallory.agent_id not in a.peers
    finally:
        await stopped(a, b)


async def test_unknown_envelope_types_are_ignored_and_duplicates_dropped():
    hub = MemoryHub()
    a, b = node(hub, "Alice"), node(hub, "Bob")
    await running(a, b)
    try:
        await handshaken(a, b)
        peer_a = b.peers.get(a.agent_id)
        future = Envelope(type=200, from_id=b.agent_id, to=a.agent_id, body=b"from the future").sign(b.identity)
        await b.transport.send(peer_a.address, future.encode())
        await b.transport.send(peer_a.address, future.encode())  # duplicate msg_id
        await asyncio.sleep(0.05)
        # Still working afterwards:
        await b.send(peer_a, {"ok": 1}, wait_ack=True, timeout=1)
        assert a.received == [(b.agent_id, {"ok": 1})]
        assert a.dedup.seen(future.msg_id)
    finally:
        await stopped(a, b)


async def test_quiet_node_never_beacons_but_handshakes():
    hub = MemoryHub()
    quiet, loud = node(hub, "Quiet", "q", visibility=Visibility.QUIET), node(hub, "Loud", "l")
    beacons_from_quiet = []
    hub.taps.append(lambda d: beacons_from_quiet.append(d) if is_beacon(d) and d[3:11] == quiet.agent_id[:8] else None)
    await running(quiet, loud)
    try:
        await handshaken(quiet, loud)
        assert quiet.peers.get(loud.agent_id).name == "Loud"
        assert loud.peers.get(quiet.agent_id).name == "Quiet"
    finally:
        await stopped(quiet, loud)
    assert beacons_from_quiet == []


async def test_silent_node_ignores_unsolicited_hello_but_can_initiate():
    hub = MemoryHub()
    silent = node(hub, "Silent", "s", visibility=Visibility.SILENT)
    loud = node(hub, "Loud", "l")
    await running(silent, loud)
    try:
        # Loud beacons; Silent hears it and initiates HELLO: both handshake.
        await handshaken(silent, loud)
        # A fresh HELLO from a stranger is ignored by the silent node.
        stranger = node(hub, "Stranger", "x", visibility=Visibility.QUIET)
        await stranger.start()
        try:
            await stranger.hello()  # broadcast, unsolicited
            await asyncio.sleep(0.1)
            assert stranger.agent_id not in silent.peers
            assert stranger.agent_id in loud.peers  # the visible node answered it
        finally:
            await stranger.stop()
    finally:
        await stopped(silent, loud)


async def test_capability_change_bumps_seq_and_refreshes_card():
    hub = MemoryHub()
    a, b = node(hub, "Alice", "one"), node(hub, "Bob", "b")
    await running(a, b)
    try:
        await handshaken(a, b)
        assert b.peers.get(a.agent_id).capabilities == ["one"]
        a.update_card(card("Alice", "one", "two"))
        assert a.seq == 1

        async def refreshed():
            while b.peers.get(a.agent_id).capabilities != ["one", "two"]:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(refreshed(), 1.0)
    finally:
        await stopped(a, b)


async def test_peers_are_forgotten_when_silent():
    hub = MemoryHub()
    a, b = node(hub, "Alice"), node(hub, "Bob")
    await running(a, b)
    await handshaken(a, b)
    await a.stop()
    await asyncio.sleep(0.3)  # > 3 beacon intervals
    await b.stop()
    assert a.agent_id not in b.peers and a.agent_id not in b.keys
