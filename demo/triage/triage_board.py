"""The nurse's trigger: the triage board on the laptop screen.

Stand-in for a ward display or a pager. The contract is the same as any
trigger: ``name``, ``description``, ``input_schema``, ``fire``.

    {"type": "triage_board.py:TriageBoard"}
"""

from __future__ import annotations

import textwrap

from loquor.triggers import Trigger

WIDTH = 72
BADGE = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🟢", 5: "🟢"}


class TriageBoard(Trigger):
    name = "triage_board"
    description = "Show the nurse the triage board: patients in the order to see them, with recommendations."
    input_schema = {
        "type": "object",
        "properties": {
            "patients": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chair": {"type": "string"},
                        "priority": {"type": "integer", "minimum": 1},
                        "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                        "urgency": {"type": "string"},
                        "concern": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["chair", "priority"],
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
        for p in input["patients"]:
            badge = BADGE.get(p.get("severity", 3), "⚪")
            head = f"{p['priority']}. {badge} {p['chair']}"
            if "severity" in p:
                head += f"   severity {p['severity']}, {p.get('urgency', '')}"
            lines.append(head)
            if p.get("concern"):
                lines += textwrap.wrap(f"      {p['concern']}", WIDTH - 4, subsequent_indent="      ")
            if p.get("reason"):
                lines += textwrap.wrap(f"      ↳ {p['reason']}", WIDTH - 4, subsequent_indent="        ")
            lines.append("")
        if input.get("recommendations"):
            lines.append("Recommendations")
            for rec in input["recommendations"]:
                lines += textwrap.wrap(f"  • {rec}", WIDTH - 4, subsequent_indent="    ")
        bar = "─" * (WIDTH - 2)
        card = [f"╭{bar}╮"] + [f"│ {line:<{WIDTH - 3}}│" for line in lines] + [f"╰{bar}╯"]
        print("\n" + "\n".join(card) + "\n", flush=True)
        return {"patients": len(input["patients"])}
