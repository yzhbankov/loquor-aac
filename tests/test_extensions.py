"""Extensions: QUERY/OFFER (§4), sessions (§7), relay (§8), trust (§9)."""

import asyncio

from loquor.extensions import QueryExtension
from loquor.protocol import AgentCard, Identity, Node, Visibility, skill_entry
from loquor.transports import MemoryHub


def node(hub, name, *caps, visibility=Visibility.VISIBLE):
    card = AgentCard(name=name, skills=[skill_entry(c) for c in caps])
    return Node(Identity.generate(), hub.transport(), card, visibility=visibility, beacon_interval=0.05)


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


async def test_silent_nodes_do_not_answer_queries_but_known_peers_still_count():
    hub = MemoryHub()
    asker = node(hub, "Asker")
    silent = node(hub, "Silent", "weather_forecast", visibility=Visibility.SILENT)
    q = QueryExtension(asker)
    QueryExtension(silent)
    await meshed(asker, silent)
    try:
        offers_only = []
        q.node.peers.get(silent.agent_id).card = {"name": "Silent"}  # forget what it offers
        assert await q.query("weather_forecast", timeout=0.2) == offers_only  # no OFFER from a silent node
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
        assert a.peers.get(b.agent_id).extensions == []  # B supports none; intersection is empty
    finally:
        await stopped(a, b)
