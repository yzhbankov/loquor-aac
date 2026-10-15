"""Extensions: QUERY/OFFER (§4), sessions (§7), relay (§8), trust (§9)."""

import asyncio
import json

import cbor2
import pytest

from loquor.extensions import (
    MemoryRelayStore,
    PinnedKeys,
    ProximityProvider,
    QueryExtension,
    RelayExtension,
    SessionExtension,
    SessionTransport,
    TrustExtension,
)
from loquor.extensions.relay import bloom_has, ids_bloom
from loquor.protocol import AgentCard, Envelope, Identity, Node, Trust, Type, Visibility, skill_entry
from loquor.protocol.crypto import encrypt
from loquor.transports import MemoryHub


def node(hub, name, *caps, visibility=Visibility.VISIBLE):
    card = AgentCard(name=name, skills=[skill_entry(c) for c in caps])
    n = Node(Identity.generate(), hub.transport(), card, visibility=visibility, beacon_interval=0.05)
    n.received = []
    n.on_message(lambda peer, body: _collect(n, peer, body))
    return n


async def _collect(n, peer, body):
    n.received.append((peer.agent_id, body))


async def meshed(*nodes, timeout=1.0):
    """Start every node and wait until all pairs have handshaken."""
    for n in nodes:
        await n.start()

    async def all_pairs():
        while any(b.agent_id not in a.keys for a in nodes for b in nodes if a is not b):
            await asyncio.sleep(0.01)
    await asyncio.wait_for(all_pairs(), timeout)


async def stopped(*nodes):
    for n in nodes:
        await n.stop()


# ------------------------------------------------------------ QUERY / OFFER


async def test_query_collects_offers_from_nodes_with_the_capability():
    hub = MemoryHub()
    asker = node(hub, "Asker")
    w1, w2 = node(hub, "W1", "weather_forecast"), node(hub, "W2", "weather_forecast", "music")
    other = node(hub, "Other", "music")
    exts = [QueryExtension(n) for n in (asker, w1, w2, other)]
    await meshed(asker, w1, w2, other)
    try:
        found = await exts[0].query("weather_forecast", timeout=0.2)
        assert {p.agent_id for p in found} == {w1.agent_id, w2.agent_id}
        assert await exts[0].query("teleportation", timeout=0.1) == []
        assert {p.agent_id for p in await exts[0].query("music", timeout=0.2)} == {w2.agent_id, other.agent_id}
    finally:
        await stopped(asker, w1, w2, other)


async def test_silent_nodes_do_not_answer_queries():
    hub = MemoryHub()
    asker = node(hub, "Asker")
    silent = node(hub, "Silent", "weather_forecast", visibility=Visibility.SILENT)
    q = QueryExtension(asker)
    QueryExtension(silent)
    await meshed(asker, silent)
    try:
        q.node.peers.get(silent.agent_id).card = {"name": "Silent"}  # forget what it offers
        assert await q.query("weather_forecast", timeout=0.2) == []  # no OFFER from a silent node
    finally:
        await stopped(asker, silent)


async def test_query_advertises_extension_types_in_the_card():
    hub = MemoryHub()
    a, b = node(hub, "A"), node(hub, "B")
    QueryExtension(a)
    assert sorted(a.card.extensions) == [5, 6]
    await meshed(a, b)
    try:
        assert b.peers.get(a.agent_id).extensions == [5, 6]
        assert a.peers.get(b.agent_id).extensions == []  # B supports none
    finally:
        await stopped(a, b)


# ------------------------------------------------------------------ sessions


class FakeWifi(SessionTransport):
    name = "wifi"

    async def offer(self, peer):
        return {"host": "10.0.0.5", "port": 4242}


async def test_session_negotiation_falls_back_to_none_without_transports():
    hub = MemoryHub()
    a, b = node(hub, "A"), node(hub, "B")
    sa, sb = SessionExtension(a), SessionExtension(b)
    await meshed(a, b)
    try:
        session = await sa.request(b.peers.get(a.agent_id) and a.peers.get(b.agent_id), "bulk-upload", hint="wifi", timeout=1)
        assert session.transport == "none" and not session.established and session.purpose == "bulk-upload"

        sb.transports["wifi"] = FakeWifi()
        session = await sa.request(a.peers.get(b.agent_id), "bulk-upload", hint="wifi", timeout=1)
        assert session.established and session.transport == "wifi"
        assert session.params == {"host": "10.0.0.5", "port": 4242}

        session = await sa.request(a.peers.get(b.agent_id), "stream", hint="ble-l2cap", timeout=1)
        assert session.transport == "wifi"  # hint unknown: any available transport
    finally:
        await stopped(a, b)


# --------------------------------------------------------------------- relay


def test_relay_store_bounds_expires_and_diffs():
    me = Identity.generate()
    store = MemoryRelayStore(max_items=3, max_age=1000)
    envs = [Envelope(type=Type.MSG, from_id=me.agent_id, to=b"\x01" * 16, ttl=3) for _ in range(3)]
    assert all(store.put(e) for e in envs) and not store.put(envs[2])  # duplicate

    spent = Envelope(type=Type.MSG, from_id=me.agent_id, to=b"\x02" * 16, ttl=0)
    store.put(spent)
    assert len(store) == 3 and envs[0].msg_id not in store.ids()  # bounded: oldest evicted
    assert store.expire() == 1 and spent.msg_id not in store.ids()  # ttl spent

    assert [e.msg_id for e in store.take(b"\x01" * 16)] == [envs[1].msg_id, envs[2].msg_id]
    assert store.take(b"\x01" * 16) == [] and len(store) == 0

    ids = {e.msg_id for e in envs}
    filt = ids_bloom(ids)
    assert len(filt) == 32 and all(bloom_has(filt, i) for i in ids)
    store.put(envs[0]); store.put(spent.relayed() or envs[1])
    assert {e.msg_id for e in store.missing(ids_bloom({envs[0].msg_id}))} == {envs[1].msg_id}


async def test_relay_forwards_stored_envelopes_when_it_meets_the_addressee():
    hub = MemoryHub()
    a, relay, c = node(hub, "A"), node(hub, "Relay"), node(hub, "C")
    ext = RelayExtension(relay)
    assert relay.card.relay is True
    await meshed(a, relay, c)
    try:
        assert c.peers.get(relay.agent_id).card.get("relay") is True

        # A can reach the relay but not C: it sends an envelope for C to the relay's address only.
        env = Envelope(type=Type.MSG, from_id=a.agent_id, to=c.agent_id, ttl=3)
        env.body = encrypt(a.keys[c.agent_id], env, cbor2.dumps({"via": "relay"}))
        env.sign(a.identity)
        await a.transport.send(a.peers.get(relay.agent_id).address, env.encode())
        await asyncio.sleep(0.05)
        stored = ext.store.take(c.agent_id)
        assert [e.msg_id for e in stored] == [env.msg_id] and stored[0].ttl == 2
        for e in stored:
            ext.store.put(e)
        assert c.received == []

        # C meets the relay again (re-HELLO): the relay hands the envelope over.
        await c.hello(c.peers.get(relay.agent_id))
        await asyncio.sleep(0.1)
        assert c.received == [(a.agent_id, {"via": "relay"})]
        # C learned A's address through the relay, so its ACK to A went that way
        # too: the relay overheard it and now holds it for A.
        leftover = ext.store.missing(bytes(32))
        assert [(e.type, e.to) for e in leftover] == [(Type.ACK, a.agent_id)]
    finally:
        await stopped(a, relay, c)


# --------------------------------------------------------------------- trust


def test_pinned_keys_persist(tmp_path):
    path = tmp_path / "pins" / "pinned.json"
    peer = Identity.generate()
    store = PinnedKeys(path)
    assert store.level(peer.agent_id, peer.public_key) == Trust.HEARD
    store.pin(peer.agent_id, peer.public_key)
    assert store.level(peer.agent_id, peer.public_key) == Trust.VERIFIED
    assert store.level(peer.agent_id, Identity.generate().public_key) == Trust.HEARD  # key changed: not verified
    reloaded = PinnedKeys(path)
    assert peer.agent_id in reloaded and reloaded.level(peer.agent_id, peer.public_key) == Trust.VERIFIED
    assert json.loads(path.read_text()) == {peer.agent_id.hex(): peer.public_key.hex()}


class Nearby(ProximityProvider):
    def __init__(self, *ids):
        self.ids = set(ids)

    def is_proximate(self, agent_id):
        return agent_id in self.ids


async def test_trust_extension_sets_peer_level_on_handshake(tmp_path):
    hub = MemoryHub()
    a, b, c = node(hub, "A"), node(hub, "B"), node(hub, "C")
    pins = PinnedKeys(tmp_path / "pins.json")
    pins.pin(b.agent_id, b.identity.public_key)
    ext = TrustExtension(a, stores=[pins], proximity=Nearby(c.agent_id))
    await meshed(a, b, c)
    try:
        assert a.peers.get(b.agent_id).trust == Trust.VERIFIED
        assert a.peers.get(c.agent_id).trust == Trust.PROXIMATE
        assert b.peers.get(a.agent_id).trust == Trust.HEARD  # B has no stores
        assert ext.level_of(a.peers.get(b.agent_id)) == 1
    finally:
        await stopped(a, b, c)
