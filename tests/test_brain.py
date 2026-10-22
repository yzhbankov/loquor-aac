"""The simple profile: share and brain; remote sensors read on demand; the brain loop."""

import asyncio
import json

import pytest

from loquor import Agent, Identity, Reasoner, Sensor, Trigger
from loquor.agent.skills import Brain, OfferTriggers, SensorReport
from loquor.config import build_agent, skill_specs
from loquor.reasoning import NoReasoner
from loquor.sensors import FunctionSensor
from loquor.settings import Settings
from loquor.transports import MemoryHub


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_MODEL", raising=False)
    return tmp_path / "home"


async def until(predicate, timeout=3.0):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(wait(), timeout)


# ------------------------------------------------------------ base classes


class Thermo(Sensor):
    name, unit, interval, description = "thermo", "°C", 10, "Air temperature."

    async def read(self):
        return self.reading(21.5)


class Lamp(Trigger):
    name, description = "lamp", "Switch the lamp."
    input_schema = {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}

    async def fire(self, input):
        return {"on": input["on"]}


class PinSensor(Sensor):
    unit = "V"

    def __init__(self, pin: int, **fields):
        super().__init__(**fields)
        self.pin = pin

    async def read(self):
        return self.reading(self.pin / 10)


def test_class_parts_take_profile_overrides_without_an_init(tmp_path):
    (tmp_path / "parts.py").write_text(
        "from loquor import Sensor, Trigger\n"
        "class Thermo(Sensor):\n    name, unit, interval = 'thermo', '°C', 10\n"
        "    async def read(self):\n        return self.reading(20)\n"
        "class Lamp(Trigger):\n    name, description = 'lamp', 'Switch the lamp.'\n"
        "    async def fire(self, input):\n        return {}\n")
    agent = build_agent({"name": "N", "description": "d",
                         "sensors": [{"type": "parts.py:Thermo", "name": "greenhouse_air", "interval": None},
                                     {"type": "parts.py:Thermo"}],
                         "triggers": [{"type": "parts.py:Lamp", "name": "porch", "description": "Porch light."}]},
                        base_dir=tmp_path, identity=Identity.generate())
    air = agent.sensors.get("greenhouse_air")
    assert air.interval is None and air.unit == "°C" and agent.sensors.get("thermo").interval == 10
    assert agent.triggers.get("porch").description == "Porch light." and agent.triggers.names() == ["porch"]
    assert PinSensor(pin=7, name="p", interval=2).interval == 2 and PinSensor(pin=7).name == ""
    with pytest.raises(ValueError, match="invalid sensor"):
        build_agent({"name": "N", "description": "d", "sensors": [{"type": "parts.py:Thermo", "colour": "red"}]},
                    base_dir=tmp_path, identity=Identity.generate())


# ---------------------------------------------------------------- profiles


def test_no_brain_is_a_node_that_shares_everything():
    profile = {"name": "Tank Node", "description": "d",
               "sensors": [{"type": "simulated", "name": "level", "min": 0, "max": 100, "unit": "%", "description": "Water left."}],
               "triggers": [{"type": "console", "name": "valve", "description": "Open the valve."}]}
    assert [s["type"] for s in skill_specs(profile)] == ["sensor_report", "offer_triggers"]
    agent = build_agent(profile, identity=Identity.generate())
    assert isinstance(agent.reasoner, NoReasoner) and agent.capabilities == ["sensor_report", "fire_trigger"]
    # the card says what it measures and what it can do, so an agent knows before asking
    card = {s["id"]: s for s in agent.card().skills}
    assert card["sensor_report"]["sensors"] == {"level": {"unit": "%", "description": "Water left."}}
    assert list(card["fire_trigger"]["triggers"]) == ["valve"]


def test_a_brain_makes_an_agent_that_shares_sensors_not_triggers():
    profile = {"name": "Controller", "description": "d",
               "sensors": [{"type": "simulated", "name": "air", "min": 0, "max": 40}],
               "triggers": [{"type": "console", "name": "vent", "description": "Open the vent."}],
               "brain": {"prompt": "You run a greenhouse.", "goal": "Keep it cool.", "every": 30, "act": ["vent", "tank-node:valve"]}}
    assert [s["type"] for s in skill_specs(profile)] == ["brain", "sensor_report"]
    agent = build_agent(profile, identity=Identity.generate(), settings=Settings(model="m"))
    brain = agent.skills[0]
    assert isinstance(brain, Brain) and brain.system_prompt == "You run a greenhouse." and brain.every == 30
    assert brain.triggers == ("vent",) and brain.act == ("vent", "tank-node:valve")  # the node's is checked at runtime
    assert agent.reasoner.model == "m" and agent.capabilities == ["ask", "sensor_report"]
    # share: false, lists and trust; hand-written skills share nothing implicitly
    assert skill_specs({**profile, "share": False}) == [skill_specs(profile)[0]]
    shared = skill_specs({**profile, "share": {"sensors": ["air"], "triggers": True, "min_trust": 1}})
    assert shared[1:] == [{"type": "sensor_report", "min_trust": 1, "sensors": ["air"]},
                          {"type": "offer_triggers", "min_trust": 1}]
    assert skill_specs({"name": "X", "description": "d", "sensors": [{}], "skills": [{"type": "goal"}]}) == [{"type": "goal"}]
    with pytest.raises(ValueError, match=r"uses triggers \['door'\]"):
        build_agent({**profile, "brain": {"goal": "g", "act": ["door"]}}, identity=Identity.generate())


# ------------------------------------------------------------- the mesh


class Counting(Sensor):
    """A node's sensor that counts how often it is actually read."""

    def __init__(self, name, value, unit="%"):
        super().__init__(name=name, unit=unit, description=f"{name} in the bed")
        self.value, self.reads = value, 0

    async def read(self):
        self.reads += 1
        return self.reading(self.value)


def node(name, hub, sensors=(), triggers=()):
    skills = [SensorReport()] + ([OfferTriggers()] if triggers else [])
    return Agent(name=name, description=f"{name}: a sensor node.", skills=skills, reasoner=NoReasoner(),
                 transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05,
                 sensors=list(sensors), triggers=list(triggers))


class Scripted(Reasoner):
    """Answers from what is in the prompt; proposes the valve when soil is dry."""

    name = "scripted"

    def __init__(self):
        self.calls = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.calls.append((system, prompt))
        if prompt.startswith("Another agent asks"):
            return "Soil is at 22 %." if "soil-node:moisture: 22 %" in system else "I do not know."
        if "advertised itself" in prompt:
            return json.dumps({"useful": True, "reason": "it knows the weather", "capability": "ask",
                               "question": "What is the weather tomorrow?"})
        if schema and "trigger" in schema.get("properties", {}):
            if "soil-node:moisture: 22 %" in prompt and "tank-node:valve" in prompt:
                return json.dumps({"trigger": "tank-node:valve", "input": {"seconds": 20}, "reason": "dry soil"})
            return json.dumps({"trigger": None, "input": {}, "reason": "all fine"})
        return "Wear a coat: it will rain." if "It answered: Rain" in prompt else "no idea"


class Valve(Trigger):
    name, description = "valve", "Open the irrigation valve."
    input_schema = {"type": "object", "properties": {"seconds": {"type": "integer", "minimum": 5}}, "required": ["seconds"]}

    def __init__(self):
        self.opened = []

    async def fire(self, input):
        self.opened.append(input["seconds"])
        return {"opened_for_s": input["seconds"]}


async def test_an_agent_reads_a_node_only_when_asked():
    hub = MemoryHub()
    moisture = Counting("moisture", 22)
    soil = node("Soil Node", hub, sensors=[moisture])
    brain = Brain(system_prompt="You watch the garden.")
    agent = Agent(name="Gardener", description="d", skills=[brain], reasoner=Scripted(), transport=hub.transport(),
                  identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0)
    asker = node("Asker", hub)
    for a in (soil, agent, asker):
        await a.start()
    try:
        await until(lambda: "soil-node:moisture" in agent.sensors and asker.node.peers.find("ask"))
        await asyncio.sleep(0.3)
        assert moisture.reads == 0  # mirrored, not polled: nobody needed it yet
        remote = agent.sensors.get("soil-node:moisture")
        assert remote.unit == "%" and remote.description == "moisture in the bed (on Soil Node)"
        out = await asker.context.ask(asker.node.peers.find("ask")[0], "ask", {"question": "How wet is the soil?"})
        assert out == {"answer": "Soil is at 22 %."} and moisture.reads == 1
        # a node's readings are reused for a few seconds: one request serves several reads
        await agent.sensors.readings(["soil-node:moisture", "soil-node:moisture"])
        assert moisture.reads == 1
        # a name that is not in range reads as unavailable, it does not fail
        (gone,) = await agent.sensors.readings(["barn-node:moisture"])
        assert gone.error == "not in range"
        # a node shares only its own sensors, never ones it would mirror
        assert soil.mirror is False and asker.sensors.names() == []
    finally:
        for a in (soil, agent, asker):
            await a.stop()


async def test_a_brain_with_a_goal_reads_nodes_and_fires_a_nodes_trigger():
    hub = MemoryHub()
    valve = Valve()
    soil = node("Soil Node", hub, sensors=[Counting("moisture", 22)])
    tank = node("Tank Node", hub, sensors=[Counting("level", 60)], triggers=[valve])
    reasoner = Scripted()
    brain = Brain(goal="Keep the soil above 30 %.", system_prompt="You run a greenhouse.", every=0.2, min_peers=2)
    controller = Agent(name="Controller", description="d", skills=[brain], reasoner=reasoner, transport=hub.transport(),
                       identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0)
    for a in (soil, tank, controller):
        await a.start()
    try:
        await until(lambda: valve.opened)
        assert valve.opened == [20] and brain.result["triggered"]["result"] == {"opened_for_s": 20}
        prompt = reasoner.calls[-1][1]
        assert "Sensors on devices nearby" in prompt and "tank-node:level: 60 %" in prompt
        # nodes cost no LLM call to consider: only the decisions about acting were made
        assert not any("advertised itself" in p for _, p in reasoner.calls) and not brain.decisions
        await until(lambda: brain.rounds >= 2)  # every 0.2 s, again
        assert not brain.done.is_set()
        # a peer that goes silent takes its sensors and triggers with it
        await tank.stop()
        await until(lambda: "tank-node:valve" not in controller.triggers and "tank-node:level" not in controller.sensors)
        assert "soil-node:moisture" in controller.sensors
    finally:
        for a in (soil, controller):
            await a.stop()


async def test_a_brain_asks_an_agent_once_and_completes_its_goal():
    hub = MemoryHub()

    class Forecaster(Reasoner):
        name = "forecaster"

        async def complete(self, system, prompt, *, temperature=None, schema=None):
            return "Rain, 9 °C."

    weather = Agent(name="Weather", description="I forecast the weather.", skills=[Brain()], reasoner=Forecaster(),
                    transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05)
    reasoner = Scripted()
    brain = Brain(goal="Recommend what to wear tomorrow.", act=[])
    advisor = Agent(name="Advisor", description="d", skills=[brain], reasoner=reasoner, transport=hub.transport(),
                    identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0)
    for a in (weather, advisor):
        await a.start()
    try:
        await asyncio.wait_for(brain.done.wait(), 3.0)
        (answer,) = brain.answers.values()
        assert answer["name"] == "Weather" and answer["answer"] == "Rain, 9 °C."
        assert brain.result["outcome"] == "Wear a coat: it will rain." and brain.result["triggered"] is None
        assert brain.rounds == 1
    finally:
        for a in (weather, advisor):
            await a.stop()


async def test_peers_filter_and_explicit_sensor_list():
    hub = MemoryHub()
    wanted, other = node("Wanted", hub, sensors=[Counting("x", 1)]), node("Other", hub, sensors=[Counting("y", 2)])
    # an agent that shares a sensor too: it is asked, not read, unless its sensor is named
    chatty = Agent(name="Chatty", description="d", skills=[Brain(), SensorReport()], reasoner=Scripted(),
                   transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05,
                   sensors=[FunctionSensor(lambda: 9, name="z")])
    brain = Brain(goal="g", peers=["Wanted", "Chatty"], act=[])
    agent = Agent(name="A", description="d", skills=[brain], reasoner=Scripted(), transport=hub.transport(),
                  identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0,
                  sensors=[FunctionSensor(lambda: 5, name="own")])
    for a in (wanted, other, chatty, agent):
        await a.start()
    try:
        await until(lambda: all(n in agent.sensors for n in ("wanted:x", "other:y", "chatty:z")))
        ctx = agent.context
        assert brain.sensor_names(ctx) == ["own", "wanted:x"]
        assert Brain(sensors=["own", "other:y", "chatty:z"]).sensor_names(ctx) == ["own", "other:y", "chatty:z"]
    finally:
        for a in (wanted, other, chatty, agent):
            await a.stop()
