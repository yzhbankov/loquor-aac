# Usage

The `loquor` command, the device config, every profile field, and how to
see what an agent is doing. Installing is in [INSTALL.md](INSTALL.md);
writing your own sensors, triggers and skills is in
[DEVELOPMENT.md](DEVELOPMENT.md).

## Commands

```
loquor doctor                        is this device ready? (Python, Ollama, model, radio)
loquor init my_agent                 a working agent to start from: profile, prompt, a sensor, a trigger
loquor init my_node --node           a sensor node: sensors + sensor_report, no LLM
loquor check my_agent/agent.json     build it, read each sensor once, check the model — nothing on the air
loquor run   my_agent/agent.json     run it until Ctrl-C
loquor status ["My Agent"]           what the running agents see, read, fired and decided
loquor trace "My Agent" [--full]     every LLM call of the last run
loquor config show | set K V | path  this device's settings
loquor service install agent.json    run it at boot (systemd on Linux, launchd on macOS)
```

`python run_agent.py …` from the repo root does the same without
installing, and a bare profile argument means `run`.

### run

```
loquor run PROFILE [--transport localhost|wifi|bluetooth] [--interface IP] [--model NAME]
                   [--visibility visible|quiet|silent] [--beacon-interval S]
                   [--wire off|no-handshake|all] [--trace | --no-trace] [-v]
```

Flags override the profile, which overrides the device config. The
first line printed is the agent's identity, model and transport; the
second is how to look at it from another terminal. The agent runs until
Ctrl-C, `kill`, or the terminal closes, and leaves the radio cleanly.

Startup refuses, with the fix, when a profile is invalid, a part cannot
be built, a skill names a sensor or trigger the agent lacks, or the
model is not pulled in Ollama.

### check

Everything `run` does short of starting the radio, plus: every sensor
is read once and shown, every trigger's description and schema are
validated, and the transport's prerequisites are checked. Exit code 1
if anything fails. Use it after every edit; it takes a second.

### doctor

The device, not a profile: Python, the package, dependencies, the state
directory, the device config, Ollama and the model, and the configured
transport (or `--transport T`). `--no-llm` skips Ollama. Exit code 1
on any failure. The Bluetooth checks on Linux read `bluetoothctl show`
and `/etc/bluetooth/main.conf`.

### status

Each running agent writes `~/.loquor/run/<name>.json` every 2 seconds;
`status` prints it:

```
clothing-advisor-33b421a7  running  pid 37836
  model      : qwen2.5:3b
  transport  : MulticastTransport (192.168.1.20)  visibility visible
  offers     : clothing_recommendation
  peers      : 1
    - weather-forecaster-55913b40  handshaken, trust 0, seen 2.1 s ago: weather_forecast, sensor_report
  sensors    : 0
  triggers   : show_outfit
    - show_outfit {"forecast": "Tomorrow: 12 °C, cloudy", "items": [...]} -> {"items": 3}
  llm calls  : 3
    #1 4.2 s  Another agent on the local network advertised itself: …  -> { "useful": true, …
  goal       : Recommend what to wear tomorrow…  [complete]
    outcome  : Tomorrow will be 12 °C and cloudy: a sweater, jeans and a light jacket.
```

`--json` gives the raw document, for scripts and dashboards. A file
whose process is gone shows as `not running`.

### trace

With tracing on (the default), every LLM call is appended to
`~/.loquor/run/<name>.trace.jsonl`: system prompt, prompt, schema,
temperature, reply, duration, or the error. `loquor trace NAME` lists
them; `--full` prints whole prompts and replies; `-n 3` the last three;
`--json` the records. The log also shows one line per call:

```
12:00:06  LLM  1.8 s  "Another agent asks: What is the load…" -> "Load is 0.4 and it is 21.3 °C here."
```

Turn it off per device (`loquor config set trace false`) or per run
(`--no-trace`); prompts contain sensor readings and peer answers, which
may be sensitive.

### config

```bash
loquor config show
loquor config set transport wifi model qwen2.5:1.5b beacon_interval 5
loquor config set wire no-handshake        # off | no-handshake | all
loquor config reset
```

See the next section for the keys.

### service

`loquor service install PROFILE [--now] [run flags]` writes a systemd
user unit (Linux) or a launchd agent (macOS) that runs `python -m loquor
run PROFILE` from the current virtualenv, restarts it on failure, and
prints the commands that enable it; `--now` runs them. `uninstall`
removes it. Details in [INSTALL.md](INSTALL.md#5-run-at-boot).

## Device config

`~/.loquor/config.json` holds what is true of the device, not of one
agent, so a profile runs unchanged on a laptop, a Pi or a robot.

| key | default | meaning |
|---|---|---|
| `transport` | `localhost` | `localhost` (agents on this machine), `wifi` (the LAN), `bluetooth` |
| `interface` | auto | `wifi`: the local IP of the interface to use, when the device has several |
| `model` | `qwen2.5:3b` | the Ollama model every agent on this device uses |
| `ollama_url` | `http://localhost:11434` | where Ollama is; another machine on the LAN works |
| `beacon_interval` | `10` | seconds between beacons; lower finds peers faster, costs radio time |
| `wire` | `off` | envelope log level: `off`, `no-handshake`, `all` |
| `trace` | `true` | record every LLM call to `~/.loquor/run/<name>.trace.jsonl` |
| `llm` | `true` | `false` on a sensor node: no Ollama here; `doctor` does not look for it and `run` refuses profiles that need one |

Precedence, highest first: command-line flag → profile → device config
→ default. `LOQUOR_MODEL` in the environment overrides the model for
one run; `LOQUOR_HOME` moves the whole `~/.loquor` directory.

## Profile

A profile is one JSON file: who the agent is and which parts it has.
`loquor init` writes a complete one. The schema is
[`src/loquor/profile.schema.json`](../src/loquor/profile.schema.json);
put its URL in `"$schema"` and VS Code validates and completes as you type.

```json
{
  "$schema": "https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/src/loquor/profile.schema.json",
  "name": "Soil Station",
  "description": "I measure soil moisture and temperature in the north field and can run the pump.",
  "system_prompt_file": "prompt.txt",
  "sensors":  [ { "type": "sensors.py:soil_moisture", "name": "soil", "unit": "%", "interval": 60 },
                { "type": "command", "name": "cpu_temp", "command": "vcgencmd measure_temp | tr -dc '0-9.'", "unit": "°C", "interval": 30 } ],
  "triggers": [ { "type": "triggers.py:Pump" } ],
  "skills":   [ { "type": "llm_answer", "capability": "field_report", "sensors": ["soil", "cpu_temp"] },
                { "type": "sensor_report" },
                { "type": "goal", "goal": "Keep the soil above 30 % moisture.", "triggers": ["pump"] } ]
}
```

### Fields

Only `name`, `description` and `skills` are required.

| field | type | meaning |
|---|---|---|
| `name` | str | human-readable name; also the key file name and the log label |
| `description` | str | one sentence other agents' LLMs read to decide whether you can help them |
| `system_prompt` | str | default persona for every skill that takes one |
| `system_prompt_file` | path | the same, read from a text file next to the profile (easier to edit than a JSON string) |
| `visibility` | `visible` \| `quiet` \| `silent` | beacon and answer policy; default `visible` |
| `beacon_interval` | number | seconds between beacons; pins the device setting |
| `key_file` | path | Ed25519 seed; default `~/.loquor/keys/<name>.key`, created on first run |
| `pinned_keys` | path | level-1 trust store; default `~/.loquor/pinned_keys.json` |
| `relay` | bool | store and forward envelopes for peers out of reach; default false |
| `transport` | part | pins a transport: `{"type": "wifi"}`; usually left to the device config |
| `reasoner` | part | pins the LLM: `{"type": "ollama", "model": "…"}`, `{"type": "template"}` or `{"type": "none"}`; usually left out: the device config picks the model, and a profile whose skills need no LLM gets `none` |
| `sensors` | [part] | drivers that produce readings; each needs a unique `name` |
| `triggers` | [part] | drivers that perform actions; each needs a unique `name` |
| `skills` | [part] | at least one; what the agent offers and pursues |

### Parts

A *part* is `{"type": T, …}` where `T` is a built-in name,
`"module:Class"` for an installed module, or `"file.py:Class"` for a
file next to the profile. Every other key is a constructor argument;
an unknown one fails at startup with the constructor's message. Sensor
and trigger types may be plain functions. Skills may use
`system_prompt_file` instead of `system_prompt`.

| part | `type` | arguments |
|---|---|---|
| skill | `llm_answer` | `capability`, `system_prompt`, `temperature` (0.2), `sensors` ([names]), `max_chars` (3000) — answers `{question}` with `{answer}`, grounded in the named sensors |
| skill | `goal` | `goal`, `system_prompt`, `temperature` (0.2), `triggers` ([names]), `min_answers` (1), `settle` (0 s) — finds peers that can help, asks them, concludes, proposes one of its triggers |
| skill | `collect` | `interval` (30 s), `capability` (`sensor_report`), `peers` ([node names]; default all), `goal`, `system_prompt`, `temperature` (0.2), `triggers` ([own names]), `remote_triggers` (true), `sensors` ([own names]), `min_nodes` (1) — pulls every node's readings each round; with a `goal` reasons over the table once per round and may fire one of its own triggers or a node's (`<node>:<trigger>`); offers the table as `collected_readings` |
| skill | `sensor_report` | `sensors` ([names]; default all), `min_trust` (0) — the readings as data, for peers that want numbers; what a **node** offers |
| skill | `offer_triggers` | `triggers` ([names]; default all), `min_trust` (0) — lets peers fire this device's triggers: they are listed in the capability card and fired on a validated `fire_trigger` request; a node that acts |
| sensor | `simulated` | `name`, `min`, `max`, `unit`, `step`, `interval`, `description`, `start` |
| sensor | `command` | `name`, `command`, `unit`, `interval`, `description`, `timeout` (10) — stdout is the value: JSON, a number, or text |
| trigger | `console` | `name` (`print`), `description`, `input_schema` (`{text}`), `template` |
| trigger | `command` | `name`, `command`, `description`, `input_schema`, `timeout` (30) — the input arrives as JSON on stdin |
| reasoner | `ollama` | `model`, `base_url`, `temperature` |
| reasoner | `template` | — (no model; deterministic text, for tests and CI) |
| reasoner | `none` | — (no LLM at all: a sensor node; chosen automatically when no skill needs one) |
| transport | `localhost`, `wifi`, `bluetooth` | `interface` (wifi), `port` (47600) |

### Nodes and agents

A profile whose skills never call a model — `sensor_report`, or
`collect` without a goal — is a **node**: it gets reasoner `none`, needs
no Ollama, and `loquor check` says `role: node (no LLM)`. It still runs
the whole protocol and answers requests with data. `loquor init --node`
writes one; `./install.sh --node` prepares a device for one (and sets
`llm false` so `doctor` stops asking for Ollama).

A node can also **act**. Give it triggers and the `offer_triggers`
skill, and it lists them — name, description, input schema — in its
capability card. An agent that meets the node sees them in its own
trigger hub as `<node>:<trigger>` (`tank-node:valve`), its LLM may propose
them like local ones, and the proposal is validated on the agent, sent
as a `fire_trigger` request, validated again on the node and fired there.
`min_trust` on `offer_triggers` says who may: 0 is anyone in range
(demos); 1 with the controller's key pinned is what a real valve wants.

An **agent** with the `collect` skill pulls readings from every node in
range: each round it sends `sensor_report` to each node and receives
`{"readings": {name: {value, unit, at}}}` in an encrypted envelope
addressed to it — no model on the node, no prose in between. With a
`goal` it then thinks once per round over the table of all nodes' readings
and its own sensors, and may fire one of its own `triggers` or one a node
offers. `loquor status` shows the table and every trigger it can reach;
`loquor trace` the prompt it was turned into.

### The LLM's behaviour

Three places shape what the LLM does, all plain text:

- **`prompt.txt`** (`system_prompt`): the persona and the rules for
  answering. `{sensors}` is replaced by the current readings of the
  sensors the skill names; `{random_number}` by a fresh 0–100 value when
  an answer must vary.
- **`description`**: what other agents' LLMs read. Say what you measure
  and do, in their terms ("I provide weather forecasts"), not yours.
- **Trigger descriptions and schemas**: what the LLM may do and the
  exact shape it must fill. The agent validates every proposal against
  the schema; a proposal that does not fit is logged and dropped.

`temperature` per skill (0 for decisions, 0.2–0.4 for prose) and the
model per device (`loquor config set model …`) are the only other knobs.

## Reading the log

The default log is the agent's story, one line per event:

```
12:00:03  my-agent-3d42fca5 met greenhouse-6b2a6c20: I am Greenhouse. I report… ['greenhouse_status']
12:00:04  greenhouse-6b2a6c20 asks for my_agent_status: What is the load and temperature at your device?
12:00:06  LLM  1.8 s  "Another agent asks: What is the load…" -> "Load is 0.4 and it is 21.3 °C here."
12:00:06  my-agent-3d42fca5: answered greenhouse-6b2a6c20: Load is 0.4 and it is 21.3 °C here.
12:00:40  my-agent-3d42fca5: reasoning about greenhouse-6b2a6c20: useful=True — it knows the greenhouse temperature
12:00:40  my-agent-3d42fca5: asking greenhouse-6b2a6c20 for greenhouse_status: How warm is it in the greenhouse?
12:00:43  my-agent-3d42fca5: greenhouse-6b2a6c20 answered: It is 31 °C in the greenhouse.
12:00:45  my-agent-3d42fca5: firing notify — the greenhouse is overheating
12:00:45  TRIGGER notify {"message": "The greenhouse is at 31 °C.", "level": "warning"} -> {"shown": true}
12:01:10  my-agent-3d42fca5 lost greenhouse-6b2a6c20
```

| line | meaning |
|---|---|
| `met …` | handshake done: keys and capability cards exchanged, encrypted from here |
| `… asks for …` / `answered …` | a request this agent received and what it replied |
| `asking … for …` / `… answered:` | a request this agent sent and what came back |
| `LLM  1.8 s  … -> …` | one model call: duration, prompt start, reply start (`loquor trace` has the whole thing) |
| `reasoning about …` | the `goal` skill deciding whether a peer can help |
| `… offers trigger tank-node:valve: …` | a node's trigger was discovered and added to this agent's hub |
| `collected from …: …` | the `collect` skill received a node's readings as data |
| `round N: …` | the `collect` skill's decision for that round: an action, or why none |
| `firing …` / `TRIGGER …` | a validated proposal; the trigger ran, with its input and result |
| `no trigger fired: …` | the proposal did not fit any allowed trigger's schema, with the reason |
| `lost …` | three beacon intervals of silence |

More detail when you need it: `--wire no-handshake` adds one line per
encrypted envelope (`SEND`/`RECV`/`DROP`, type, size); `--wire all`
includes the handshake; `-v` adds beacons, duplicates and every sensor
reading. `loquor config set wire all` makes it the default on a device.

## Debugging, by symptom

| symptom | look at |
|---|---|
| `loquor run` exits at once | the message names the part and the fix; `loquor check` shows all problems together |
| agents never `met` | both on the same transport (`loquor config show` on each); neither `silent`; same Wi-Fi without client isolation; for Bluetooth, `loquor doctor --transport bluetooth` on both |
| `met`, but no request follows | `loquor status` on the asker: the peer's capabilities; `loquor trace` the `useful=False` decision and its reason — usually the `description` does not say what the asker needs |
| the answer is wrong or empty | `loquor trace NAME --full`: the exact system prompt with the readings in it; most often the prompt does not say how to use them |
| `no trigger fired: invalid input` | the proposal did not match the schema; make the description say what each field is, or loosen the schema |
| a sensor shows `unavailable` | `loquor check` prints the driver's error; the agent keeps running and says so in prompts |
| slow | `LLM  … s` lines are the model: a smaller model (`loquor config set model qwen2.5:1.5b`), or Ollama on a faster machine (`ollama_url`); `beacon_interval 3` for faster discovery |
| `DROP … bad signature` | the peer's key changed (it deleted `~/.loquor/keys/`); it re-handshakes on its next beacon |
