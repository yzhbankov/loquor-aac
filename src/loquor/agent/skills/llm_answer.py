"""Answer free-text questions with the reasoner, guided by a system prompt.

Input ``{"question": str}`` → output ``{"answer": str}``.
"""

from __future__ import annotations

from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill, fill_placeholders


class LlmAnswer(Skill):
    def __init__(
        self,
        capability: str,
        system_prompt: str,
        temperature: float = 0.2,
        max_chars: int = 3000,  # keeps the response under the protocol's size cap
    ):
        self.capability = capability
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_chars = max_chars

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        question = str(input.get("question") or "").strip()
        if not question:
            raise ValueError("input.question is required")
        answer = await ctx.reasoner.complete(
            system=fill_placeholders(self.system_prompt),
            prompt=f"Another agent asks: {question}",
            temperature=self.temperature,
        )
        return {"answer": answer.strip()[: self.max_chars]}
