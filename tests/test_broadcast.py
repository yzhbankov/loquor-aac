"""Broadcast protocol: advertise, decide, request by uid, answer, act."""

import asyncio
import json
from pathlib import Path

import pytest

from loquor.broadcast import BroadcastMessage, BroadcastType, LocalBus, make_bus
from loquor.broadcast_agent import AgentProfile, BroadcastAgent
from loquor.reasoning import Reasoner

SECRET = "test-pairing-secret"
EXAMPLES = Path(__file__).parent.parent / "demo" / "weather_and_clothes"

WEATHER = AgentProfile(
    name="Weather Forecaster",
    description="I provide weather forecasts.",
    capabilities=["weather_forecast"],
    system_prompt="You forecast the weather. Random number: {random_number}",
)
CLOTHING = AgentProfile(
    name="Clothing Advisor",
    description="I recommend clothes.",
    capabilities=["clothing_recommendation"],
    system_prompt="You recommend clothes.",
    goal="Recommend what to wear tomorrow.",
)


class ScriptedReasoner(Reasoner):
    """Stands in for an LLM: decides on adverts, forecasts, recommends."""

    name = "scripted"

    def __init__(self):
        self.calls: list[str] = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.calls.append(prompt)
        if schema is not None:  # decision about an advertised agent
            useful = "weather" in prompt.lower()
            return json.dumps(
                {
                    "useful": useful,
                    "reason": "it forecasts weather" if useful else "not relevant",
                    "question": "What is the weather tomorrow?" if useful else "",
                }
            )
        if "asks:" in prompt:  # answering a request
            assert "{random_number}" not in system
            return "Tomorrow: 3 degrees Celsius and snowy."
        return "Wear a warm coat, gloves and boots."


def make(profile, bus, reasoner=None, **kwargs):
    return BroadcastAgent(
        profile,
        reasoner or ScriptedReasoner(),
        pairing_secret=kwargs.pop("secret", SECRET),
        bus=bus,
        advert_interval=0.05,
        request_timeout=1.0,
        **kwargs,
    )


async def test_clothing_agent_finds_forecaster_asks_and_acts():
    bus = LocalBus()
    weather = make(WEATHER, bus.join())
    clothing = make(CLOTHING, bus.join())
    await weather.start()
    await clothing.start()
    try:
        await asyncio.wait_for(clothing.done.wait(), 2)
    finally:
        await clothing.stop()
        await weather.stop()

    result = clothing.result
    assert result["asked"]["uid"] == weather.uid
    assert result["asked"]["question"] == "What is the weather tomorrow?"
    assert "snowy" in result["answer"]
    assert "coat" in result["outcome"]
    # The forecaster has no goal: it only advertised and answered.
    assert weather.result is None
    assert clothing.uid in weather.directory


async def test_agent_without_useful_peer_does_nothing():
    bus = LocalBus()
    other = make(
        AgentProfile("Music Player", "I play music.", ["music"], "You play music."),
        bus.join(),
    )
    clothing = make(CLOTHING, bus.join())
    await other.start()
    await clothing.start()
    await asyncio.sleep(0.3)
    await clothing.stop()
    await other.stop()
    assert not clothing.done.is_set()
    assert other.uid in clothing.directory


async def test_requests_for_other_uids_are_only_overheard():
    bus = LocalBus()
    reasoner = ScriptedReasoner()
    weather = make(WEATHER, bus.join(), reasoner=reasoner)
    observer = make(CLOTHING, bus.join())
    await weather.start()
    observer._evaluated.add(weather.uid)  # keep the observer from asking itself
    await observer.start()
    try:
        # A request addressed to somebody else reaches the weather agent's
        # socket but must not be answered.
        observer._send(BroadcastType.REQUEST, "someone-else", {"request_id": "r1", "question": "?"})
        await asyncio.sleep(0.1)
        assert not any("asks:" in call for call in reasoner.calls)
    finally:
        await observer.stop()
        await weather.stop()


async def test_retried_request_gets_the_same_cached_answer():
    bus = LocalBus()
    reasoner = ScriptedReasoner()
    weather = make(WEATHER, bus.join(), reasoner=reasoner)
    asker = make(CLOTHING, bus.join())
    await weather.start()
    asker._evaluated.add(weather.uid)
    await asker.start()
    try:
        first = await asker.request(weather.uid, "Weather?")
        # Same request_id again, as a retry after a lost response would be.
        replies = []
        bus.hub.members.append(lambda data: replies.append(BroadcastMessage.model_validate_json(data)))
        asker._send(BroadcastType.REQUEST, weather.uid, {"request_id": "dup", "question": "Weather?"})
        asker._send(BroadcastType.REQUEST, weather.uid, {"request_id": "dup", "question": "Weather?"})
        await asyncio.sleep(0.1)
    finally:
        await asker.stop()
        await weather.stop()
    answers = [m for m in replies if m.type is BroadcastType.RESPONSE]
    assert first and len({m.payload["answer"] for m in answers}) == 1
    assert sum("asks:" in call for call in reasoner.calls) == 2  # "Weather?" once + "dup" once


async def test_unauthenticated_agents_are_ignored():
    bus = LocalBus()
    weather = make(WEATHER, bus.join(), secret="other-secret")
    clothing = make(CLOTHING, bus.join())
    await weather.start()
    await clothing.start()
    await asyncio.sleep(0.2)
    await clothing.stop()
    await weather.stop()
    assert weather.uid not in clothing.directory
    assert not clothing.done.is_set()


async def test_over_localhost_multicast():
    port = 47000 + (id(object()) % 900)
    try:
        weather = make(WEATHER, make_bus("localhost", port=port))
        clothing = make(CLOTHING, make_bus("localhost", port=port))
        await weather.start()
        await clothing.start()
    except OSError as exc:
        pytest.skip(f"multicast unavailable here: {exc}")
    try:
        await asyncio.wait_for(clothing.done.wait(), 5)
    except asyncio.TimeoutError:
        pytest.skip("multicast packets not delivered in this environment")
    finally:
        await clothing.stop()
        await weather.stop()
    assert clothing.result["asked"]["uid"] == weather.uid


def test_transport_selection():
    assert make_bus("localhost").interface == "127.0.0.1"
    with pytest.raises(NotImplementedError):
        make_bus("bluetooth")
    with pytest.raises(ValueError):
        make_bus("carrier-pigeon")


def test_profile_files(tmp_path):
    for path in EXAMPLES.glob("*.json"):
        profile = AgentProfile.from_file(path)
        assert profile.capabilities and profile.system_prompt

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "X", "descripton": "typo"}))
    with pytest.raises(ValueError, match="unknown fields \\['descripton'\\]"):
        AgentProfile.from_file(bad)
