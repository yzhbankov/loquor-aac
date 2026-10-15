"""Answer free-text questions with the reasoner, guided by a system prompt.

Input ``{"question": str}`` → output ``{"answer": str}``. With ``sensors``
set, the current readings go into the system prompt (at ``{sensors}`` or
appended), so the answer is grounded in what the device measures.
"""

from __future__ import annotations

from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill, fill_placeholders, with_sensors


class LlmAnswer(Skill):
    def __init__(
        self,
        capability: str,
        system_prompt: str,
        temperature: float = 0.2,
        sensors: list[str] | None = None,
        max_chars: int = 3000,  # keeps the response well under the envelope cap
    ):
        self.capability = capability
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.sensors = tuple(sensors or ())
        self.max_chars = max_chars

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        question = str(input.get("question") or "").strip()
        if not question:
            raise ValueError("input.question is required")
        system = fill_placeholders(self.system_prompt)
        if self.sensors:
            readings = await ctx.sensors.readings(self.sensors)
            system = with_sensors(system, ctx.sensors.describe(readings))
        answer = await ctx.reasoner.complete(
            system=system,
            prompt=f"Another agent asks: {question}",
            temperature=self.temperature,
        )
        return {"answer": answer.strip()[: self.max_chars]}
