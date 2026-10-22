"""Device settings: what is true of *this device*, not of one agent.

A profile describes an agent (persona, skills, sensors, triggers). The
transport it should use, the model this hardware can run, how often to
beacon and how much to log are properties of the device it runs on. They
live in ``$LOQUOR_HOME/config.json`` (``~/.loquor/config.json``), written
once by ``install.sh`` or ``loquor config set``, so the same profile runs
unchanged on a laptop, a Raspberry Pi or a robot.

Precedence, highest first: command line → profile → device config → defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TRANSPORT = "localhost"
DEFAULT_BEACON_INTERVAL = 10.0
WIRE_LEVELS = ("off", "no-handshake", "all")
REASONER_BACKENDS = ("ollama", "openai")


def loquor_home() -> Path:
    return Path(os.environ.get("LOQUOR_HOME", Path.home() / ".loquor"))


def config_path() -> Path:
    return loquor_home() / "config.json"


def run_dir() -> Path:
    """Where running agents leave their status and trace files."""
    return loquor_home() / "run"


@dataclass
class Settings:
    transport: str = DEFAULT_TRANSPORT  # localhost | wifi | bluetooth
    interface: str | None = None  # wifi: local IP of the interface to use
    model: str = DEFAULT_MODEL  # the model every agent on this device uses
    reasoner: str = "ollama"  # ollama | openai (an OpenAI-compatible server: llama.cpp, LM Studio, vLLM)
    ollama_url: str = "http://localhost:11434"
    openai_url: str = "http://localhost:8080/v1"
    api_key: str | None = None  # for an openai server that wants one; LOQUOR_API_KEY works too
    beacon_interval: float = DEFAULT_BEACON_INTERVAL
    wire: str = "off"  # envelope log: off | no-handshake | all
    trace: bool = True  # record every LLM call to run/<agent>.trace.jsonl
    llm: bool = True  # False on a sensor node: no Ollama here, doctor does not look for it

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """The device settings, or defaults if the file does not exist.

        ``LOQUOR_MODEL`` in the environment overrides the model, for one-off runs.
        """
        path = path or config_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
            except ValueError as exc:
                raise ValueError(f"invalid device config {path}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"invalid device config {path}: not a JSON object")
            data = loaded
        settings = cls()
        for problem in settings.apply(data):
            raise ValueError(f"invalid device config {path}: {problem}")
        if os.environ.get("LOQUOR_MODEL"):
            settings.model = os.environ["LOQUOR_MODEL"]
        return settings

    def apply(self, data: dict[str, Any]) -> list[str]:
        """Set known keys from ``data``; returns the problems found (empty = all good)."""
        problems = []
        known = {f.name for f in fields(self)}
        for key, value in data.items():
            if key not in known:
                problems.append(f"unknown key {key!r} (known: {', '.join(sorted(known))})")
                continue
            try:
                setattr(self, key, _coerce(key, value))
            except ValueError as exc:
                problems.append(str(exc))
        return problems

    def set(self, key: str, raw: str) -> None:
        """Set one key from its command-line spelling (``loquor config set wire all``)."""
        problems = self.apply({key: raw})
        if problems:
            raise ValueError(problems[0])

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def shown(self) -> dict[str, Any]:
        """``to_dict`` with the API key masked, for printing."""
        data = self.to_dict()
        if data.get("api_key"):
            data["api_key"] = "***"
        return data


def _coerce(key: str, value: Any) -> Any:
    if key == "transport":
        from loquor.transports import TRANSPORTS  # noqa: PLC0415 — avoid importing radios at import time

        if value not in TRANSPORTS:
            raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}, not {value!r}")
        return value
    if key in ("interface", "api_key"):
        return None if value in (None, "", "null", "none") else str(value)
    if key == "reasoner":
        if value not in REASONER_BACKENDS:
            raise ValueError(f"reasoner must be one of {', '.join(REASONER_BACKENDS)}, not {value!r}")
        return value
    if key in ("model", "ollama_url", "openai_url"):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value
    if key == "beacon_interval":
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"beacon_interval must be a number of seconds, not {value!r}") from None
        if seconds <= 0:
            raise ValueError("beacon_interval must be positive")
        return seconds
    if key == "wire":
        if value not in WIRE_LEVELS:
            raise ValueError(f"wire must be one of {', '.join(WIRE_LEVELS)}, not {value!r}")
        return value
    if key in ("trace", "llm"):
        if isinstance(value, bool):
            return value
        text = str(value).lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{key} must be true or false, not {value!r}")
    return value
