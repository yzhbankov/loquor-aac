"""Built-in sensors.

- :class:`Simulated` — a random walk between ``min`` and ``max``; for demos
  and tests without hardware.
- :class:`Command` — runs a shell command and takes its stdout as the value
  (JSON if it parses, else a number, else text). The universal adapter: any
  device that already has a CLI, a script, or ``cat /sys/...`` becomes a sensor.
- :class:`File` — reads a file: sysfs and procfs values (``/sys/class/thermal/…``,
  a 1-wire thermometer's ``w1_slave``), or anything a driver writes to disk.
- :class:`Http` — GETs a URL: devices with a web API (ESP32 boards, smart
  plugs, weather stations), JSON or text.

GPIO pins and serial ports are in :mod:`loquor.sensors.hardware`.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Any

from loquor.sensors.base import Reading, Sensor


class Simulated(Sensor):
    def __init__(self, name: str, min: float, max: float, unit: str = "", step: float | None = None,
                 interval: float | None = None, description: str = "", start: float | None = None):
        self.name = name
        self.unit = unit
        self.interval = interval
        self.description = description or f"simulated {name} between {min} and {max} {unit}".strip()
        self.lo, self.hi = float(min), float(max)
        self.step = step if step is not None else (self.hi - self.lo) / 10
        self.value = float(start) if start is not None else random.uniform(self.lo, self.hi)

    async def read(self) -> Reading:
        self.value = min(self.hi, max(self.lo, self.value + random.uniform(-self.step, self.step)))
        return self.reading(round(self.value, 2))


class Command(Sensor):
    def __init__(self, name: str, command: str, unit: str = "", interval: float | None = None,
                 description: str = "", timeout: float = 10.0):
        self.name = name
        self.command = command
        self.unit = unit
        self.interval = interval
        self.description = description or f"output of `{command}`"
        self.timeout = timeout

    async def read(self) -> Reading:
        proc = await asyncio.create_subprocess_shell(
            self.command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return self.failed(f"timed out after {self.timeout}s")
        if proc.returncode != 0:
            return self.failed(f"exit {proc.returncode}: {err.decode(errors='replace').strip()[:200]}")
        return self.reading(parse_output(out.decode(errors="replace").strip()))


class File(Sensor):
    """``path`` holds the value. ``pattern`` picks it out of longer text (its first group,
    e.g. ``"t=(-?\\d+)"`` for a DS18B20); ``scale`` and ``offset`` turn a raw number into units."""

    def __init__(self, name: str, path: str, unit: str = "", interval: float | None = None, description: str = "",
                 pattern: str | None = None, scale: float = 1.0, offset: float = 0.0):
        super().__init__(name=name, unit=unit, interval=interval, description=description or f"value in {path}")
        self.path = Path(path).expanduser()
        self.pattern = re.compile(pattern) if pattern else None
        self.scale, self.offset = scale, offset

    async def read(self) -> Reading:
        try:
            text = await asyncio.to_thread(self.path.read_text)
        except OSError as exc:
            return self.failed(f"cannot read {self.path}: {exc.strerror or exc}")
        return self._value(text.strip())

    def _value(self, text: str) -> Reading:
        if self.pattern:
            match = self.pattern.search(text)
            if not match:
                return self.failed(f"pattern {self.pattern.pattern!r} not found in {text[:60]!r}")
            text = match.group(1) if match.groups() else match.group(0)
        return self.reading(scaled(parse_output(text), self.scale, self.offset))


class Http(Sensor):
    """GET ``url``; ``field`` picks a value out of the JSON answer by a dotted path
    (``"main.temp"``, ``"sensors.0.value"``). Without it the whole answer is the value."""

    def __init__(self, name: str, url: str, unit: str = "", interval: float | None = None, description: str = "",
                 field: str | None = None, headers: dict[str, str] | None = None, timeout: float = 5.0,
                 scale: float = 1.0, offset: float = 0.0):
        super().__init__(name=name, unit=unit, interval=interval, description=description or f"value at {url}")
        self.url, self.field, self.headers, self.timeout = url, field, headers or {}, timeout
        self.scale, self.offset = scale, offset

    async def read(self) -> Reading:
        import aiohttp  # noqa: PLC0415 — a dependency of loquor anyway; imported where it is used

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.get(self.url, headers=self.headers) as response:
                    if response.status >= 400:
                        return self.failed(f"HTTP {response.status} from {self.url}")
                    text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return self.failed(f"{self.url}: {type(exc).__name__} {exc}".strip())
        value = parse_output(text.strip())
        if self.field:
            try:
                value = pick(value, self.field)
            except (KeyError, IndexError, TypeError, ValueError):
                return self.failed(f"no {self.field!r} in the answer from {self.url}")
        return self.reading(scaled(value, self.scale, self.offset))


def pick(value: Any, path: str) -> Any:
    """``pick({"a": [{"b": 1}]}, "a.0.b") == 1``."""
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def scaled(value: Any, scale: float, offset: float) -> Any:
    if (scale != 1.0 or offset != 0.0) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value * scale + offset, 6)
    return value


def parse_output(text: str):
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text
