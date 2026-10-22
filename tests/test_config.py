"""Profiles: built-in types by name, custom types by module:Class or file.py:Class."""

import json
from pathlib import Path

import pytest

from loquor.agent import Skill
from loquor.agent.skills import Brain
from loquor.config import build_agent, load_profile, resolve_type
from loquor.protocol import Identity, Visibility
from loquor.reasoning import NoReasoner, OllamaReasoner, TemplateReasoner
from loquor.sensors import FunctionSensor
from loquor.transports import MulticastTransport
from loquor.triggers import FunctionTrigger

DEMO = Path(__file__).parent.parent / "demo" / "weather_and_clothes"


class EchoSkill(Skill):
    capability = "echo"

    def __init__(self, prefix: str = ""):
        self.prefix = prefix

    async def handle(self, input, ctx):
        return {"echo": self.prefix + str(input)}


def read_fake():
    """A fake thermometer."""
    return 21


async def beep(input):
    return {"beeped": True}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_demo_profiles_build(home):
    station = build_agent(load_profile(DEMO / "weather_station.json"), base_dir=DEMO)
    assert station.capabilities == ["sensor_report"] and isinstance(station.reasoner, NoReasoner)  # a node
    assert isinstance(station.transport, MulticastTransport) and station.transport.interface == "127.0.0.1"
    assert station.sensors.names() == ["weather"] and station.sensors.get("weather").interval == 5
    assert (home / "keys" / "weather-station.key").exists()
    # The same profile gets the same identity next time.
    assert build_agent(load_profile(DEMO / "weather_station.json"), base_dir=DEMO).agent_id == station.agent_id

    clothing = build_agent(load_profile(DEMO / "clothing_agent.json"), base_dir=DEMO)
    (brain,) = clothing.skills  # no sensors: nothing to share
    assert isinstance(brain, Brain) and brain.act == ("show_outfit",) and brain.every is None
    assert isinstance(clothing.reasoner, OllamaReasoner) and clothing.reasoner.model == "qwen2.5:3b"  # device default
    assert clothing.triggers.names() == ["show_outfit"] and clothing.capabilities == ["ask"]
    assert "forecast" in clothing.triggers.get("show_outfit").input_schema["properties"]
    assert "Clothing Advisor" in brain.system_prompt  # read from prompt_file
    assert clothing.agent_id != station.agent_id


def test_command_line_overrides(tmp_path):
    profile = load_profile(DEMO / "clothing_agent.json")
    identity = Identity.generate()
    agent = build_agent(profile, base_dir=DEMO, model="llama3.1:8b", transport="localhost",
                        visibility="quiet", identity=identity)
    assert agent.reasoner.model == "llama3.1:8b"
    assert agent.node.visibility is Visibility.QUIET and agent.identity is identity


def test_profile_validation(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "X", "descripton": "typo", "skills": [{"type": "goal"}]}))
    with pytest.raises(ValueError, match=r"unknown fields \['descripton'\]; missing fields \['description'\]"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": []}))
    with pytest.raises(ValueError, match="it does nothing"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"type": "goal"}], "sensors": {}}))
    with pytest.raises(ValueError, match="sensors must be a list"):
        load_profile(bad)
    # the JSON schema catches wrong types and values with a path to them
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"type": "goal"}], "visibility": "loud"}))
    with pytest.raises(ValueError, match="visibility: 'loud' is not one of"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"capability": "c"}]}))
    with pytest.raises(ValueError, match="skills/0: 'type' is a required property"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"type": "goal"}], "beacon_interval": 0}))
    with pytest.raises(ValueError, match="beacon_interval"):
        load_profile(bad)
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_profile(bad)
    with pytest.raises(ValueError, match="does not exist"):
        load_profile(tmp_path / "missing.json")
    # every demo profile matches the schema, "$schema" included, and builds
    for demo in DEMO.parent.glob("*/*.json"):
        profile = load_profile(demo)
        assert "$schema" not in profile and "skills" not in profile  # demos use the simple form
        build_agent(profile, base_dir=demo.parent, identity=Identity.generate())


def test_prompt_files_and_profile_level_beacon_interval(tmp_path):
    (tmp_path / "persona.txt").write_text("You are the station.\n")
    (tmp_path / "goal.txt").write_text("Pursue it.")
    profile_path = tmp_path / "agent.json"
    profile_path.write_text(json.dumps({
        "name": "Files", "description": "d", "system_prompt_file": "persona.txt", "beacon_interval": 2.5,
        "reasoner": {"type": "template"},
        "skills": [{"type": "llm_answer", "capability": "c"},
                   {"type": "goal", "goal": "g", "system_prompt_file": "goal.txt"}],
    }))
    profile = load_profile(profile_path)
    assert profile["system_prompt"] == "You are the station." and "system_prompt_file" not in profile
    agent = build_agent(profile, base_dir=tmp_path, identity=Identity.generate())
    assert agent.skills[0].system_prompt == "You are the station." and agent.skills[1].system_prompt == "Pursue it."
    assert agent.node.beacon_interval == 2.5
    profile_path.write_text(json.dumps({"name": "F", "description": "d", "system_prompt_file": "nope.txt",
                                        "skills": [{"type": "sensor_report"}]}))
    with pytest.raises(ValueError, match="cannot read system_prompt_file 'nope.txt'"):
        load_profile(profile_path)


def test_custom_types_functions_and_bad_arguments(tmp_path):
    profile = {
        "name": "Custom",
        "description": "d",
        "key_file": str(tmp_path / "custom.key"),
        "visibility": "silent",
        "reasoner": {"type": "template"},
        "sensors": [{"type": "test_config:read_fake", "name": "temp", "unit": "°C", "interval": 2}],
        "triggers": [{"type": "test_config:beep", "name": "beep", "description": "Beep."}],
        "skills": [{"type": "test_config:EchoSkill", "prefix": "> "}],
    }
    agent = build_agent(profile)
    assert isinstance(agent.reasoner, TemplateReasoner)
    assert isinstance(agent.skills[0], EchoSkill) and agent.skills[0].prefix == "> "
    assert agent.node.visibility is Visibility.SILENT and (tmp_path / "custom.key").exists()
    sensor = agent.sensors.get("temp")
    assert isinstance(sensor, FunctionSensor) and sensor.description == "A fake thermometer." and sensor.interval == 2
    assert isinstance(agent.triggers.get("beep"), FunctionTrigger)

    with pytest.raises(ValueError, match="invalid skill 'llm_answer'"):
        build_agent({**profile, "skills": [{"type": "llm_answer", "nope": 1}]})
    with pytest.raises(ValueError, match="invalid sensor 'test_config:read_fake'"):
        build_agent({**profile, "sensors": [{"type": "test_config:read_fake", "name": "t", "bogus": 1}]})
    with pytest.raises(ValueError, match="is not a class"):
        build_agent({**profile, "skills": [{"type": "test_config:read_fake"}]})
    with pytest.raises(ValueError, match="unknown skill type 'telepathy'"):
        resolve_type("telepathy", {}, "skill")
    with pytest.raises(ValueError, match="cannot load skill"):
        resolve_type("no.such.module:Thing", {}, "skill")
    with pytest.raises(ValueError, match='needs a "type"'):
        build_agent({**profile, "skills": [{"capability": "x"}]})
    with pytest.raises(ValueError, match=r"uses sensors \['nope'\]"):
        build_agent({**profile, "skills": [{"type": "llm_answer", "capability": "c", "system_prompt": "s",
                                            "sensors": ["nope"]}]})


def test_file_types_resolve_relative_to_the_profile(tmp_path):
    (tmp_path / "drivers.py").write_text(
        "from loquor.sensors import Sensor\n"
        "class Uart(Sensor):\n"
        "    def __init__(self, name='uart', port='/dev/ttyUSB0'):\n"
        "        self.name, self.port = name, port\n"
        "    async def read(self):\n"
        "        return self.reading(42)\n"
    )
    profile_path = tmp_path / "agent.json"
    profile_path.write_text(json.dumps({
        "name": "Dev", "description": "d", "reasoner": {"type": "template"},
        "sensors": [{"type": "drivers.py:Uart", "port": "COM3"}],
        "skills": [{"type": "sensor_report"}],
    }))
    agent = build_agent(load_profile(profile_path), base_dir=profile_path.parent, identity=Identity.generate())
    assert agent.sensors.get("uart").port == "COM3"
    with pytest.raises(ValueError, match="does not exist"):
        resolve_type("missing.py:X", {}, "sensor", base_dir=tmp_path)
    with pytest.raises(ValueError, match="cannot load sensor"):
        resolve_type("drivers.py:Nope", {}, "sensor", base_dir=tmp_path)
