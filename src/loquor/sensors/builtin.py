"""Built-in sensors.

- :class:`Simulated` — a random walk between ``min`` and ``max``; for demos
  and tests without hardware.
- :class:`Command` — runs a shell command and takes its stdout as the value
  (JSON if it parses, else a number, else text). The universal adapter: any
  device that already has a CLI, a script, or ``cat /sys/...`` becomes a sensor.
"""

from __future__ import annotations

import asyncio
import json
import random

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


def parse_output(text: str):
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text
