"""Profile JSON -> a configured :class:`Agent`.

A profile names the agent and lists its parts. Each part is a dict with a
``"type"`` — a built-in name, or ``"module:Class"`` for your own — and the
constructor's keyword arguments::

    {
      "name": "Weather Station",
      "description": "I report tomorrow's weather.",
      "system_prompt": "You are ...",                    # default for skills that take one
      "transport": {"type": "wifi"},                      # default: localhost
      "reasoner":  {"type": "ollama", "model": "qwen2.5:3b"},
      "skills": [
        {"type": "llm_answer", "capability": "weather_forecast"},
        {"type": "my_farm.irrigation:Irrigation"}
      ]
    }

That ``"type"`` lookup is the whole extension mechanism.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from loquor.agent.agent import Agent
from loquor.agent.skills import BUILTIN_SKILLS
from loquor.reasoning import OllamaReasoner, TemplateReasoner
from loquor.transports import TRANSPORTS, make_transport

BUILTIN_REASONERS = {"ollama": OllamaReasoner, "template": TemplateReasoner}

PROFILE_FIELDS = {"name", "description", "system_prompt", "transport", "reasoner", "skills"}
REQUIRED_FIELDS = {"name", "description", "skills"}


def load_profile(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"invalid profile {path}: not a JSON object")
    unknown = sorted(set(data) - PROFILE_FIELDS)
    missing = sorted(REQUIRED_FIELDS - set(data))
    if unknown or missing:
        problems = [p for p in (
            unknown and f"unknown fields {unknown}",
            missing and f"missing fields {missing}",
        ) if p]
        raise ValueError(f"invalid profile {path}: " + "; ".join(problems))
    if not isinstance(data["skills"], list) or not data["skills"]:
        raise ValueError(f"invalid profile {path}: skills must be a non-empty list")
    return data


def build_agent(
    profile: dict[str, Any],
    *,
    pairing_secret: str,
    transport: str | None = None,
    interface: str | None = None,
    model: str | None = None,
    advert_interval: float = 10.0,
) -> Agent:
    """Instantiate every part of a profile. Command-line overrides win."""
    transport_spec = dict(profile.get("transport") or {"type": "localhost"})
    if transport:
        transport_spec["type"] = transport
    if interface:
        transport_spec["interface"] = interface

    reasoner_spec = dict(profile.get("reasoner") or {"type": "ollama"})
    if model:
        reasoner_spec["model"] = model

    skills = [
        instantiate(spec, BUILTIN_SKILLS, "skill", defaults={"system_prompt": profile.get("system_prompt")})
        for spec in profile["skills"]
    ]
    return Agent(
        name=profile["name"],
        description=profile["description"],
        skills=skills,
        reasoner=instantiate(reasoner_spec, BUILTIN_REASONERS, "reasoner"),
        transport=_build_transport(transport_spec),
        pairing_secret=pairing_secret,
        advert_interval=advert_interval,
    )


def _build_transport(spec: dict[str, Any]):
    spec = dict(spec)
    name = spec.pop("type", "localhost")
    if name in TRANSPORTS:
        return make_transport(name, **spec)
    return instantiate({"type": name, **spec}, {}, "transport")


def instantiate(
    spec: dict[str, Any],
    builtins: dict[str, type],
    what: str,
    defaults: dict[str, Any] | None = None,
) -> Any:
    """``{"type": name_or_module:Class, **kwargs}`` -> an instance.

    ``defaults`` fill in constructor arguments the spec leaves out, if the
    constructor accepts them (so a profile-level ``system_prompt`` reaches
    every skill that takes one).
    """
    spec = dict(spec)
    type_name = spec.pop("type", None)
    if not isinstance(type_name, str) or not type_name:
        raise ValueError(f"{what} needs a \"type\" (one of {', '.join(builtins) or 'module:Class'})")
    cls = resolve_type(type_name, builtins, what)
    if defaults:
        params = inspect.signature(cls).parameters
        for key, value in defaults.items():
            if value is not None and key in params and key not in spec:
                spec[key] = value
    try:
        return cls(**spec)
    except TypeError as exc:
        raise ValueError(f"invalid {what} {type_name!r}: {exc}") from exc


def resolve_type(type_name: str, builtins: dict[str, type], what: str) -> type:
    if type_name in builtins:
        return builtins[type_name]
    module_name, sep, attr = type_name.partition(":")
    if not sep or not attr:
        raise ValueError(
            f"unknown {what} type {type_name!r}; use one of {', '.join(builtins)} or \"module:Class\""
        )
    try:
        return getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot load {what} {type_name!r}: {exc}") from exc
