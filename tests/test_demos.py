"""The demo profiles, as written, end to end over the memory transport with a scripted LLM."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from loquor import Reasoner
from loquor.config import build_agent, load_profile
from loquor.tools.ask import ask
from loquor.transports import MemoryHub

DEMO = Path(__file__).parent.parent / "demo"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))


class Scripted(Reasoner):
    """Reads the numbers in its prompt like a careful small model would."""

    name = "scripted"

    def __init__(self):
        self.calls = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.calls.append((system, prompt))
        if prompt.startswith("Another agent asks"):
            m = re.search(r"soil_moisture: ([\d.]+) %", system)
            return f"Soil moisture is {m.group(1)} %." if m else "I cannot see the beds."
        if schema and "trigger" in schema.get("properties", {}):
            soil = re.search(r"soil-node:soil_moisture: ([\d.]+) %", prompt)
            if soil and float(soil.group(1)) < 30 and "tank-node:valve" in prompt:
                return json.dumps({"trigger": "tank-node:valve", "input": {"seconds": 30}, "reason": "soil below 30 %"})
            weather = re.search(r"weather-station:weather: (temperature_c=[^(]+)", prompt)
            if weather and "show_outfit" in prompt:
                return json.dumps({"trigger": "show_outfit", "reason": "final",
                                   "input": {"forecast": weather.group(1).strip(), "items": ["a jacket"]}})
            return json.dumps({"trigger": None, "input": {}, "reason": "nothing to do"})
        return "ok"


def demo_agents(monkeypatch, hub, *paths):
    """Build demo profiles as `loquor run` does, on the memory hub, with fast beacons and the scripted LLM."""
    monkeypatch.setattr("loquor.config._build_transport", lambda spec, base_dir: hub.transport())
    brain = Scripted()
    agents = [build_agent(load_profile(DEMO / p), base_dir=(DEMO / p).parent, beacon_interval=0.05,
                          wrap_reasoner=lambda r: r if r.name == "none" else brain) for p in paths]
    for a in agents:
        a.request_timeout = 2.0
    return agents, brain


async def test_garden_reads_the_node_only_when_asked(monkeypatch):
    hub = MemoryHub()
    (node, gardener), brain = demo_agents(monkeypatch, hub, "garden/soil_node.json", "garden/gardener.json")
    for a in (node, gardener):
        await a.start()
    try:
        async def mirrored():
            while "soil-node:soil_moisture" not in gardener.sensors:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(mirrored(), 3)
        assert not brain.calls  # nothing is read or thought until someone asks
        got = await ask("Gardener", "Do the beds need water?", hub.transport(), timeout=3, beacon_interval=0.05)
        assert got["capability"] == "ask" and got["output"]["answer"].startswith("Soil moisture is ")
        raw = await ask("soil node", None, hub.transport(), timeout=3, beacon_interval=0.05)
        assert set(raw["output"]["readings"]) == {"soil_moisture", "soil_temperature"}
        with pytest.raises(LookupError, match="takes no questions"):
            await ask("Soil Node", "hello?", hub.transport(), timeout=3, beacon_interval=0.05)
        with pytest.raises(TimeoutError, match="'Nobody' not heard"):
            await ask("Nobody", "hi", hub.transport(), timeout=0.3, beacon_interval=0.05)
    finally:
        for a in (node, gardener):
            await a.stop()


async def test_greenhouse_waters_through_the_tank_nodes_valve(monkeypatch, capsys):
    hub = MemoryHub()
    agents, brain = demo_agents(monkeypatch, hub, "greenhouse/node_soil.json", "greenhouse/node_air.json",
                                "greenhouse/node_tank.json", "greenhouse/controller.json")
    controller = agents[-1]
    controller.skills[0].every = 0.2
    for a in agents:
        await a.start()
    try:
        async def watered():
            while not any(h["trigger"] == "tank-node:valve" for h in controller.triggers.history):
                await asyncio.sleep(0.02)
        await asyncio.wait_for(watered(), 5)
        assert "VALVE OPEN for 30 s" in capsys.readouterr().out  # printed by the tank node's console trigger
        prompt = brain.calls[-1][1]
        assert all(s in prompt for s in ("soil-node:soil_moisture", "air-node:air_temperature", "tank-node:water_level"))
    finally:
        for a in agents:
            await a.stop()


async def test_weather_station_node_and_clothing_advisor(monkeypatch, capsys):
    hub = MemoryHub()
    (station, advisor), _ = demo_agents(monkeypatch, hub, "weather_and_clothes/weather_station.json",
                                        "weather_and_clothes/clothing_agent.json")
    brain = advisor.skills[0]
    for a in (station, advisor):
        await a.start()
    try:
        await asyncio.wait_for(brain.done.wait(), 5)
        assert brain.result["triggered"]["trigger"] == "show_outfit" and "OUTFIT FOR TODAY" in capsys.readouterr().out
        assert not brain.decisions  # the station is a node: read, never asked
    finally:
        for a in (station, advisor):
            await a.stop()
