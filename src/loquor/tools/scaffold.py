"""``loquor init``: a working agent to start from.

Writes a folder with a profile, a prompt, one sensor file with a function
and a class, and one trigger — all runnable as they are, each marked where
your own hardware goes. ``loquor check`` then ``loquor run`` and it is on
the air. ``--node`` writes the same without a brain (and without a prompt).
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_URL = "https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/src/loquor/profile.schema.json"

PROFILE = """{{
  "$schema": "{schema}",
  "name": "{name}",
  "description": "I am {name}. I report the load and temperature at my device and can show a message to the person next to it.",
  "sensors": [
    {{ "type": "sensors.py:load_average", "name": "load", "interval": 10 }},
    {{ "type": "sensors.py:Thermometer", "interval": 10 }}
  ],
  "triggers": [
    {{ "type": "triggers.py:Notify" }}
  ],
  "brain": {{
    "prompt_file": "prompt.txt"
  }}
}}
"""

PROMPT = """You are {name}, an agent running on a device in an offline local network.
Other agents ask you questions; answer from what your sensors read right now:

{{sensors}}

Answer in one or two short sentences with the numbers that matter.
Do not mention sensors, readings or that you are an AI.
"""

SENSORS = '''"""Sensors for {name}.

A sensor answers one question: *what do you measure right now?*
Two shapes, pick whichever is simpler for you:

- a **function** that returns the value (a number, a string, or a flat dict);
- a **class** that subclasses ``Sensor`` and returns ``self.reading(value)`` from ``read()``.

Each one is listed in ``agent.json`` under ``"sensors"``; ``name`` and
``interval`` (seconds between readings; leave it out to read only on demand)
there override the class's own. The LLM reads the description (the
docstring, or ``description``) to know what the number means.

Many devices need no Python: ``command``, ``file``, ``http``, ``gpio`` and
``serial`` sensors are built in (docs/HARDWARE.md).
"""

from __future__ import annotations

import os
import random

from loquor.sensors import Sensor


def load_average():
    """System load over the last minute (1.0 = one busy core)."""
    return round(os.getloadavg()[0], 2)


class Thermometer(Sensor):
    name = "temperature"
    description = "Temperature at the device."
    unit = "°C"

    async def read(self):
        # Replace the next line with a reading from your instrument. For a
        # Raspberry Pi's CPU sensor, for example:
        #     return self.reading(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000)
        # or skip Python in agent.json:
        #     {{ "type": "file", "name": "temperature", "path": "/sys/class/thermal/thermal_zone0/temp", "scale": 0.001 }}
        # Blocking calls go through ``await asyncio.to_thread(...)``. On any
        # failure return ``self.failed("why")`` instead of raising: a broken
        # sensor is "no data", not a crash.
        return self.reading(round(random.uniform(18, 24), 1))
'''

TRIGGERS = '''"""Triggers for {name}.

A trigger is one action the device can take. The LLM never runs it directly:
it *proposes* a trigger and an input, the agent validates the input against
``input_schema`` and only then calls ``fire()``. So ``fire()`` can trust
what it receives, and ``description`` must tell the LLM what the action is
for and when to use it.

Try it by hand: ``loquor fire agent.json notify '{{"message": "hello"}}'``.
"""

from __future__ import annotations

from loquor.triggers import Trigger


class Notify(Trigger):
    name = "notify"
    description = "Show a short message to the person at this device. Use it for anything they must know now."
    input_schema = {{
        "type": "object",
        "properties": {{
            "message": {{"type": "string", "description": "one or two sentences"}},
            "level": {{"type": "string", "enum": ["info", "warning", "alert"], "default": "info"}},
        }},
        "required": ["message"],
    }}

    async def fire(self, input):
        # Drive your hardware here: a display, a relay, a buzzer, an HTTP call…
        # ``input`` already matches ``input_schema``. Raise on failure.
        print(f"[{{input.get('level', 'info').upper()}}] {{input['message']}}", flush=True)
        return {{"shown": True}}
'''

README = """# {name}

An **agent** made by `loquor init`: a device with sensors, a trigger and a
**brain** (an LLM). Everything in this folder is yours to change.

| file | what it is |
|---|---|
| `agent.json` | the profile: name, description, sensors, triggers, brain |
| `prompt.txt` | how its LLM behaves (`{{sensors}}` is replaced by the current readings) |
| `sensors.py` | what it measures: a function and a class, both runnable as they are |
| `triggers.py` | what it can do: one action with a schema the LLM is held to |

```bash
loquor check {dir}/agent.json     # loads everything, reads each sensor once, checks the model
loquor read  {dir}/agent.json     # just the sensors, again
loquor fire  {dir}/agent.json notify '{{"message": "hello"}}'   # a trigger, by hand
loquor run   {dir}/agent.json     # on the air; Ctrl-C to stop
loquor status "{name}"            # from another terminal: peers, readings, LLM calls
```

As it is, the agent **answers** other agents' questions from its sensors
and shares its readings with them as data. Next steps:

1. **Read your own instrument** in `sensors.py` (`Thermometer.read`) and
   **drive your own hardware** in `triggers.py` (`Notify.fire`) — or use a
   built-in driver with no Python (`command`, `file`, `http`, `gpio`,
   `serial`; docs/HARDWARE.md).
2. **Change the persona** in `prompt.txt` and the `description` in `agent.json`:
   other agents' LLMs read the description to decide whether to ask you.
3. **Give the brain a goal**, and it reads what it needs — its own sensors
   and those of nodes in range —, asks agents that can help, and acts:

   ```json
   "brain": {{
     "prompt_file": "prompt.txt",
     "goal": "Warn the person here if this device or any device nearby is overheating.",
     "every": 60,
     "act": ["notify"]
   }}
   ```

4. **Add a node** for a device without an LLM: `loquor init my_node --node`
   on it. Its sensors appear here as `<node-name>:<sensor>` and its triggers
   as `<node-name>:<trigger>`, for the brain to read and fire.

Reference: docs/CONCEPTS.md (how it fits together), docs/USAGE.md (every field).
"""


NODE_PROFILE = """{{
  "$schema": "{schema}",
  "name": "{name}",
  "description": "I am {name}, a sensor node. I measure the load and temperature at my device and can switch my relay.",
  "sensors": [
    {{ "type": "sensors.py:load_average", "name": "load", "interval": 10 }},
    {{ "type": "sensors.py:Thermometer", "interval": 10 }}
  ],
  "triggers": [
    {{ "type": "triggers.py:Relay" }}
  ]
}}
"""

NODE_TRIGGERS = '''"""Triggers for {name}: what this node can do when an agent tells it to.

A node has no LLM. It shares its triggers with the agents in range (name,
description and input schema, in its capability card); an agent shows them
to its own LLM as ``<node-name>:relay`` next to its own triggers, and when
it decides to use one it sends ``fire_trigger`` here. The input is
validated against ``input_schema`` on both sides before ``fire()`` runs.

Any agent in range may fire them — fine for a demo. For a real actuator add
``"share": {{"min_trust": 1}}`` to ``agent.json`` and pin the controller's
key in ``~/.loquor/pinned_keys.json`` so only that agent can.

Try it by hand: ``loquor fire agent.json relay '{{"on": true}}'``.
"""

from __future__ import annotations

from loquor.triggers import Trigger


class Relay(Trigger):
    name = "relay"
    description = "Switch the relay on this device on or off, for example a lamp, a fan or a valve."
    input_schema = {{
        "type": "object",
        "properties": {{"on": {{"type": "boolean"}}}},
        "required": ["on"],
    }}

    async def fire(self, input):
        # Drive your hardware here (GPIO, serial, HTTP…). ``input`` is already valid.
        # Or skip Python in agent.json: {{ "type": "gpio", "name": "relay", "pin": 17, "description": "…" }}
        print(f"[RELAY] {{'ON' if input['on'] else 'OFF'}}", flush=True)
        return {{"on": input["on"]}}
'''

NODE_README = """# {name}

A **node** made by `loquor init --node`: a device with sensors and a
trigger, and **no brain** — no LLM, no Ollama; a Raspberry Pi Zero is
enough (`./install.sh --node` on the device). It runs the same protocol as
an agent — beacons, handshake, encryption — and shares its sensors and
triggers with every agent in range.

| file | what it is |
|---|---|
| `agent.json` | the profile: name, description, sensors, triggers — no brain |
| `sensors.py` | what it measures: a function and a class, both runnable as they are |
| `triggers.py` | what it can do when an agent tells it to: one relay |

```bash
loquor check {dir}/agent.json     # loads everything and reads each sensor once
loquor read  {dir}/agent.json     # just the sensors, again
loquor fire  {dir}/agent.json relay '{{"on": true}}'   # the relay, by hand
loquor run   {dir}/agent.json     # on the air; Ctrl-C to stop
loquor status "{name}"            # from another terminal: peers and readings
```

Next steps:

1. **Read your own instrument**: edit `sensors.py`, or use a built-in
   driver with no Python, e.g. a Raspberry Pi's CPU temperature:
   `{{ "type": "file", "name": "cpu_temp", "path": "/sys/class/thermal/thermal_zone0/temp", "scale": 0.001, "unit": "°C", "interval": 10 }}`
   (docs/HARDWARE.md has one recipe per interface).
2. **Say what you measure** in `description` and in each sensor's
   `description`: agents read them to decide whether your numbers are what
   they need.
3. **Drive your own hardware** in `triggers.py` (`Relay.fire`), or with
   `{{ "type": "gpio", "name": "relay", "pin": 17, "description": "…" }}`, and
   say in its `description` when it should be used: the agent's LLM reads it.
4. **Point an agent at it**: on a device with an LLM, `loquor init my_agent`.
   That agent sees `<node-name>:temperature` among its sensors and
   `<node-name>:relay` among its triggers, and reads or fires them when its
   brain needs to (`demo/greenhouse` is a complete example).
5. **Keep it running**: `loquor service install {dir}/agent.json --now`.

Reference: docs/CONCEPTS.md, docs/USAGE.md.
"""


def init(directory: Path, name: str | None = None, *, force: bool = False, node: bool = False) -> list[Path]:
    """Write the starter files into ``directory``; returns the paths written."""
    directory = Path(directory)
    name = name or directory.resolve().name.replace("_", " ").replace("-", " ").strip().title() or "My Agent"
    shown = directory.as_posix().rstrip("/") or "."
    if node:
        files = {
            "agent.json": NODE_PROFILE.format(schema=SCHEMA_URL, name=name),
            "sensors.py": SENSORS.format(name=name),
            "triggers.py": NODE_TRIGGERS.format(name=name),
            "README.md": NODE_README.format(name=name, dir=shown),
        }
    else:
        files = {
            "agent.json": PROFILE.format(schema=SCHEMA_URL, name=name),
            "prompt.txt": PROMPT.format(name=name),
            "sensors.py": SENSORS.format(name=name),
            "triggers.py": TRIGGERS.format(name=name),
            "README.md": README.format(name=name, dir=shown),
        }
    existing = [f for f in files if (directory / f).exists()]
    if existing and not force:
        raise FileExistsError(f"{directory} already has {', '.join(existing)}; use --force to overwrite")
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in files.items():
        path = directory / filename
        path.write_text(content)
        written.append(path)
    return written
