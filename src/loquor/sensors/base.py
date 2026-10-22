"""The sensor contract.

A sensor answers one question — *what do you measure right now?* — with a
:class:`Reading`. Two ways to deliver readings:

- **on demand**: the agent calls ``read()`` when a skill asks for a fresh value;
- **periodically / pushed**: set ``interval`` and the agent polls ``read()``
  into its store, or override ``run(publish)`` for a device that streams.

Rules for a sensor:

1. ``read()`` returns a :class:`Reading`; on failure return one with
   ``error`` set (``self.failed(...)``). Never raise out of ``read()``: the
   agent treats a dead sensor as "no data", not as a crash.
2. ``value`` is JSON-safe: a number, a string, or a flat dict of those. Units
   go in ``unit``, not in the value.
3. Blocking I/O (serial ports, subprocesses) goes through ``asyncio.to_thread``.
4. Import nothing from loquor but this module. A sensor knows nothing about
   agents, peers or LLMs; that is what keeps it reusable.

A class sets its contract as class attributes; the profile may override
``name``, ``description``, ``unit`` and ``interval`` per instance, with no
``__init__`` to write::

    class Thermometer(Sensor):
        name, unit, interval = "thermometer", "°C", 10
        async def read(self):
            return self.reading(await read_my_device())
    # profile: {"type": "drivers.py:Thermometer", "name": "greenhouse_air", "interval": 30}

A class that takes its own arguments (a port, a pin) defines ``__init__``
and passes the rest on: ``super().__init__(**fields)``.

A plain function works too::

    async def read_temp():            # or a sync def
        return 21.5                   # or a Reading
    # profile: {"type": "my_drivers.py:read_temp", "name": "temp", "unit": "°C", "interval": 5}
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_KEEP = object()  # Sensor(interval=None) means on demand, so absence needs its own marker


@dataclass
class Reading:
    sensor: str
    value: Any
    unit: str = ""
    at: float = field(default_factory=time.time)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.at)

    def text(self) -> str:
        """One line for a prompt or a log: ``thermometer: 12.4 °C (3 s ago)``."""
        if not self.ok:
            return f"{self.sensor}: unavailable ({self.error})"
        if isinstance(self.value, dict):
            body = ", ".join(f"{k}={_fmt(v)}" for k, v in self.value.items())
        else:
            body = f"{_fmt(self.value)} {self.unit}".strip()
        return f"{self.sensor}: {body} ({self.age:.0f} s ago)"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"sensor": self.sensor, "value": self.value, "unit": self.unit, "at": self.at}
        if self.error:
            d["error"] = self.error
        return d


def describe_readings(readings: dict[str, Any]) -> str:
    """One line for a log or a prompt from ``{name: Reading.to_dict()}``:
    ``soil_moisture=24 % (1 s ago); soil_temperature=21 °C (1 s ago)``."""
    parts = []
    for name, r in readings.items():
        if not isinstance(r, dict):
            parts.append(f"{name}={_fmt(r)}")
            continue
        if r.get("error"):
            parts.append(f"{name}: unavailable ({r['error']})")
            continue
        value = r.get("value")
        if isinstance(value, dict):
            value = ", ".join(f"{k}={_fmt(v)}" for k, v in value.items())
        else:
            value = _fmt(value)
        age = f" ({max(0.0, time.time() - float(r['at'])):.0f} s ago)" if r.get("at") else ""
        parts.append(f"{name}={value} {r.get('unit', '')}".rstrip() + age)
    return "; ".join(parts) or "(no readings)"


class Sensor:
    name: str = ""
    description: str = ""  # one line the LLM reads: what this measures
    unit: str = ""
    interval: float | None = None  # poll every N seconds; None = on demand only

    def __init__(self, *, name: str | None = None, description: str | None = None, unit: str | None = None,
                 interval: Any = _KEEP):
        """The profile's per-instance overrides of the class attributes."""
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if unit is not None:
            self.unit = unit
        if interval is not _KEEP:
            self.interval = interval

    async def read(self) -> Reading:
        raise NotImplementedError

    async def run(self, publish: Callable[[Reading], None]) -> None:
        """Deliver readings on your own schedule. Default: poll every ``interval``."""
        if self.interval is None:
            return
        while True:
            publish(await self.safe_read())
            await asyncio.sleep(self.interval)

    async def safe_read(self) -> Reading:
        try:
            return await self.read()
        except Exception as exc:  # a driver bug must not take the agent down
            return self.failed(f"{type(exc).__name__}: {exc}")

    # helpers for implementations
    def reading(self, value: Any, unit: str | None = None) -> Reading:
        return Reading(self.name, value, self.unit if unit is None else unit)

    def failed(self, error: str) -> Reading:
        return Reading(self.name, None, self.unit, error=error)


class FunctionSensor(Sensor):
    """Adapts a function (sync or async) returning a value or a Reading."""

    def __init__(self, fn: Callable[[], Any], name: str, description: str = "", unit: str = "",
                 interval: float | None = None):
        self.fn = fn
        self.name = name
        self.description = description or (inspect.getdoc(fn) or "").split("\n")[0]
        self.unit = unit
        self.interval = interval

    async def read(self) -> Reading:
        result = self.fn()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Reading):
            return result
        return self.reading(result)


def _fmt(v: Any) -> str:
    return f"{v:.1f}" if isinstance(v, float) else str(v)
