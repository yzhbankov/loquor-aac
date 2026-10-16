"""``loquor init``: a working agent to start from.

Writes a folder with a profile, a prompt, one sensor file with a function
and a class, and one trigger — all runnable as they are, each marked where
your own hardware goes. ``loquor check`` then ``loquor run`` and it is on
the air.
"""

from __future__ import annotations

from pathlib import Path

from loquor.protocol.discovery import slug

SCHEMA_URL = "https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/src/loquor/profile.schema.json"

PROFILE = """{{
  "$schema": "{schema}",
  "name": "{name}",
  "description": "I am {name}. I report the load and temperature at my device and can show a message to the person next to it.",
  "system_prompt_file": "prompt.txt",
  "sensors": [
    {{ "type": "sensors.py:load_average", "name": "load", "interval": 10 }},
    {{ "type": "sensors.py:Thermometer", "name": "temperature", "interval": 10 }}
  ],
  "triggers": [
    {{ "type": "triggers.py:Notify" }}
  ],
  "skills": [
    {{ "type": "llm_answer", "capability": "{capability}", "sensors": ["load", "temperature"] }},
    {{ "type": "sensor_report" }}
  ]
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

Each one is listed in ``agent.json`` under ``"sensors"`` with a ``name`` and an
``interval`` (seconds between readings; leave it out to read only on demand).
The LLM reads the description (the docstring, or ``description``) to know
what the number means.
"""

from __future__ import annotations

import os
import random

from loquor.sensors import Sensor


def load_average():
    """System load over the last minute (1.0 = one busy core)."""
    return round(os.getloadavg()[0], 2)


class Thermometer(Sensor):
    description = "Temperature at the device."
    unit = "°C"

    def __init__(self, name: str = "temperature", interval: float | None = None):
        self.name = name
        self.interval = interval
        # Open your instrument here: a serial port, an I²C bus, a GPIO pin…

    async def read(self):
        # Replace the next line with a reading from your instrument. For a
        # Raspberry Pi's CPU sensor, for example:
        #     return self.reading(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000)
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

An agent made by `loquor init`. Everything in this folder is yours to change.

| file | what it is |
|---|---|
| `agent.json` | the profile: who the agent is and which parts it has |
| `prompt.txt` | how its LLM behaves (`{{sensors}}` is replaced by the current readings) |
| `sensors.py` | what it measures: a function and a class, both runnable as they are |
| `triggers.py` | what it can do: one action with a schema the LLM is held to |

```bash
loquor check {dir}/agent.json     # loads everything, reads each sensor once, checks the model
loquor run   {dir}/agent.json     # on the air; Ctrl-C to stop
loquor status "{name}"            # from another terminal: peers, readings, LLM calls
```

Next steps, in the order people usually take them:

1. **Read your own instrument** in `sensors.py` (`Thermometer.read`) and
   **drive your own hardware** in `triggers.py` (`Notify.fire`).
2. **Change the persona** in `prompt.txt` and the `description` in `agent.json`:
   other agents' LLMs read the description to decide whether to ask you.
3. **Give it a goal** that uses other agents and your trigger — add to `"skills"`:

   ```json
   {{ "type": "goal",
     "goal": "Find out whether any device nearby is overheating and warn the person here if so.",
     "triggers": ["notify"] }}
   ```

4. **Run it on another device** too: the same folder, `loquor run`, and the
   two find each other over the transport in `loquor config show`.

Reference: `docs/USAGE.md` (profile fields, commands) and `docs/DEVELOPMENT.md`
(sensor, trigger and skill contracts).
"""


NODE_PROFILE = """{{
  "$schema": "{schema}",
  "name": "{name}",
  "description": "I am {name}, a sensor node. I measure the load and temperature at my device and report the numbers to any agent that asks.",
  "sensors": [
    {{ "type": "sensors.py:load_average", "name": "load", "interval": 10 }},
    {{ "type": "sensors.py:Thermometer", "name": "temperature", "interval": 10 }}
  ],
  "skills": [
    {{ "type": "sensor_report" }}
  ]
}}
"""

NODE_README = """# {name}

A **sensor node** made by `loquor init --node`: it measures and reports,
and has no LLM. It runs the same protocol as a full agent — beacons,
handshake, encryption — and answers `sensor_report` requests with its
readings as data. No Ollama, no model: a Raspberry Pi Zero is enough
(`./install.sh --node` on the device).

| file | what it is |
|---|---|
| `agent.json` | the profile: the node's name, description and sensors |
| `sensors.py` | what it measures: a function and a class, both runnable as they are |

```bash
loquor check {dir}/agent.json     # loads everything and reads each sensor once
loquor run   {dir}/agent.json     # on the air; Ctrl-C to stop
loquor status "{name}"            # from another terminal: peers and readings
```

Next steps:

1. **Read your own instrument** in `sensors.py` (`Thermometer.read`), or
   wire a shell command with no Python at all:
   `{{ "type": "command", "name": "cpu_temp", "command": "vcgencmd measure_temp | tr -dc '0-9.'", "unit": "°C", "interval": 10 }}`.
2. **Say what you measure** in `description`: the agents that find this
   node read it to decide whether your numbers are what they need.
3. **Point an agent at it**: on a device with an LLM, a profile with
   `{{ "type": "collect", "interval": 30, "goal": "…", "triggers": [...] }}`
   pulls the readings from every node in range and acts on them
   (`loquor init` writes an agent; see `demo/greenhouse` for a complete one).
4. **Keep it running**: `loquor service install {dir}/agent.json --now`.

Reference: `docs/USAGE.md`.
"""


def init(directory: Path, name: str | None = None, *, force: bool = False, node: bool = False) -> list[Path]:
    """Write the starter files into ``directory``; returns the paths written."""
    directory = Path(directory)
    name = name or directory.resolve().name.replace("_", " ").replace("-", " ").strip().title() or "My Agent"
    capability = f"{slug(name).replace('-', '_') or 'device'}_status"
    shown = directory.as_posix().rstrip("/") or "."
    if node:
        files = {
            "agent.json": NODE_PROFILE.format(schema=SCHEMA_URL, name=name),
            "sensors.py": SENSORS.format(name=name),
            "README.md": NODE_README.format(name=name, dir=shown),
        }
    else:
        files = {
            "agent.json": PROFILE.format(schema=SCHEMA_URL, name=name, capability=capability),
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
