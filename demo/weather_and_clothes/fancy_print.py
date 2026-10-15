"""The demo's trigger: shows the outfit as a card in the terminal.

This is the whole contract a trigger needs: ``name``, ``description`` (the
LLM reads it to decide when to use the trigger), ``input_schema`` (what the
LLM must provide — validated before ``fire`` runs) and ``fire()``. A real
one would write to a display over UART or flip a relay. Listed in the
profile as::

    {"type": "fancy_print.py:FancyPrint"}
"""

from __future__ import annotations

import textwrap

from loquor.triggers import Trigger

WIDTH = 58


class FancyPrint(Trigger):
    name = "show_outfit"
    description = (
        "Show the person an outfit card: tomorrow's forecast in one line and the "
        "list of clothes to wear. Use it once the recommendation is final."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "forecast": {"type": "string", "description": "the forecast in one short line"},
            "items": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                      "description": "clothes to wear, one per entry"},
            "note": {"type": "string", "description": "optional one-line tip"},
        },
        "required": ["forecast", "items"],
    }

    def __init__(self, name: str = "show_outfit"):
        self.name = name

    async def fire(self, input):
        lines = ["👕  OUTFIT FOR TOMORROW", ""]
        lines += textwrap.wrap(f"🌤   {input['forecast']}", WIDTH - 4)
        lines.append("")
        for item in input["items"]:
            lines += textwrap.wrap(f"  •  {item}", WIDTH - 4, subsequent_indent="     ")
        if input.get("note"):
            lines.append("")
            lines += textwrap.wrap(f"💡  {input['note']}", WIDTH - 4)
        bar = "─" * (WIDTH - 2)
        card = [f"╭{bar}╮"] + [f"│ {line:<{WIDTH - 3}}│" for line in lines] + [f"╰{bar}╯"]
        print("\n" + "\n".join(card) + "\n", flush=True)
        return {"items": len(input["items"])}
