"""The nurse's trigger: the triage board on the laptop screen.

Stand-in for a ward display or a pager. Like every trigger: ``name``,
``description`` (the LLM reads it), ``input_schema`` (what the LLM must
provide; validated before ``fire`` runs) and ``fire()``.

    {"type": "triage_board.py:TriageBoard"}
"""

from __future__ import annotations

import textwrap

from loquor.triggers import Trigger

WIDTH = 70


class TriageBoard(Trigger):
    name = "triage_board"
    description = (
        "Show the nurse the triage board: the patients in the order to see them, "
        "each with the chair name and a one-line reason, plus short recommendations."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "patients": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "chair": {"type": "string", "description": "the chair's name as announced"},
                        "reason": {"type": "string", "description": "why this position: condition and urgency"},
                    },
                    "required": ["chair", "reason"],
                },
            },
            "recommendations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["patients"],
    }

    def __init__(self, name: str = "triage_board"):
        self.name = name

    async def fire(self, input):
        lines = ["🏥  TRIAGE BOARD — see patients in this order", ""]
        for i, p in enumerate(input["patients"], 1):
            lines.append(f"{i}. {p['chair']}")
            lines += textwrap.wrap(f"      {p['reason']}", WIDTH - 4, subsequent_indent="      ")
            lines.append("")
        if input.get("recommendations"):
            lines.append("Recommendations")
            for rec in input["recommendations"]:
                lines += textwrap.wrap(f"  • {rec}", WIDTH - 4, subsequent_indent="    ")
        bar = "─" * (WIDTH - 2)
        card = [f"╭{bar}╮"] + [f"│ {line:<{WIDTH - 3}}│" for line in lines] + [f"╰{bar}╯"]
        print("\n" + "\n".join(card) + "\n", flush=True)
        return {"patients": len(input["patients"])}
