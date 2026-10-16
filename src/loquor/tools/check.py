"""``loquor check``: does this profile build and work, before going on the air?

Loads the profile, instantiates every part, reads every sensor once,
validates every trigger's schema, confirms the model is pulled and the
transport is usable — and prints what the agent will offer. Nothing is
sent over the radio.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import jsonschema

from loquor.agent.agent import Agent
from loquor.config import build_agent, load_profile
from loquor.settings import Settings
from loquor.tools.doctor import FAIL, OK, WARN, Check, check_transport

READ_TIMEOUT = 10.0


async def check_profile(path: Path, settings: Settings, *, llm: bool = True, **overrides: Any) -> tuple[list[str], list[Check]]:
    """Returns (description lines, checks). The profile is fine if every check is ok."""
    checks: list[Check] = []
    try:
        profile = load_profile(path)
    except ValueError as exc:
        return [], [Check("profile", FAIL, str(exc), "see docs/USAGE.md#profile for every field")]
    checks.append(Check("profile", OK, f"{path} is valid ({profile['name']})"))
    try:
        agent = build_agent(profile, base_dir=path.parent, settings=settings, **overrides)
    except (ValueError, OSError) as exc:
        checks.append(Check("parts", FAIL, str(exc), "fix the part the message names; constructor arguments come from the profile"))
        return [], checks
    lines = describe(agent)
    checks += await check_sensors(agent)
    checks += check_triggers(agent)
    if llm:
        checks += await check_reasoner(agent, settings)
    checks += check_transport(_transport_name(agent))
    return lines, checks


def describe(agent: Agent) -> list[str]:
    lines = [f"{agent.name} — {agent.description}", f"  identity   : {agent.agent_id.hex()}",
             f"  model      : {getattr(agent.reasoner, 'model', None) or agent.reasoner.name}",
             f"  transport  : {_transport_name(agent)}",
             f"  visibility : {agent.node.visibility.value}, beacon every {agent.node.beacon_interval:g} s"]
    lines.append(f"  offers     : {', '.join(agent.capabilities) or 'nothing (only pursues goals)'}")
    for skill in agent.skills:
        kind = type(skill).__name__
        uses = []
        if getattr(skill, "sensors", ()):
            uses.append(f"sensors {list(skill.sensors)}")
        if getattr(skill, "triggers", ()):
            uses.append(f"triggers {list(skill.triggers)}")
        what = f"capability {skill.capability!r}" if skill.capability else f"goal {getattr(skill, 'goal', '')!r}"
        lines.append(f"    - {kind}: {what}" + (f", uses {', '.join(uses)}" if uses else ""))
    lines.append(f"  sensors    : {', '.join(agent.sensors.names()) or 'none'}")
    lines.append(f"  triggers   : {', '.join(agent.triggers.names()) or 'none'}")
    return lines


async def check_sensors(agent: Agent) -> list[Check]:
    checks = []
    for name in agent.sensors.names():
        sensor = agent.sensors.get(name)
        try:
            reading = await asyncio.wait_for(sensor.safe_read(), READ_TIMEOUT)
        except asyncio.TimeoutError:
            checks.append(Check(f"sensor {name}", FAIL, f"read() took longer than {READ_TIMEOUT:g} s",
                                "bound the read with a timeout; run blocking I/O through asyncio.to_thread"))
            continue
        if reading.error:
            checks.append(Check(f"sensor {name}", WARN, f"unavailable: {reading.error}",
                                "the agent will run and report it as unavailable; fix the device or the driver"))
        else:
            try:
                json.dumps(reading.value)
            except (TypeError, ValueError):
                checks.append(Check(f"sensor {name}", FAIL, f"value {reading.value!r} is not JSON-safe",
                                    "return a number, a string, or a flat dict of those"))
                continue
            checks.append(Check(f"sensor {name}", OK, reading.text()))
    return checks


def check_triggers(agent: Agent) -> list[Check]:
    checks = []
    for name in agent.triggers.names():
        trigger = agent.triggers.get(name)
        if not trigger.description:
            checks.append(Check(f"trigger {name}", FAIL, "has no description", "the LLM reads it to know when to use the trigger"))
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(trigger.input_schema)
        except jsonschema.SchemaError as exc:
            checks.append(Check(f"trigger {name}", FAIL, f"input_schema is not a valid JSON schema: {exc.message}"))
            continue
        checks.append(Check(f"trigger {name}", OK, trigger.description))
    return checks


async def check_reasoner(agent: Agent, settings: Settings) -> list[Check]:
    from loquor.reasoning import OllamaReasoner, ollama_available  # noqa: PLC0415

    reasoner = getattr(agent.reasoner, "inner", agent.reasoner)
    if not isinstance(reasoner, OllamaReasoner):
        return [Check("reasoner", OK, f"{reasoner.name} (no model needed)")]
    if not await ollama_available(reasoner.base_url):
        return [Check("ollama", FAIL, f"not reachable at {reasoner.base_url}", "loquor doctor")]
    if not await ollama_available(reasoner.base_url, model=reasoner.model):
        return [Check(f"model {reasoner.model}", FAIL, "not pulled", f"ollama pull {reasoner.model}")]
    return [Check(f"model {reasoner.model}", OK, "pulled")]


def _transport_name(agent: Agent) -> str:
    transport = agent.transport
    interface = getattr(transport, "interface", None)
    if type(transport).__name__ == "MulticastTransport":
        return "localhost" if str(interface).startswith("127.") else "wifi"
    if type(transport).__name__ == "BleTransport":
        return "bluetooth"
    return type(transport).__name__
