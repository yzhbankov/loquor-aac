"""Profiles: built-in types by name, custom types by module:Class or file.py:Class."""

import json
from pathlib import Path

import pytest

from loquor.agent import Skill
from loquor.agent.skills import Goal, LlmAnswer, SensorReport
from loquor.config import build_agent, load_profile, resolve_type
from loquor.protocol import Identity, Visibility
from loquor.reasoning import OllamaReasoner, TemplateReasoner
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
    weather = build_agent(load_profile(DEMO / "weather_agent.json"), base_dir=DEMO)
    assert weather.capabilities == ["weather_forecast", "sensor_report"]
    assert isinstance(weather.reasoner, OllamaReasoner) and weather.reasoner.model == "qwen2.5:3b"
    assert isinstance(weather.transport, MulticastTransport) and weather.transport.interface == "127.0.0.1"
    assert weather.sensors.names() == ["weather"] and weather.sensors.get("weather").interval == 5
    assert weather.skills[0].sensors == ("weather",) and isinstance(weather.skills[1], SensorReport)
    assert (home / "keys" / "weather-forecaster.key").exists()
    # The same profile gets the same identity next time.
    assert build_agent(load_profile(DEMO / "weather_agent.json"), base_dir=DEMO).agent_id == weather.agent_id

    clothing = build_agent(load_profile(DEMO / "clothing_agent.json"), base_dir=DEMO)
    answer, goal = clothing.skills
    assert isinstance(answer, LlmAnswer) and isinstance(goal, Goal)
    assert clothing.triggers.names() == ["show_outfit"] and goal.triggers == ("show_outfit",)
    assert "forecast" in clothing.triggers.get("show_outfit").input_schema["properties"]
    # The profile-level system_prompt reaches both skills.
    assert answer.system_prompt == goal.system_prompt and "Clothing Advisor" in goal.system_prompt
    assert clothing.agent_id != weather.agent_id


def test_command_line_overrides(tmp_path):
    profile = load_profile(DEMO / "weather_agent.json")
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
    with pytest.raises(ValueError, match="non-empty list"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"type": "goal"}], "sensors": {}}))
    with pytest.raises(ValueError, match="sensors must be a list"):
        load_profile(bad)


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
