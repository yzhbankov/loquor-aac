"""The orchestrator: advertise, track peers, dispatch to skills, ask with retries."""

import asyncio
import json

import pytest

from loquor.agent import Agent, PeerDirectory, RequestError, RequestTracker, Skill
from loquor.agent.skills import Goal, LlmAnswer
from loquor.protocol import MessageType, decode, request
from loquor.reasoning import Reasoner
from loquor.transports import MemoryHub, make_transport

SECRET = "test-pairing-secret"
WEATHER_PROMPT = "You forecast the weather. Random number: {random_number}"
CLOTHES_PROMPT = "You recommend clothes."
GOAL = "Recommend what to wear tomorrow."


class ScriptedReasoner(Reasoner):
    """Stands in for an LLM: decides on adverts, forecasts, recommends."""

    name = "scripted"

    def __init__(self):
        self.calls: list[str] = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.calls.append(prompt)
        if schema is not None:  # decision about an advertised agent
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

    async def handle(self, input, ctx):
        return {"echo": input}


class Broken(Skill):
    capability = "broken"

    async def handle(self, input, ctx):
        raise RuntimeError("kaboom")


def weather_skills():
    return [LlmAnswer("weather_forecast", WEATHER_PROMPT)]


def clothing_skills():
    return [LlmAnswer("clothing_recommendation", CLOTHES_PROMPT), Goal(GOAL, CLOTHES_PROMPT)]


def make(name, skills, transport, reasoner=None, secret=SECRET, **kwargs):
    return Agent(
        name=name,
        description=f"I am {name}.",
        skills=skills,
        reasoner=reasoner or ScriptedReasoner(),
        transport=transport,
        pairing_secret=secret,
        advert_interval=0.05,
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


# ------------------------------------------------------------- unit pieces


def test_directory_tracks_and_prunes():
    d = PeerDirectory(ttl=0.0)
    assert d.update("w-1", "Weather", "I forecast.", ["weather_forecast"], address=1)
    assert not d.update("w-1", "Weather", "I forecast.", ["weather_forecast"], address=2)
    assert d.get("w-1").address == 2 and "w-1" in d and len(d) == 1
    assert [p.uid for p in d.find("weather_forecast")] == ["w-1"]
    assert d.find("music") == []
    assert [p.uid for p in d.prune()] == ["w-1"] and len(d) == 0


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

    assert t.begin(("s", "1")) and not t.begin(("s", "1"))
    reply = request("a", "A", "s", "1", "cap", {})
    t.finish(("s", "1"), reply)
    assert t.cached(("s", "1")) is reply and t.begin(("s", "1"))
    t.finish(("s", "2"), reply)
    assert t.cached(("s", "1")) is None  # evicted: cache_size=1


def test_duplicate_capabilities_are_rejected():
    with pytest.raises(ValueError, match="two skills"):
        make("X", [Echo(), Echo()], MemoryHub().transport())


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
        "uid": weather.uid, "name": "Weather Forecaster",
        "capability": "weather_forecast", "question": "What is the weather tomorrow?",
    }
    assert "snowy" in goal.result["answer"]
    assert "coat" in goal.result["outcome"]
    assert clothing.uid in weather.directory and weather.uid in clothing.directory


async def test_agent_without_useful_peer_does_nothing():
    hub = MemoryHub()
    music = make("Music Player", [Echo()], hub.transport())
    clothing = make("Clothing Advisor", clothing_skills(), hub.transport())
    await running(music, clothing)
    await asyncio.sleep(0.3)
    await stopped(clothing, music)
    assert not goal_of(clothing).done.is_set()
    assert music.uid in clothing.directory


async def test_ask_dispatches_on_capability_and_reports_errors():
    hub = MemoryHub()
    server = make("Server", [Echo(), Broken()], hub.transport())
    client = make("Client", [], hub.transport())
    await running(server, client)
    try:
        await asyncio.sleep(0.1)  # hear the advert
        peer = client.directory.get(server.uid)
        assert peer.capabilities == ["echo", "broken"]
        assert await client.ask(peer, "echo", {"n": 1}) == {"echo": {"n": 1}}
        with pytest.raises(RequestError, match="skill_failed: RuntimeError: kaboom"):
            await client.ask(peer, "broken", {})
        with pytest.raises(RequestError, match="no_such_capability"):
            await client.ask(peer, "music", {})
    finally:
        await stopped(client, server)


async def test_requests_for_other_uids_are_only_overheard():
    hub = MemoryHub()
    reasoner = ScriptedReasoner()
    weather = make("Weather Forecaster", weather_skills(), hub.transport(), reasoner=reasoner)
    other = make("Other", [], hub.transport())
    await running(weather, other)
    try:
        # Broadcast a request addressed to somebody else: the forecaster
        # hears it but must not answer.
        msg = request(other.uid, "Other", "someone-else", "r1", "weather_forecast", {"question": "?"})
        await other._send(msg)
        await asyncio.sleep(0.1)
        assert not any("asks:" in call for call in reasoner.calls)
    finally:
        await stopped(other, weather)


async def test_retried_request_gets_the_same_cached_answer():
    hub = MemoryHub()
    reasoner = ScriptedReasoner()
    weather = make("Weather Forecaster", weather_skills(), hub.transport(), reasoner=reasoner)
    asker = make("Asker", [], hub.transport())
    replies = []
    hub.taps.append(lambda data: replies.append(decode(data)))
    await running(weather, asker)
    try:
        await asyncio.sleep(0.1)
        peer = asker.directory.get(weather.uid)
        first = await asker.ask(peer, "weather_forecast", {"question": "Weather?"})
        # Same request_id again, as a retry after a lost response would be.
        dup = request(asker.uid, "Asker", weather.uid, "dup", "weather_forecast", {"question": "Weather?"})
        await asker._send(dup, to=peer.address)
        await asker._send(dup.model_copy(update={"msg_id": "second-copy"}), to=peer.address)
        await asyncio.sleep(0.1)
    finally:
        await stopped(asker, weather)
    answers = [m for m in replies if m.type is MessageType.RESPONSE and m.request_id == "dup"]
    assert first["answer"] and len(answers) == 2
    assert len({m.payload["output"]["answer"] for m in answers}) == 1
    assert sum("asks:" in call for call in reasoner.calls) == 2  # "Weather?" once + "dup" once


async def test_unauthenticated_agents_are_ignored():
    hub = MemoryHub()
    weather = make("Weather Forecaster", weather_skills(), hub.transport(), secret="other-secret")
    clothing = make("Clothing Advisor", clothing_skills(), hub.transport())
    await running(weather, clothing)
    await asyncio.sleep(0.2)
    await stopped(clothing, weather)
    assert weather.uid not in clothing.directory
    assert not goal_of(clothing).done.is_set()


async def test_peers_are_forgotten_when_silent():
    hub = MemoryHub()
    weather = make("Weather Forecaster", weather_skills(), hub.transport())
    watcher = make("Watcher", [], hub.transport())
    await running(weather, watcher)
    await asyncio.sleep(0.1)
    assert weather.uid in watcher.directory
    await weather.stop()
    await asyncio.sleep(0.3)  # > 3 advert intervals
    await watcher.stop()
    assert weather.uid not in watcher.directory


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
    assert goal.result["asked"]["uid"] == weather.uid
