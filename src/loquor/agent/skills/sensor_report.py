"""Put the agent's sensors on the mesh: a peer asks, gets the numbers.

Input ``{"sensor": name}`` for one, or ``{}`` for all this skill exposes →
output ``{"readings": {name: {value, unit, at, error?}}}``. Structured, so a
peer's skill can compare values instead of parsing prose.

The capability card lists the exposed sensors (name, unit, description),
so an agent knows what this device measures before asking, and mirrors
them as ``<device>:<sensor>`` in its own sensor hub.
"""

from __future__ import annotations

from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill


class SensorReport(Skill):
    capability = "sensor_report"
    description = "Latest readings from this agent's sensors."
    needs_reasoner = False  # pure data: this is what a sensor node offers
    input_schema = {"type": "object", "properties": {"sensor": {"type": "string"}}}
    output_schema = {"type": "object", "properties": {"readings": {"type": "object"}}, "required": ["readings"]}

    def __init__(self, sensors: list[str] | None = None, min_trust: int = 0):
        self.sensors = tuple(sensors or ())  # () = every sensor the agent has
        self.min_trust = min_trust

    def exposed(self, hub) -> list[str]:
        return [n for n in (self.sensors or hub.local_names()) if n in hub]

    def advertise(self, agent: Any) -> dict[str, Any]:
        hub = agent.sensors
        return {"sensors": {
            name: {k: v for k, v in (("unit", hub.get(name).unit), ("description", hub.get(name).description)) if v}
            for name in self.exposed(hub)
        }}

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        exposed = self.exposed(ctx.sensors)
        wanted = [str(input["sensor"])] if input.get("sensor") else exposed
        unknown = [n for n in wanted if n not in exposed]
        if unknown:
            raise ValueError(f"no such sensor: {', '.join(unknown)}; have {', '.join(exposed)}")
        readings = await ctx.sensors.readings(wanted)
        return {"readings": {r.sensor: r.to_dict() for r in readings}}
