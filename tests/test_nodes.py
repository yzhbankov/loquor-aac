"""Nodes (no LLM) and the collect skill that pulls their readings."""

import asyncio
import json
from pathlib import Path

import pytest

from loquor import Agent, Identity, Reasoner, Trigger
from loquor.agent.skills import Collect, SensorReport
from loquor.config import build_agent, load_profile
from loquor.reasoning import NoReasoner
from loquor.sensors import FunctionSensor
from loquor.settings import Settings
from loquor.tools import scaffold
from loquor.tools.check import check_profile
from loquor.tools.doctor import run_checks
from loquor.tools.status import snapshot
from loquor.transports import MemoryHub

DEMO = Path(__file__).parent.parent / "demo" / "greenhouse"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_MODEL", raising=False)
    return tmp_path / "home"


class Controller(Reasoner):
    """Scripted brain: waters when any node reports soil moisture under 30."""

    name = "scripted"

    def __init__(self):
        self.prompts = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.prompts.append(prompt)
        dry = "soil_moisture=22" in prompt
        if dry:
            return json.dumps({"trigger": "pump", "input": {"seconds": 30}, "reason": "soil_moisture=22 is below 30 %"})
        return json.dumps({"trigger": None, "input": {}, "reason": "all readings within limits"})


class Pump(Trigger):
    name, description = "pump", "Water the beds."
    input_schema = {"type": "object", "properties": {"seconds": {"type": "integer"}}, "required": ["seconds"]}

    def __init__(self):
        self.runs = []

    async def fire(self, input):
        self.runs.append(input["seconds"])
        return {"ok": True}


def node(name, hub, **values):
    sensors = [FunctionSensor(lambda v=v: v, name=k, unit="%") for k, v in values.items()]
    return Agent(name=name, description=f"{name} measures things.", skills=[SensorReport()], reasoner=NoReasoner(),
                 transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05, sensors=sensors)


# ----------------------------------------------------------------- profiles


def test_a_profile_of_sensor_report_only_is_a_node_without_llm():
    agent = build_agent({"name": "N", "description": "d", "skills": [{"type": "sensor_report"}]},
                        identity=Identity.generate(), model="qwen2.5:3b")  # --model is ignored for a node
    assert isinstance(agent.reasoner, NoReasoner) and agent.card().llm is None
    explicit = build_agent({"name": "N", "description": "d", "reasoner": {"type": "none"},
                            "skills": [{"type": "sensor_report"}]}, identity=Identity.generate())
    assert isinstance(explicit.reasoner, NoReasoner)
    with pytest.raises(ValueError, match='reasoner "none" but LlmAnswer need an LLM'):
        build_agent({"name": "N", "description": "d", "reasoner": {"type": "none"},
                     "skills": [{"type": "llm_answer", "capability": "c", "system_prompt": "s"}]},
                    identity=Identity.generate())
    # collect without a goal needs no LLM either; with one it does
    only_collect = build_agent({"name": "C", "description": "d", "skills": [{"type": "collect"}]}, identity=Identity.generate())
    assert isinstance(only_collect.reasoner, NoReasoner)
    thinking = build_agent({"name": "C", "description": "d", "skills": [{"type": "collect", "goal": "g"}]},
                           identity=Identity.generate(), settings=Settings(model="m"))
    assert thinking.reasoner.model == "m"


async def test_no_reasoner_fails_loudly_if_a_skill_calls_it():
    with pytest.raises(RuntimeError, match="no LLM"):
        await NoReasoner().complete("s", "p")


def test_demo_profiles_build_and_check(home):
    nodes = [build_agent(load_profile(DEMO / f), base_dir=DEMO) for f in ("node_soil.json", "node_air.json", "node_tank.json")]
    assert all(isinstance(n.reasoner, NoReasoner) for n in nodes)
    assert nodes[0].sensors.names() == ["soil_moisture", "soil_temperature"]
    controller = build_agent(load_profile(DEMO / "controller.json"), base_dir=DEMO, settings=Settings(model="m"))
    (collect,) = controller.skills
    assert isinstance(collect, Collect) and collect.triggers == ("vent",) and collect.needs_reasoner
    assert "greenhouse" in collect.system_prompt and controller.triggers.names() == ["vent"]
    # the tank node offers its valve in its card; the controller will fire it remotely
    tank = nodes[2]
    (entry,) = [s for s in tank.card().skills if s["id"] == "fire_trigger"]
    assert list(entry["triggers"]) == ["valve"] and entry["triggers"]["valve"]["input_schema"]["required"] == ["seconds"]


async def test_check_reports_a_node_as_such():
    lines, checks = await check_profile(DEMO / "node_tank.json", Settings(), transport="localhost")
    assert any("role       : node (no LLM)" in line for line in lines)
    reasoner = next(c for c in checks if c.name == "reasoner")
    assert reasoner.state == "ok" and "sensor node" in reasoner.detail and all(c.ok for c in checks)


# -------------------------------------------------------------- the mesh


async def test_controller_collects_from_nodes_and_acts(home):
    hub = MemoryHub()
    soil = node("Soil Node", hub, soil_moisture=22, soil_temperature=21)
    tank = node("Tank Node", hub, water_level=62)
    brain, pump = Controller(), Pump()
    collect = Collect(interval=0.2, goal="keep the plants watered", system_prompt="You run a greenhouse.",
                      triggers=["pump"], min_nodes=2)
    controller = Agent(name="Controller", description="d", skills=[collect], reasoner=brain, transport=hub.transport(),
                       identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0, triggers=[pump])
    for a in (soil, tank, controller):
        await a.start()
    try:
        async def two_nodes():
            while len(collect.collected) < 2 or not pump.runs:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(two_nodes(), 3.0)
        # readings arrived as data, straight from each node, no LLM on the node side
        by_name = {n["name"]: n["readings"] for n in collect.collected.values()}
        assert by_name["Soil Node"]["soil_moisture"]["value"] == 22 and by_name["Tank Node"]["water_level"]["unit"] == "%"
        assert "Soil Node: soil_moisture=22 %" in collect.table()
        # the brain saw the table once per round and watered because the soil was dry
        assert brain.prompts and "Tank Node: water_level=62 %" in brain.prompts[0]
        assert pump.runs[0] == 30 and collect.assessment == "soil_moisture=22 is below 30 %"
        # another agent can ask the controller for the whole table
        asker = node("Asker", hub)
        await asker.start()
        try:
            async def found():
                while not asker.node.peers.find("collected_readings"):
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(found(), 2.0)
            out = await asker.context.ask(asker.node.peers.find("collected_readings")[0], "collected_readings", {})
            assert {n["name"] for n in out["nodes"].values()} == {"Soil Node", "Tank Node"}
        finally:
            await asker.stop()
        # status shows the collected table
        data = snapshot(controller)
        (c,) = data["collected"]
        assert c["rounds"] >= 1 and {n["label"] for n in c["nodes"]} == {soil.uid, tank.uid}
        assert snapshot(soil)["model"] is None and snapshot(soil)["llm"]["calls"] is None
    finally:
        for a in (soil, tank, controller):
            await a.stop()


async def test_collect_without_goal_only_collects_and_filters_peers(home):
    hub = MemoryHub()
    wanted, other = node("Wanted", hub, x=1), node("Other", hub, y=2)
    collect = Collect(interval=0.1, peers=["Wanted"])
    collector = Agent(name="Collector", description="d", skills=[collect], reasoner=NoReasoner(),
                      transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0)
    for a in (wanted, other, collector):
        await a.start()
    try:
        await asyncio.wait_for(collect.collected_event.wait(), 3.0)
        await asyncio.sleep(0.3)
        assert [n["name"] for n in collect.collected.values()] == ["Wanted"] and collect.assessment is None
    finally:
        for a in (wanted, other, collector):
            await a.stop()


# ----------------------------------------------------------- device side


async def test_node_scaffold_doctor_and_settings(tmp_path, home):
    written = scaffold.init(tmp_path / "barn", node=True)
    assert [p.name for p in written] == ["agent.json", "sensors.py", "triggers.py", "README.md"]
    profile = json.loads((tmp_path / "barn" / "agent.json").read_text())
    assert [s["type"] for s in profile["skills"]] == ["sensor_report", "offer_triggers"] and "prompt.txt" not in str(written)
    lines, checks = await check_profile(tmp_path / "barn" / "agent.json", Settings(), transport="localhost")
    assert all(c.ok for c in checks) and any("node (no LLM)" in line for line in lines)

    s = Settings.load()
    s.set("llm", "false")
    s.save()
    assert Settings.load().llm is False
    checks = await run_checks(Settings.load(), transport="localhost")  # no Ollama looked for
    ollama = next(c for c in checks if c.name == "ollama")
    assert ollama.state == "ok" and "sensor node" in ollama.detail
    assert "sensor node, no LLM" in next(c for c in checks if c.name == "device config").detail


# ------------------------------------------------------- remote triggers


class Valve(Trigger):
    name, description = "valve", "Open the irrigation valve for some seconds."
    input_schema = {"type": "object", "properties": {"seconds": {"type": "integer", "minimum": 5}}, "required": ["seconds"]}

    def __init__(self):
        self.opened = []

    async def fire(self, input):
        self.opened.append(input["seconds"])
        return {"opened_for_s": input["seconds"]}


class ValveBrain(Reasoner):
    """Proposes the tank node's valve by its remote name when the soil is dry."""

    name = "scripted"

    def __init__(self):
        self.prompts = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.prompts.append(prompt)
        if "soil_moisture=22" in prompt and "tank-node:valve" in prompt:
            return json.dumps({"trigger": "tank-node:valve", "input": {"seconds": 20}, "reason": "dry soil, water via the tank"})
        return json.dumps({"trigger": None, "input": {}, "reason": "nothing to do"})


async def test_node_offers_a_trigger_and_the_controller_fires_it_remotely(home):
    from loquor.agent.skills import OfferTriggers
    from loquor.triggers import TriggerError

    hub = MemoryHub()
    valve = Valve()
    tank = Agent(name="Tank Node", description="tank", skills=[SensorReport(), OfferTriggers()], reasoner=NoReasoner(),
                 transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05,
                 sensors=[FunctionSensor(lambda: 60, name="water_level", unit="%")], triggers=[valve])
    soil = node("Soil Node", hub, soil_moisture=22)
    brain = ValveBrain()
    collect = Collect(interval=0.2, goal="water when dry", system_prompt="s", min_nodes=2)
    controller = Agent(name="Controller", description="d", skills=[collect], reasoner=brain, transport=hub.transport(),
                       identity=Identity.generate(), beacon_interval=0.05, request_timeout=1.0)
    for a in (tank, soil, controller):
        await a.start()
    try:
        async def watered():
            while not valve.opened:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(watered(), 3.0)
        # the node's valve showed up in the controller's hub under the node's name, with the node's schema
        assert "tank-node:valve" in controller.triggers and collect.remote == {"tank-node:valve": tank.agent_id.hex()}
        remote = controller.triggers.get("tank-node:valve")
        assert remote.description == "Open the irrigation valve for some seconds. (on Tank Node)"
        assert "tank-node:valve" in brain.prompts[-1] and "Open the irrigation valve" in brain.prompts[-1]
        # validated here, sent to the node, validated there, fired there
        assert valve.opened == [20] and tank.triggers.history[-1]["result"] == {"opened_for_s": 20}
        assert controller.triggers.history[-1]["result"] == {"opened_for_s": 20}
        # the node refuses what its schema or its offer does not allow
        with pytest.raises(TriggerError, match="invalid input for tank-node:valve"):  # caught here, never sent
            await controller.triggers.fire("tank-node:valve", {"seconds": 1})
        remote.input_schema = {"type": "object"}  # a lax copy here still meets the node's own check
        with pytest.raises(TriggerError, match="refused valve: .*invalid input"):
            await controller.triggers.fire("tank-node:valve", {"seconds": 1})
        peer = controller.node.peers.get(tank.agent_id)
        with pytest.raises(Exception, match="no such trigger"):
            await controller.context.ask(peer, "fire_trigger", {"trigger": "nuke", "input": {}})
        # a node without offer_triggers advertises nothing
        assert not any(n.startswith("soil-node:") for n in controller.triggers.names())
        # status lists the remote trigger with the local ones
        assert "tank-node:valve" in snapshot(controller)["triggers"]["names"]
    finally:
        for a in (tank, soil, controller):
            await a.stop()


def test_offer_triggers_only_lists_what_it_is_told_to():
    from loquor.agent.skills import OfferTriggers

    agent = Agent(name="N", description="d", skills=[OfferTriggers(triggers=["valve"], min_trust=1)], reasoner=NoReasoner(),
                  transport=MemoryHub().transport(), identity=Identity.generate(), triggers=[Valve(), Pump()])
    (entry,) = agent.card().skills
    assert entry["min_trust"] == 1 and list(entry["triggers"]) == ["valve"]
    with pytest.raises(ValueError, match=r"uses triggers \['lamp'\]"):
        Agent(name="N", description="d", skills=[OfferTriggers(triggers=["lamp"])], reasoner=NoReasoner(),
              transport=MemoryHub().transport(), identity=Identity.generate(), triggers=[Valve()])
