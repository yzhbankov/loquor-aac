"""The orchestrator: dispatch to skills by capability, ask with retries, trust."""

import asyncio
import json

import pytest

from loquor.agent import Agent, RequestError, RequestTracker, Skill
from loquor.agent.skills import Goal, LlmAnswer
from loquor.protocol import Identity, Trust
from loquor.reasoning import Reasoner
from loquor.transports import MemoryHub, make_transport

WEATHER_PROMPT = "You forecast the weather. Random number: {random_number}"
CLOTHES_PROMPT = "You recommend clothes."
GOAL = "Recommend what to wear tomorrow."


class ScriptedReasoner(Reasoner):
    """Stands in for an LLM: decides on peers, forecasts, recommends."""

    name = "scripted"

    def __init__(self):
        self.calls: list[str] = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.calls.append(prompt)
        if schema is not None:  # decision about a peer
            useful = "weather" in prompt.lower()
            return json.dumps({
                "useful": useful,
                "reason": "it forecasts weather" if useful else "not relevant",
                "capability": "weather_forecast" if useful else "",
                "question": "What is the weather tomorrow?" if useful else "",
            })
        if "asks:" in prompt:  # answering a request
            assert "{random_number}" not in system
            return "Tomorrow: 3 degrees Celsius and snowy."
        return "Wear a warm coat, gloves and boots."


class Echo(Skill):
    capability = "echo"
    input_schema = {"type": "object"}

    async def handle(self, input, ctx):
        return {"echo": input}


class Broken(Skill):
    capability = "broken"

    async def handle(self, input, ctx):
        raise RuntimeError("kaboom")


class Private(Skill):
    capability = "private"
    min_trust = Trust.VERIFIED

    async def handle(self, input, ctx):
        return {"secret": 42}


def weather_skills():
    return [LlmAnswer("weather_forecast", WEATHER_PROMPT)]


def clothing_skills():
    return [LlmAnswer("clothing_recommendation", CLOTHES_PROMPT), Goal(GOAL, CLOTHES_PROMPT)]


def make(name, skills, transport, reasoner=None, **kwargs):
    return Agent(
        name=name,
        description=f"I am {name}.",
        skills=skills,
        reasoner=reasoner or ScriptedReasoner(),
        transport=transport,
        identity=Identity.generate(),
        beacon_interval=0.05,
        request_timeout=1.0,
        **kwargs,
    )


def goal_of(agent) -> Goal:
    return next(s for s in agent.skills if isinstance(s, Goal))


async def running(*agents):
    for agent in agents:
        await agent.start()


async def stopped(*agents):
    for agent in agents:
        await agent.stop()


async def peer_of(client, server, timeout=1.0):
    """Wait until ``client`` has handshaken with ``server``; return the Peer."""
    async def wait():
        while server.agent_id not in client.node.keys:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)
    return client.node.peers.get(server.agent_id)


# ------------------------------------------------------------- unit pieces


async def test_tracker_outbound_and_cache():
    t = RequestTracker(cache_size=1)
    f = t.open("r1")
    t.resolve("r1", {"a": 1})
    assert await f == {"a": 1}
    f2 = t.open("r2")
    t.fail("r2", RequestError("skill_failed", "x"))
    with pytest.raises(RequestError, match="skill_failed"):
        await f2
    t.close("r1"), t.close("r2")
    t.resolve("r1", {})  # unknown ids are ignored

    key = (b"s" * 16, "1")
    assert t.begin(key) and not t.begin(key)
    reply = {"kind": "response", "request_id": "1", "output": {}}
    t.finish(key, reply)
    assert t.cached(key) is reply and t.begin(key)
    t.finish((b"s" * 16, "2"), reply)
    assert t.cached(key) is None  # evicted: cache_size=1


def test_duplicate_capabilities_are_rejected():
    with pytest.raises(ValueError, match="two skills"):
        make("X", [Echo(), Echo()], MemoryHub().transport())


def test_card_is_built_from_skills_and_reasoner():
    agent = make("Server", [Echo(), Private()], MemoryHub().transport())
    agent.reasoner.model = "qwen2.5:3b"
    doc = agent.card().to_dict()
    assert doc["name"] == "Server" and doc["llm"] == {"model": "qwen2.5:3b"}
    assert doc["skills"] == [
        {"id": "echo", "name": "echo", "input_schema": {"type": "object"}},
        {"id": "private", "name": "private", "min_trust": 1},
    ]


# ---------------------------------------------------------------- end to end


async def test_clothing_agent_finds_forecaster_asks_and_acts():
    hub = MemoryHub()
    weather = make("Weather Forecaster", weather_skills(), hub.transport())
    clothing = make("Clothing Advisor", clothing_skills(), hub.transport())
    goal = goal_of(clothing)
    await running(weather, clothing)
    try:
        await asyncio.wait_for(goal.done.wait(), 2)
    finally:
        await stopped(clothing, weather)

    assert goal.result["asked"] == {
        "uid": weather.agent_id.hex(), "name": "Weather Forecaster",
        "capability": "weather_forecast", "question": "What is the weather tomorrow?",
    }
    assert "snowy" in goal.result["answer"]
    assert "coat" in goal.result["outcome"]
    assert clothing.agent_id in weather.node.peers and weather.agent_id in clothing.node.peers


async def test_agent_without_useful_peer_does_nothing():
    hub = MemoryHub()
    music = make("Music Player", [Echo()], hub.transport())
    clothing = make("Clothing Advisor", clothing_skills(), hub.transport())
    await running(music, clothing)
    await asyncio.sleep(0.3)
    await stopped(clothing, music)
    assert not goal_of(clothing).done.is_set()
    assert music.agent_id in clothing.node.peers


async def test_ask_dispatches_on_capability_and_reports_errors():
    hub = MemoryHub()
    server = make("Server", [Echo(), Broken(), Private()], hub.transport())
    client = make("Client", [], hub.transport())
    await running(server, client)
    try:
        peer = await peer_of(client, server)
        assert peer.capabilities == ["echo", "broken", "private"]
        assert await client.ask(peer, "echo", {"n": 1}) == {"echo": {"n": 1}}
        with pytest.raises(RequestError, match="skill_failed: RuntimeError: kaboom"):
            await client.ask(peer, "broken", {})
        with pytest.raises(RequestError, match="no_such_capability"):
            await client.ask(peer, "music", {})
        # Trust is enforced at dispatch: the client is only "heard" (level 0).
        with pytest.raises(RequestError, match="insufficient_trust"):
            await client.ask(peer, "private", {})
        server.node.peers.get(client.agent_id).trust = Trust.VERIFIED
        assert await client.ask(peer, "private", {}) == {"secret": 42}
    finally:
        await stopped(client, server)


async def test_retried_request_gets_the_same_cached_answer():
    hub = MemoryHub()
    reasoner = ScriptedReasoner()
    weather = make("Weather Forecaster", weather_skills(), hub.transport(), reasoner=reasoner)
    asker = make("Asker", [], hub.transport())
    await running(weather, asker)
    try:
        peer = await peer_of(asker, weather)
        first = await asker.ask(peer, "weather_forecast", {"question": "Weather?"})
        # Same request_id again, as a retry after a lost response would be.
        dup = {"kind": "request", "request_id": "dup", "capability": "weather_forecast",
               "input": {"question": "Weather?"}}
        replies = []
        asker.node.on_message(lambda p, body: _append(replies, body))
        await asker.node.send(peer, dup)
        await asker.node.send(peer, dup)
        await asyncio.sleep(0.1)
    finally:
        await stopped(asker, weather)
    answers = [b for b in replies if b["kind"] == "response" and b["request_id"] == "dup"]
    assert first["answer"] and len(answers) == 2
    assert len({b["output"]["answer"] for b in answers}) == 1
    assert sum("asks:" in call for call in reasoner.calls) == 2  # "Weather?" once + "dup" once


async def _append(items, body):
    items.append(body)


async def test_over_localhost_multicast():
    port = 47000 + (id(object()) % 900)
    try:
        weather = make("Weather Forecaster", weather_skills(), make_transport("localhost", port=port))
        clothing = make("Clothing Advisor", clothing_skills(), make_transport("localhost", port=port))
        await running(weather, clothing)
    except OSError as exc:
        pytest.skip(f"multicast unavailable here: {exc}")
    goal = goal_of(clothing)
    try:
        await asyncio.wait_for(goal.done.wait(), 5)
    except asyncio.TimeoutError:
        pytest.skip("multicast packets not delivered in this environment")
    finally:
        await stopped(clothing, weather)
    assert goal.result["asked"]["uid"] == weather.agent_id.hex()
