"""Profile JSON -> a configured :class:`Agent`.

A profile names the agent and lists its parts. Each part is a dict with a
``"type"`` — a built-in name, ``"module:Class"`` for an installed module, or
``"file.py:Class"`` for a file next to the profile — and the constructor's
keyword arguments::

    {
      "name": "Weather Station",
      "description": "I report tomorrow's weather.",
      "system_prompt_file": "prompt.txt",                 # or "system_prompt": "You are ..."
      "visibility": "visible",                            # visible | quiet | silent
      "sensors":  [{"type": "weather_sensor.py:RandomWeather", "name": "weather", "interval": 5}],
      "triggers": [{"type": "console", "name": "print"}],
      "skills": [
        {"type": "llm_answer", "capability": "weather_forecast", "sensors": ["weather"]},
        {"type": "my_farm.irrigation:Irrigation"}
      ]
    }

Transport, model and beacon interval come from the device config
(:mod:`loquor.settings`) unless the profile pins them (``"transport"``,
``"reasoner"``, ``"beacon_interval"``) or the command line overrides them.
The full list of fields is ``profile.schema.json`` next to this module.

A profile whose skills never call the reasoner (only ``sensor_report``,
say) is a **node**: it gets ``reasoner "none"`` and needs no Ollama.
``"reasoner": {"type": "none"}`` says so explicitly; combined with a skill
that needs an LLM it is refused at startup.

Sensors and triggers may also be plain functions; they are wrapped
automatically. That ``"type"`` lookup is the whole extension mechanism.
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
from loquor.reasoning import NoReasoner, OllamaReasoner, TemplateReasoner
from loquor.sensors import BUILTIN_SENSORS, FunctionSensor
from loquor.settings import Settings, loquor_home
from loquor.transports import TRANSPORTS, make_transport
from loquor.triggers import BUILTIN_TRIGGERS, FunctionTrigger

BUILTIN_REASONERS = {"ollama": OllamaReasoner, "template": TemplateReasoner, "none": NoReasoner}

REQUIRED_FIELDS = {"name", "description", "skills"}

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
    if not isinstance(data["skills"], list) or not data["skills"]:
        raise ValueError(f"invalid profile {path}: skills must be a non-empty list")
    for key in ("sensors", "triggers"):
        if key in data and not isinstance(data[key], list):
            raise ValueError(f"invalid profile {path}: {key} must be a list")
    try:
        jsonschema.validate(data, profile_schema())
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "profile"
        raise ValueError(f"invalid profile {path}: {where}: {exc.message}") from None
    data.pop("$schema", None)
    _read_prompt_files(data, path.parent)
    return data


def _read_prompt_files(data: dict[str, Any], base_dir: Path) -> None:
    """``system_prompt_file`` (profile or skill) -> ``system_prompt``."""
    holders = [data, *(s for s in data["skills"] if isinstance(s, dict))]
    for holder in holders:
        file = holder.pop("system_prompt_file", None)
        if file is None:
            continue
        prompt_path = Path(file).expanduser()
        if not prompt_path.is_absolute():
            prompt_path = base_dir / prompt_path
        try:
            holder["system_prompt"] = prompt_path.read_text().strip()
        except OSError as exc:
            raise ValueError(f"cannot read system_prompt_file {file!r}: {exc}") from None


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
        for spec in profile["skills"]
    ]
    thinking = [type(s).__name__ for s in skills if s.needs_reasoner]
    reasoner_spec = dict(profile.get("reasoner") or ({"type": "ollama"} if thinking else {"type": "none"}))
    if reasoner_spec.get("type") == "none" and thinking:
        raise ValueError(
            f"profile says reasoner \"none\" but {', '.join(thinking)} need an LLM; "
            "drop those skills to make this a node, or choose a reasoner"
        )
    if reasoner_spec.get("type") == "ollama":
        reasoner_spec.setdefault("model", settings.model)
        reasoner_spec.setdefault("base_url", settings.ollama_url)
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
