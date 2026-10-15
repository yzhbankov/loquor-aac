"""Deterministic reasoners for runs without an LLM."""

from __future__ import annotations

import logging

from loquor.reasoning.base import Reasoner

log = logging.getLogger("loquor")


class TemplateReasoner(Reasoner):
    """Echoes the prompt as a readable summary. No model, no network."""

    name = "template"

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        return "Summary (deterministic, no local LLM attached): " + " ".join(prompt.split())[:600]


class FallbackReasoner(Reasoner):
    """Use ``primary``; if it fails at runtime, answer with templates instead."""

    def __init__(self, primary: Reasoner):
        self.primary = primary
        self.fallback = TemplateReasoner()
        self.name = primary.name

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        try:
            return await self.primary.complete(system, prompt, temperature=temperature, schema=schema)
        except Exception as exc:
            log.warning("%s reasoner failed (%s); using template", self.primary.name, exc)
            return await self.fallback.complete(system, prompt)
