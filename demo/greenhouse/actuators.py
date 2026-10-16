"""The controller's own trigger: the roof vent.

A stand-in for a relay: a ``description`` the LLM reads, an
``input_schema`` it must satisfy, and ``fire()`` that runs only after the
input has been validated. Replace ``print`` with a GPIO call and the rest
stays the same. The valve is not here: it is on the tank node, which
offers it (see ``node_tank.json``), and the controller fires it remotely.
"""

from __future__ import annotations

from loquor.triggers import Trigger


class Vent(Trigger):
    name = "vent"
    description = "Open or close the roof vent. Open it when the air is too hot; close it when it has cooled."
    input_schema = {
        "type": "object",
        "properties": {"open": {"type": "boolean"}},
        "required": ["open"],
    }

    async def fire(self, input):
        print(f"\n🌬   VENT {'OPEN' if input['open'] else 'CLOSED'}\n", flush=True)
        return {"open": input["open"]}
