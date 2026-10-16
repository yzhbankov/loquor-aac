"""The controller's triggers: the pump and the roof vent.

Stand-ins for two relays. Each is one class: a ``description`` the LLM
reads, an ``input_schema`` it must satisfy, and ``fire()`` that runs only
after the input has been validated. Replace ``print`` with a GPIO call
and the rest stays the same.
"""

from __future__ import annotations

from loquor.triggers import Trigger


class Pump(Trigger):
    name = "pump"
    description = (
        "Run the irrigation pump for a number of seconds to water the beds. "
        "Use it when soil moisture is low and the tank has water."
    )
    input_schema = {
        "type": "object",
        "properties": {"seconds": {"type": "integer", "minimum": 5, "maximum": 120}},
        "required": ["seconds"],
    }

    async def fire(self, input):
        print(f"\n💧  PUMP ON for {input['seconds']} s\n", flush=True)
        return {"watered_for_s": input["seconds"]}


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
