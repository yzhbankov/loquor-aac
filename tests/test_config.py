"""Profiles: built-in types by name, custom types by module:Class, identity files."""

import json
from pathlib import Path

import pytest

from loquor.agent import Skill
from loquor.agent.skills import Goal, LlmAnswer
from loquor.config import build_agent, load_profile, resolve_type
from loquor.protocol import Identity, Visibility
from loquor.reasoning import OllamaReasoner, TemplateReasoner
from loquor.transports import MulticastTransport

DEMO = Path(__file__).parent.parent / "demo" / "weather_and_clothes"


class EchoSkill(Skill):
    capability = "echo"

    def __init__(self, prefix: str = ""):
        self.prefix = prefix

    async def handle(self, input, ctx):
        return {"echo": self.prefix + str(input)}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_demo_profiles_build(home):
    weather = build_agent(load_profile(DEMO / "weather_agent.json"))
    assert weather.capabilities == ["weather_forecast"]
    assert isinstance(weather.reasoner, OllamaReasoner) and weather.reasoner.model == "qwen2.5:3b"
    assert isinstance(weather.transport, MulticastTransport) and weather.transport.interface == "127.0.0.1"
    assert (home / "keys" / "weather-forecaster.key").exists()
    # The same profile gets the same identity next time.
    assert build_agent(load_profile(DEMO / "weather_agent.json")).agent_id == weather.agent_id

    clothing = build_agent(load_profile(DEMO / "clothing_agent.json"))
    answer, goal = clothing.skills
    assert isinstance(answer, LlmAnswer) and isinstance(goal, Goal)
    # The profile-level system_prompt reaches both skills.
    assert answer.system_prompt == goal.system_prompt and "Clothing Advisor" in goal.system_prompt
    assert clothing.agent_id != weather.agent_id


def test_command_line_overrides(tmp_path):
    profile = load_profile(DEMO / "weather_agent.json")
    identity = Identity.generate()
    agent = build_agent(profile, model="llama3.1:8b", transport="localhost", visibility="quiet", identity=identity)
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


def test_custom_types_key_file_and_bad_arguments(tmp_path):
    profile = {
        "name": "Custom",
        "description": "d",
        "key_file": str(tmp_path / "custom.key"),
        "visibility": "silent",
        "reasoner": {"type": "template"},
        "skills": [{"type": "test_config:EchoSkill", "prefix": "> "}],
    }
    agent = build_agent(profile)
    assert isinstance(agent.reasoner, TemplateReasoner)
    assert isinstance(agent.skills[0], EchoSkill) and agent.skills[0].prefix == "> "
    assert agent.node.visibility is Visibility.SILENT and (tmp_path / "custom.key").exists()

    with pytest.raises(ValueError, match="invalid skill 'llm_answer'"):
        build_agent({**profile, "skills": [{"type": "llm_answer", "nope": 1}]})
    with pytest.raises(ValueError, match="unknown skill type 'telepathy'"):
        resolve_type("telepathy", {}, "skill")
    with pytest.raises(ValueError, match="cannot load skill"):
        resolve_type("no.such.module:Thing", {}, "skill")
    with pytest.raises(ValueError, match='needs a "type"'):
        build_agent({**profile, "skills": [{"capability": "x"}]})
