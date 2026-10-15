"""Profiles: built-in types by name, custom types by module:Class."""

import json
from pathlib import Path

import pytest

from loquor.agent import Skill
from loquor.agent.skills import Goal, LlmAnswer
from loquor.config import build_agent, load_profile, resolve_type
from loquor.reasoning import OllamaReasoner, TemplateReasoner
from loquor.transports import MulticastTransport

DEMO = Path(__file__).parent.parent / "demo" / "weather_and_clothes"


class EchoSkill(Skill):
    capability = "echo"

    def __init__(self, prefix: str = ""):
        self.prefix = prefix

    async def handle(self, input, ctx):
        return {"echo": self.prefix + str(input)}


def test_demo_profiles_build():
    weather = build_agent(load_profile(DEMO / "weather_agent.json"), pairing_secret="s")
    assert weather.capabilities == ["weather_forecast"]
    assert isinstance(weather.reasoner, OllamaReasoner) and weather.reasoner.model == "qwen2.5:3b"
    assert isinstance(weather.transport, MulticastTransport) and weather.transport.interface == "127.0.0.1"

    clothing = build_agent(load_profile(DEMO / "clothing_agent.json"), pairing_secret="s")
    answer, goal = clothing.skills
    assert isinstance(answer, LlmAnswer) and isinstance(goal, Goal)
    # The profile-level system_prompt reaches both skills.
    assert answer.system_prompt == goal.system_prompt and "Clothing Advisor" in goal.system_prompt


def test_command_line_overrides(tmp_path):
    profile = load_profile(DEMO / "weather_agent.json")
    agent = build_agent(profile, pairing_secret="s", model="llama3.1:8b", transport="localhost")
    assert agent.reasoner.model == "llama3.1:8b"


def test_profile_validation(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "X", "descripton": "typo", "skills": [{"type": "goal"}]}))
    with pytest.raises(ValueError, match=r"unknown fields \['descripton'\]; missing fields \['description'\]"):
        load_profile(bad)
    bad.write_text(json.dumps({"name": "X", "description": "d", "skills": []}))
    with pytest.raises(ValueError, match="non-empty list"):
        load_profile(bad)


def test_custom_types_and_bad_arguments():
    profile = {
        "name": "Custom",
        "description": "d",
        "reasoner": {"type": "template"},
        "skills": [{"type": "test_config:EchoSkill", "prefix": "> "}],
    }
    agent = build_agent(profile, pairing_secret="s")
    assert isinstance(agent.reasoner, TemplateReasoner)
    assert isinstance(agent.skills[0], EchoSkill) and agent.skills[0].prefix == "> "

    with pytest.raises(ValueError, match="invalid skill 'llm_answer'"):
        build_agent({**profile, "skills": [{"type": "llm_answer", "nope": 1}]}, pairing_secret="s")
    with pytest.raises(ValueError, match="unknown skill type 'telepathy'"):
        resolve_type("telepathy", {}, "skill")
    with pytest.raises(ValueError, match="cannot load skill"):
        resolve_type("no.such.module:Thing", {}, "skill")
    with pytest.raises(ValueError, match='needs a "type"'):
        build_agent({**profile, "skills": [{"capability": "x"}]}, pairing_secret="s")
