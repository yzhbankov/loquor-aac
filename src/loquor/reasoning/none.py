"""No LLM at all: for sensor nodes that only measure and report.

A node runs the full protocol — beacons, handshake, encryption, requests —
and answers with structured data through skills that need no model
(``sensor_report``). It needs no Ollama, no model and a fraction of the
memory, so it fits a Raspberry Pi Zero or any small Linux board. Any skill
that does call the reasoner fails with a clear message; ``build_agent``
refuses such a profile at startup.
"""

from __future__ import annotations

from loquor.reasoning.base import Reasoner


class NoReasoner(Reasoner):
    name = "none"

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        raise RuntimeError("this agent has no LLM (reasoner \"none\"); only skills that need no model can run here")
