"""A peer's sensors and triggers, usable here as if they were our own.

A node lists what it shares in its capability card: ``sensor_report``
lists its sensors (name, unit, description), ``offer_triggers`` its
triggers (name, description, input schema). The agent mirrors them into
its own hubs under ``<node>:<name>`` (``tank-node:valve``,
``soil-node:soil_moisture``), so every skill uses them like local ones and
the LLM can tell two nodes' pumps apart.

- A :class:`RemoteSensor` is on demand: reading it asks the node for
  ``sensor_report``. One request serves every sensor of that node for
  ``ttl`` seconds (:class:`RemoteSource`), so a prompt with five of its
  readings costs one round trip, and only when a skill needs them.
- A :class:`RemoteTrigger` has the same contract as a local trigger, so
  the trigger hub validates the LLM's proposal here; ``fire()`` sends
  ``fire_trigger`` to the node, which validates again and acts.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from loquor.agent.tracker import RequestError
from loquor.protocol.discovery import Peer, slug
from loquor.sensors.base import Reading, Sensor
from loquor.triggers.base import Trigger, TriggerError

if TYPE_CHECKING:
    from loquor.agent.context import Context

CAPABILITY = "fire_trigger"
SENSOR_CAPABILITY = "sensor_report"
SENSOR_TTL = 5.0  # seconds a node's readings are reused before asking again


class RemoteTrigger(Trigger):
    remote = True

    def __init__(self, peer: Peer, trigger: str, description: str, input_schema: dict[str, Any], ctx: Context):
        self.peer = peer
        self.trigger = trigger
        self.name = remote_name(peer, trigger)
        self.description = f"{description} (on {peer.name})"
        self.input_schema = input_schema
        self._ctx = ctx

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await self._ctx.ask(self.peer, CAPABILITY, {"trigger": self.trigger, "input": input})
        except RequestError as exc:
            raise TriggerError(f"{self.peer.label} refused {self.trigger}: {exc.detail}") from exc
        except TimeoutError as exc:
            raise TriggerError(f"{self.peer.label} did not answer") from exc
        result = out.get("result")
        return result if isinstance(result, dict) else {"result": result}


class RemoteSource:
    """All of one node's readings, fetched with one request and reused for ``ttl`` seconds."""

    def __init__(self, peer: Peer, ctx: Context, ttl: float = SENSOR_TTL):
        self.peer = peer
        self.ttl = ttl
        self._ctx = ctx
        self._readings: dict[str, Any] = {}
        self._at = -1e9
        self._error: str | None = None
        self._lock = asyncio.Lock()

    async def reading(self, sensor: str) -> dict[str, Any] | str:
        """The node's reading of ``sensor`` as ``{value, unit, at, error?}``, or why there is none."""
        async with self._lock:  # concurrent reads of the same node share one request
            if time.monotonic() - self._at > self.ttl:
                await self._fetch()
        if self._error:
            return self._error
        found = self._readings.get(sensor)
        return found if isinstance(found, dict) else f"{self.peer.name} did not report {sensor}"

    async def _fetch(self) -> None:
        self._at = time.monotonic()
        try:
            output = await self._ctx.ask(self.peer, SENSOR_CAPABILITY, {})
        except RequestError as exc:
            self._error, self._readings = f"{self.peer.label} refused: {exc.detail}", {}
            return
        except TimeoutError:
            self._error, self._readings = f"{self.peer.label} did not answer", {}
            self._at = -1e9  # ask again next time rather than serving the failure for ttl
            return
        readings = output.get("readings")
        self._error = None if isinstance(readings, dict) else f"{self.peer.label} answered without readings"
        self._readings = readings if isinstance(readings, dict) else {}


class RemoteSensor(Sensor):
    remote = True

    def __init__(self, source: RemoteSource, sensor: str, unit: str = "", description: str = ""):
        self.source = source
        self.peer = source.peer
        self.sensor = sensor
        self.name = remote_name(source.peer, sensor)
        self.unit = unit
        self.description = f"{description or sensor} (on {source.peer.name})"
        self.interval = None  # on demand: the node is asked only when a skill reads it

    async def read(self) -> Reading:
        got = await self.source.reading(self.sensor)
        if isinstance(got, str):
            return self.failed(got)
        reading = Reading(self.name, got.get("value"), str(got.get("unit", self.unit) or ""),
                          error=got.get("error") or None)
        if isinstance(got.get("at"), (int, float)):
            reading.at = float(got["at"])
        return reading


def remote_name(peer: Peer, part: str) -> str:
    return f"{slug(peer.name) or peer.uid[:8]}:{part}"


def is_remote_name(name: str) -> bool:
    """``node:part`` names a peer's sensor or trigger; checked at runtime, not at startup."""
    return ":" in name


def remote_triggers_of(peer: Peer) -> dict[str, dict[str, Any]]:
    """``{trigger: {description, input_schema}}`` a peer offers, from its card."""
    offered = _skill_key(peer, CAPABILITY, "triggers")
    return {
        str(name): {
            "description": str(spec.get("description", "")),
            "input_schema": spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {"type": "object"},
        }
        for name, spec in offered.items() if isinstance(spec, dict)
    }


def remote_sensors_of(peer: Peer) -> dict[str, dict[str, str]]:
    """``{sensor: {unit, description}}`` a peer shares, from its card."""
    shared = _skill_key(peer, SENSOR_CAPABILITY, "sensors")
    return {
        str(name): {"unit": str(spec.get("unit", "") or ""), "description": str(spec.get("description", "") or "")}
        for name, spec in shared.items() if isinstance(spec, dict)
    }


def _skill_key(peer: Peer, capability: str, key: str) -> dict[str, Any]:
    for entry in peer.card.get("skills", []):
        if isinstance(entry, dict) and entry.get("id") == capability:
            value = entry.get(key)
            return value if isinstance(value, dict) else {}
    return {}
