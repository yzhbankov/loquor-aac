"""``ctx.sensors``: the agent's sensors and their latest readings.

The hub keeps the latest :class:`Reading` per sensor. Polled and pushed
sensors fill it on their own (``Sensor.run``); on-demand sensors are read
when a skill asks. Skills never touch a sensor object directly.

The hub also holds the sensors of nodes in range (``soil-node:moisture``),
added and removed by the agent as peers come and go. They are on-demand:
reading one asks the node. A name that is not (or no longer) in the hub
reads as unavailable instead of failing, since a node may be out of range.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable

from loquor.sensors.base import Reading, Sensor

log = logging.getLogger("loquor")


class SensorHub:
    def __init__(self, sensors: Iterable[Sensor] = ()):
        self._sensors: dict[str, Sensor] = {}
        for sensor in sensors:
            if not sensor.name:
                raise ValueError(f"sensor {type(sensor).__name__} has no name")
            if sensor.name in self._sensors:
                raise ValueError(f"two sensors named {sensor.name!r}")
            self._sensors[sensor.name] = sensor
        self._latest: dict[str, Reading] = {}

    def names(self) -> list[str]:
        return list(self._sensors)

    def local_names(self) -> list[str]:
        """This device's own sensors, without the peers' mirrored here."""
        return [n for n, s in self._sensors.items() if not getattr(s, "remote", False)]

    def add(self, sensor: Sensor) -> None:
        """Register a sensor at runtime (a peer's, discovered on the mesh)."""
        if sensor.name in self._sensors:
            raise ValueError(f"two sensors named {sensor.name!r}")
        self._sensors[sensor.name] = sensor

    def remove(self, name: str) -> None:
        self._sensors.pop(name, None)
        self._latest.pop(name, None)

    def __contains__(self, name: object) -> bool:
        return name in self._sensors

    def __len__(self) -> int:
        return len(self._sensors)

    def get(self, name: str) -> Sensor:
        return self._sensors[name]

    def latest(self, name: str) -> Reading | None:
        """The last stored reading, without touching the device."""
        return self._latest.get(name)

    async def read(self, name: str) -> Reading:
        """A fresh reading from the device; also stored."""
        reading = await self._sensors[name].safe_read()
        self.publish(reading)
        return reading

    async def readings(self, names: Iterable[str] | None = None) -> list[Reading]:
        """Current readings: stored ones for polled sensors, fresh for on-demand ones (read in parallel)."""
        names = list(names if names is not None else self.names())

        async def one(name: str) -> Reading:
            sensor = self._sensors.get(name)
            if sensor is None:
                return Reading(name, None, error="not in range")
            reading = self._latest.get(name)
            if reading is None or sensor.interval is None:
                reading = await self.read(name)
            return reading

        return list(await asyncio.gather(*(one(n) for n in names)))

    def snapshot(self) -> dict[str, Reading]:
        return dict(self._latest)

    def publish(self, reading: Reading) -> None:
        self._latest[reading.sensor] = reading
        log.debug("SENSOR %s", reading.text())

    @staticmethod
    def describe(readings: Iterable[Reading]) -> str:
        """Prompt-ready lines, one per reading."""
        return "\n".join(r.text() for r in readings) or "(no sensors)"

    def start(self, spawn: Callable) -> None:
        """Run every sensor's delivery loop (polled or pushed) as a task."""
        for sensor in self._sensors.values():
            spawn(sensor.run(self.publish))
