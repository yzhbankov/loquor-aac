"""Put the agent's sensors on the mesh: a peer asks, gets the numbers.

Input ``{"sensor": name}`` for one, or ``{}`` for all this skill exposes →
output ``{"readings": {name: {value, unit, at, error?}}}``. Structured, so a
peer's skill can compare values instead of parsing prose.
"""

from __future__ import annotations

from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill


class SensorReport(Skill):
    capability = "sensor_report"
    description = "Latest readings from this agent's sensors."
    input_schema = {"type": "object", "properties": {"sensor": {"type": "string"}}}
    output_schema = {"type": "object", "properties": {"readings": {"type": "object"}}, "required": ["readings"]}

    def __init__(self, sensors: list[str] | None = None, min_trust: int = 0):
        self.sensors = tuple(sensors or ())  # () = every sensor the agent has
        self.min_trust = min_trust

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        exposed = list(self.sensors) or ctx.sensors.names()
        wanted = [str(input["sensor"])] if input.get("sensor") else exposed
        unknown = [n for n in wanted if n not in exposed]
        if unknown:
            raise ValueError(f"no such sensor: {', '.join(unknown)}; have {', '.join(exposed)}")
        readings = await ctx.sensors.readings(wanted)
        return {"readings": {r.sensor: r.to_dict() for r in readings}}
