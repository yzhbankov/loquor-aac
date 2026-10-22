"""Profile JSON -> a configured :class:`Agent`.

A profile describes one device: who it is, what it measures, what it can
do, and — if it thinks — how::

    {
      "name": "Greenhouse Controller",
      "description": "I run the greenhouse.",                   # what other agents' LLMs read
      "sensors":  [{"type": "command", "name": "cpu_temp", "command": "...", "unit": "°C", "interval": 10}],
      "triggers": [{"type": "console", "name": "vent", "description": "Open or close the roof vent."}],
      "brain": {                                                  # leave out on a device without an LLM
        "prompt_file": "prompt.txt",
        "goal": "Keep the soil above 30 % moisture.",
        "every": 30,
        "act": ["vent", "tank-node:valve"]
      }
    }

- **No ``brain``** = a **node**: no LLM, no Ollama. It shares its sensors
  (``sensor_report``) and triggers (``offer_triggers``) with agents in range.
- **``brain``** = an **agent**: it answers questions and, with a ``goal``,
  reads what it needs (its sensors and those nodes share), asks agents
  that can help, and acts. It shares its sensors too, not its triggers.
- **``share``** changes what is shared: ``false``, or
  ``{"sensors": true | [names], "triggers": true | [names], "min_trust": 1}``.
- **``skills``** adds skills by hand, for behaviour the brain does not cover.
  A profile with ``skills`` shares nothing it does not list, unless it
  sets ``share``.

Each sensor, trigger and skill is a *part*: a dict with a ``"type"`` — a
built-in name, ``"module:Class"`` for an installed module, or
``"file.py:Class"`` for a file next to the profile — and the constructor's
keyword arguments. Sensors and triggers may also be plain functions; they
are wrapped automatically. That ``"type"`` lookup is the whole extension
mechanism.

Transport, model and beacon interval come from the device config
(:mod:`loquor.settings`) unless the profile pins them (``"transport"``,
``"reasoner"``, ``"beacon_interval"``) or the command line overrides them.
The full list of fields is ``profile.schema.json`` next to this module.
"""

from __future__ import annotations

import importlib
import importlib.resources
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from loquor.agent.agent import Agent
from loquor.agent.skills import BUILTIN_SKILLS
from loquor.protocol.discovery import Visibility, slug
from loquor.protocol.identity import Identity
from loquor.reasoning import NoReasoner, OllamaReasoner, OpenAIReasoner, TemplateReasoner
from loquor.sensors import BUILTIN_SENSORS, FunctionSensor
from loquor.settings import Settings, loquor_home
from loquor.transports import TRANSPORTS, make_transport
from loquor.triggers import BUILTIN_TRIGGERS, FunctionTrigger

BUILTIN_REASONERS = {"ollama": OllamaReasoner, "openai": OpenAIReasoner, "template": TemplateReasoner, "none": NoReasoner}

REQUIRED_FIELDS = {"name", "description"}

__all__ = ["build_agent", "load_profile", "profile_schema", "resolve_type", "instantiate", "loquor_home"]


def profile_schema() -> dict[str, Any]:
    """The JSON schema every profile must match (``src/loquor/profile.schema.json``)."""
    text = importlib.resources.files("loquor").joinpath("profile.schema.json").read_text()
    return json.loads(text)


PROFILE_FIELDS = set(profile_schema()["properties"])


def load_profile(path: str | Path) -> dict[str, Any]:
    """Read and validate a profile. ``*_file`` prompts are read relative to it."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"profile {path} does not exist") from None
    except ValueError as exc:
        raise ValueError(f"invalid profile {path}: not valid JSON ({exc})") from None
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
    for key in ("sensors", "triggers", "skills"):
        if key in data and not isinstance(data[key], list):
            raise ValueError(f"invalid profile {path}: {key} must be a list")
    try:
        jsonschema.validate(data, profile_schema())
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "profile"
        raise ValueError(f"invalid profile {path}: {where}: {exc.message}") from None
    data.pop("$schema", None)
    _read_prompt_files(data, path.parent)
    if not (data.get("brain") or data.get("skills") or data.get("sensors") or data.get("triggers")):
        raise ValueError(f"invalid profile {path}: it does nothing; give it sensors or triggers to share, "
                         "a \"brain\", or \"skills\"")
    return data


def _read_prompt_files(data: dict[str, Any], base_dir: Path) -> None:
    """``system_prompt_file`` (profile or skill) and the brain's ``prompt_file`` -> the prompt text."""
    holders = [(data, "system_prompt"), *((s, "system_prompt") for s in data.get("skills") or [] if isinstance(s, dict))]
    if isinstance(data.get("brain"), dict):
        holders.append((data["brain"], "prompt"))
    for holder, key in holders:
        file = holder.pop(f"{key}_file", None)
        if file is None:
            continue
        prompt_path = Path(file).expanduser()
        if not prompt_path.is_absolute():
            prompt_path = base_dir / prompt_path
        try:
            holder[key] = prompt_path.read_text().strip()
        except OSError as exc:
            raise ValueError(f"cannot read {key}_file {file!r}: {exc}") from None


def build_agent(
    profile: dict[str, Any],
    *,
    base_dir: str | Path | None = None,  # where "file.py:Class" types are looked up
    settings: Settings | None = None,  # device config; loaded from disk if not given
    transport: str | None = None,
    interface: str | None = None,
    model: str | None = None,
    visibility: str | None = None,
    identity: Identity | None = None,
    beacon_interval: float | None = None,
    wrap_reasoner: Callable[[Any], Any] | None = None,  # e.g. TracingReasoner
) -> Agent:
    """Instantiate every part of a profile.

    Precedence for transport, model and beacon interval: the keyword
    arguments (command line) → the profile → ``settings`` (device config).
    """
    base_dir = Path(base_dir) if base_dir else None
    settings = settings or Settings.load()

    transport_spec = dict(profile.get("transport") or {"type": settings.transport})
    if transport:
        transport_spec["type"] = transport
    if interface:
        transport_spec["interface"] = interface
    elif settings.interface and "interface" not in transport_spec and transport_spec.get("type") == "wifi":
        transport_spec["interface"] = settings.interface

    if beacon_interval is None:
        beacon_interval = float(profile.get("beacon_interval") or settings.beacon_interval)

    skills = [
        instantiate(spec, BUILTIN_SKILLS, "skill", defaults={"system_prompt": profile.get("system_prompt")},
                    base_dir=base_dir)
        for spec in skill_specs(profile)
    ]
    thinking = [type(s).__name__ for s in skills if s.needs_reasoner]
    reasoner_spec = dict(profile.get("reasoner") or ({"type": settings.reasoner} if thinking else {"type": "none"}))
    if reasoner_spec.get("type") == "none" and thinking:
        raise ValueError(
            f"profile says reasoner \"none\" but {', '.join(thinking)} need an LLM; "
            "drop those skills to make this a node, or choose a reasoner"
        )
    if reasoner_spec.get("type") == "ollama":
        reasoner_spec.setdefault("model", settings.model)
        reasoner_spec.setdefault("base_url", settings.ollama_url)
    if reasoner_spec.get("type") == "openai":
        reasoner_spec.setdefault("model", settings.model)
        reasoner_spec.setdefault("base_url", settings.openai_url)
        if settings.api_key:
            reasoner_spec.setdefault("api_key", settings.api_key)
    if model and reasoner_spec.get("type") != "none":
        reasoner_spec["model"] = model
    sensors = [instantiate(spec, BUILTIN_SENSORS, "sensor", wrap=FunctionSensor, base_dir=base_dir)
               for spec in profile.get("sensors") or []]
    triggers = [instantiate(spec, BUILTIN_TRIGGERS, "trigger", wrap=FunctionTrigger, base_dir=base_dir)
                for spec in profile.get("triggers") or []]
    if identity is None:
        key_file = profile.get("key_file") or loquor_home() / "keys" / f"{slug(profile['name']) or 'agent'}.key"
        identity = Identity.load_or_create(Path(key_file).expanduser())
    reasoner = instantiate(reasoner_spec, BUILTIN_REASONERS, "reasoner", base_dir=base_dir)
    if wrap_reasoner is not None:
        reasoner = wrap_reasoner(reasoner)
    return Agent(
        name=profile["name"],
        description=profile["description"],
        skills=skills,
        reasoner=reasoner,
        transport=_build_transport(transport_spec, base_dir),
        identity=identity,
        visibility=Visibility(visibility or profile.get("visibility") or "visible"),
        beacon_interval=beacon_interval,
        pinned_keys=str(profile.get("pinned_keys") or loquor_home() / "pinned_keys.json"),
        relay=bool(profile.get("relay", False)),
        sensors=sensors,
        triggers=triggers,
    )


def skill_specs(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """The skills a profile asks for: its ``brain`` and ``share`` turned into skill specs, then its ``skills``."""
    explicit = [dict(s) for s in profile.get("skills") or []]
    types = {s.get("type") for s in explicit}
    specs: list[dict[str, Any]] = []
    brain = profile.get("brain")
    if brain:
        spec = {"type": "brain", **{k: v for k, v in brain.items() if k != "prompt"}}
        if brain.get("prompt"):
            spec["system_prompt"] = brain["prompt"]
        specs.append(spec)
    # A profile that lists skills by hand chose what it offers; it shares more only if it says so.
    share = profile.get("share", not explicit)
    if share is False:
        share = {"sensors": False, "triggers": False}
    elif share is True:
        share = {}
    min_trust = share.get("min_trust", 0)
    sensors = share.get("sensors", True)
    triggers = share.get("triggers", not brain)  # a node shares its triggers; an agent keeps them
    if sensors and profile.get("sensors") and "sensor_report" not in types:
        specs.append({"type": "sensor_report", "min_trust": min_trust,
                      **({"sensors": sensors} if isinstance(sensors, list) else {})})
    if triggers and profile.get("triggers") and "offer_triggers" not in types:
        specs.append({"type": "offer_triggers", "min_trust": min_trust,
                      **({"triggers": triggers} if isinstance(triggers, list) else {})})
    return specs[:1] + explicit + specs[1:] if brain else explicit + specs


def _build_transport(spec: dict[str, Any], base_dir: Path | None):
    spec = dict(spec)
    name = spec.pop("type", "localhost")
    if name in TRANSPORTS:
        return make_transport(name, **spec)
    return instantiate({"type": name, **spec}, {}, "transport", base_dir=base_dir)


def instantiate(
    spec: dict[str, Any],
    builtins: dict[str, type],
    what: str,
    defaults: dict[str, Any] | None = None,
    wrap: Callable[..., Any] | None = None,
    base_dir: Path | None = None,
) -> Any:
    """``{"type": name | module:Class | file.py:Class, **kwargs}`` -> an instance.

    ``defaults`` fill in constructor arguments the spec leaves out, if the
    constructor accepts them (so a profile-level ``system_prompt`` reaches
    every skill that takes one). If the type resolves to a plain function
    and ``wrap`` is given, the function is wrapped: ``wrap(fn, **kwargs)``.
    """
    spec = dict(spec)
    type_name = spec.pop("type", None)
    if not isinstance(type_name, str) or not type_name:
        raise ValueError(f"{what} needs a \"type\" (one of {', '.join(builtins) or 'module:Class'})")
    target = resolve_type(type_name, builtins, what, base_dir)
    if not inspect.isclass(target):
        if wrap is None or not callable(target):
            raise ValueError(f"{what} {type_name!r} is not a class")
        try:
            return wrap(target, **spec)
        except TypeError as exc:
            raise ValueError(f"invalid {what} {type_name!r}: {exc}") from exc
    if defaults:
        params = inspect.signature(target).parameters
        for key, value in defaults.items():
            if value is not None and key in params and key not in spec:
                spec[key] = value
    try:
        return target(**spec)
    except TypeError as exc:
        raise ValueError(f"invalid {what} {type_name!r}: {exc}") from exc


def resolve_type(type_name: str, builtins: dict[str, type], what: str, base_dir: Path | None = None) -> Any:
    if type_name in builtins:
        return builtins[type_name]
    module_name, sep, attr = type_name.partition(":")
    if not sep or not attr:
        raise ValueError(
            f"unknown {what} type {type_name!r}; use one of {', '.join(builtins)}, "
            f"\"module:Class\" or \"file.py:Class\""
        )
    try:
        module = _load_file(module_name, base_dir) if module_name.endswith(".py") else importlib.import_module(module_name)
        return getattr(module, attr)
    except (ImportError, AttributeError, OSError) as exc:
        raise ValueError(f"cannot load {what} {type_name!r}: {exc}") from exc


def _load_file(file: str, base_dir: Path | None):
    """Import a .py file by path (relative to the profile's directory)."""
    path = Path(file).expanduser()
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    path = path.resolve()
    if not path.is_file():
        raise ImportError(f"{path} does not exist")
    name = f"loquor_local_{path.stem}_{abs(hash(str(path)))}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
