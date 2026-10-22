# Usage

The `loquor` command, the device config, every profile field, and how to
see what a device is doing. The idea behind it all is in
[CONCEPTS.md](CONCEPTS.md); installing is in [INSTALL.md](INSTALL.md);
connecting hardware in [HARDWARE.md](HARDWARE.md); writing your own
parts in Python in [DEVELOPMENT.md](DEVELOPMENT.md).

## Commands

```
loquor doctor                        is this device ready? (Python, the LLM, the radio)
loquor init my_agent                 an agent to start from: profile, prompt, a sensor, a trigger, a brain
loquor init my_node --node           a node: the same without a brain — no LLM
loquor check my_agent/agent.json     build it, read each sensor once, check the model — nothing on the air
loquor read  my_agent/agent.json     read its sensors once (or only the ones named)
loquor fire  my_agent/agent.json notify '{"message": "hi"}'   fire one trigger by hand, schema-checked
loquor run   my_agent/agent.json     run it until Ctrl-C
loquor ask "Gardener" "question?"    ask an agent in range; no question = a device's readings
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
first line printed is the device's identity, model and transport; the
second is how to look at it from another terminal. It runs until
Ctrl-C, `kill`, or the terminal closes, and leaves the radio cleanly.

Startup refuses, with the fix, when a profile is invalid, a part cannot
be built, the brain names a local sensor or trigger the device lacks,
or the LLM is not available.

### check

Everything `run` does short of starting the radio, plus: every sensor
is read once and shown, every trigger's description and schema are
validated, the LLM is checked, and the transport's prerequisites. It
prints the role (node or agent), what the device offers, what its brain
reads and may fire, and a `pin entry` line for other devices'
`pinned_keys.json`. Exit code 1 if anything fails. Use it after every
edit; it takes a second.

### read, fire

Try hardware before going on the air. `read` reads every sensor (or
the ones named) once and prints the readings — `--json` for the
structured form; exit code 1 if any is unavailable. `fire` validates the
JSON input against the trigger's schema, fires it, and prints the result;
on an invalid input it prints the schema instead.

```bash
loquor read node.json water_level
loquor fire node.json valve '{"seconds": 10}'
```

Both build the profile without a radio and without the LLM.

### ask

`loquor ask TARGET [QUESTION]` joins the mesh for a moment as a silent,
throwaway peer, waits until `TARGET` (a name, any case, or an id prefix)
is heard, and asks:

- with a question, the agent's `ask` capability — its brain answers from
  what its sensors and the nodes' read right now;
- without one, `sensor_report` — any device's shared readings, as data.

It uses the device config's transport (`--transport` to override) and
waits up to `--timeout` seconds (30) — a beacon interval or two for
discovery, plus the LLM's time. If the target is not heard it says whom
it did hear.

### doctor

The device, not a profile: Python, the package, dependencies, the state
directory, the device config, the LLM (Ollama and the model, or the
OpenAI-compatible server), and the configured transport (or
`--transport T`). `--no-llm` skips the LLM. Exit code 1 on any failure.

### status

Each running device writes `~/.loquor/run/<name>.json` every 2 seconds;
`status` prints it:

```
greenhouse-controller-dc1068b6  running  pid 14636
  model      : qwen2.5:3b
  transport  : MulticastTransport (127.0.0.1)  visibility visible
  offers     : ask
  peers      : 3
    - soil-node-dcde0cd5  handshaken, trust 0, seen 0.0 s ago: sensor_report
    - air-node-f6f2c484  handshaken, trust 0, seen 0.0 s ago: sensor_report
    - tank-node-751c33bb  handshaken, trust 0, seen 0.0 s ago: sensor_report, fire_trigger
  sensors    : 5
    - soil-node:soil_moisture: 19.1 % (4.0 s ago)
    - air-node:air_temperature: 33.2 °C (4.0 s ago)
    …
  triggers   : vent, tank-node:valve
    - tank-node:valve {"seconds": 60} -> {"printed": 24}
  llm calls  : 3
    #3 1.66 s  Your goal: Keep the plants healthy: …  -> { "reason": "Soil moisture is below …
  goal       : Keep the plants healthy: …  [every 15 s, 3 round(s), 0 answer(s)]
    outcome  : Soil moisture is below the goal threshold of 30%, which requires immediate attention.
    fired    : {"trigger": "tank-node:valve", "input": {"seconds": 60}, "result": {"printed": 24}}
```

Sensors and triggers named `device:name` live on a node in range. `--json`
gives the raw document, for scripts and dashboards. A file whose process
is gone shows as `not running`.

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
loquor config set reasoner openai openai_url http://192.168.1.10:8080/v1
loquor config reset
```

`show` masks the API key. See the next section for the keys.

### service

`loquor service install PROFILE [--now] [run flags]` writes a systemd
user unit (Linux) or a launchd agent (macOS) that runs `python -m loquor
run PROFILE` from the current virtualenv, restarts it on failure, and
prints the commands that enable it; `--now` runs them. `uninstall`
removes it. Details in [INSTALL.md](INSTALL.md#5-run-at-boot).

## Device config

`~/.loquor/config.json` holds what is true of the device, not of one
profile, so a profile runs unchanged on a laptop, a Pi or a robot.

| key | default | meaning |
|---|---|---|
| `transport` | `localhost` | `localhost` (devices on this machine), `wifi` (the LAN), `bluetooth` |
| `interface` | auto | `wifi`: the local IP of the interface to use, when the device has several |
| `llm` | `true` | `false` on a node: no LLM here; `doctor` does not look for one and `run` refuses a profile with a brain |
| `reasoner` | `ollama` | the LLM backend: `ollama`, or `openai` for any OpenAI-compatible server (llama.cpp `llama-server`, LM Studio, vLLM, LocalAI) |
| `model` | `qwen2.5:3b` | the model the brain uses on this device |
| `ollama_url` | `http://localhost:11434` | where Ollama is; another machine on the LAN works |
| `openai_url` | `http://localhost:8080/v1` | where the OpenAI-compatible server is |
| `api_key` | none | for a server that wants one; `LOQUOR_API_KEY` in the environment works too |
| `beacon_interval` | `10` | seconds between beacons; lower finds peers faster, costs radio time |
| `wire` | `off` | envelope log level: `off`, `no-handshake`, `all` |
| `trace` | `true` | record every LLM call to `~/.loquor/run/<name>.trace.jsonl` |

Precedence, highest first: command-line flag → profile → device config
→ default. `LOQUOR_MODEL` in the environment overrides the model for
one run; `LOQUOR_HOME` moves the whole `~/.loquor` directory.

## Profile

A profile is one JSON file: who the device is and what it has.
`loquor init` writes a complete one. The schema is
[`src/loquor/profile.schema.json`](../src/loquor/profile.schema.json);
put its URL in `"$schema"` and VS Code validates and completes as you type.

```json
{
  "$schema": "https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/src/loquor/profile.schema.json",
  "name": "Greenhouse Controller",
  "description": "I run the greenhouse: I water the beds through the tank node's valve and open the roof vent.",
  "sensors":  [ { "type": "file", "name": "cpu_temp", "path": "/sys/class/thermal/thermal_zone0/temp", "scale": 0.001, "unit": "°C", "interval": 30 } ],
  "triggers": [ { "type": "gpio", "name": "vent", "pin": 22, "description": "Open the roof vent; close it when the air has cooled." } ],
  "brain": {
    "prompt_file": "prompt.txt",
    "goal": "Soil moisture above 30 %, air below 35 °C. Water through the tank node's valve only if the tank has more than 10 %.",
    "every": 15,
    "act": ["vent", "tank-node:valve"]
  }
}
```

### Fields

Only `name` and `description` are required, and something to do:
sensors or triggers to share, a `brain`, or `skills`.

| field | type | meaning |
|---|---|---|
| `name` | str | human-readable name; other devices call its parts `<slug of name>:<part>` (`Tank Node` → `tank-node:valve`); also the key file name and the log label |
| `description` | str | one sentence other devices' LLMs read to decide whether you can help them: say what you measure and do, in their terms |
| `sensors` | [part] | what it measures; each needs a unique `name` |
| `triggers` | [part] | what it can do; each needs a unique `name` and a `description` |
| `brain` | object | how it thinks; present = an agent, absent = a node. [Below](#brain) |
| `share` | bool \| object | what devices in range may use. [Below](#share) |
| `skills` | [part] | skills added by hand, for behaviour the brain does not cover ([DEVELOPMENT.md](DEVELOPMENT.md#add-a-skill)) |
| `system_prompt`, `system_prompt_file` | str, path | default prompt for hand-written skills; with a brain, use its `prompt` |
| `visibility` | `visible` \| `quiet` \| `silent` | beacon and answer policy; default `visible` |
| `beacon_interval` | number | seconds between beacons; pins the device setting |
| `key_file` | path | Ed25519 seed; default `~/.loquor/keys/<name>.key`, created on first run |
| `pinned_keys` | path | level-1 trust store; default `~/.loquor/pinned_keys.json` |
| `relay` | bool | store and forward envelopes for peers out of reach; default false |
| `transport` | part | pins a transport: `{"type": "wifi"}`; usually left to the device config |
| `reasoner` | part | pins the LLM: `{"type": "ollama" \| "openai", "model": "…"}`, `{"type": "template"}` (tests) or `{"type": "none"}`; usually left out: the device config picks it, and a profile without a brain gets `none` |

### brain

| key | default | meaning |
|---|---|---|
| `prompt` / `prompt_file` | a plain persona | how the LLM behaves. `{sensors}` is replaced by the current readings (else they are appended); `{random_number}` by a fresh 0–100 value when an answer must vary |
| `goal` | none | what to achieve. Without one the brain only answers questions |
| `every` | once | pursue the goal every N seconds. Left out: once, when enough peers are in range |
| `act` | everything it can reach | triggers it may fire: its own names and nodes' (`tank-node:valve`); `[]` = never act |
| `answer` | `true` | answer other agents' questions (capability `ask`) |
| `sensors` | its own + every node's | sensors it reads; name others' as `device:sensor` (an agent's shared sensors are read only when named — agents are asked, nodes are read) |
| `peers` | all | only use these peers (names) |
| `ask_agents` | `true` | ask other agents in range for help with the goal |
| `min_peers` | 1 (0 with `every` and own sensors) | think once this many peers have shared data or answered |
| `settle` | 0 | or think this many seconds after the last new peer, if fewer than `min_peers` turn up |
| `temperature` | 0.2 | 0 for steady decisions, 0.2–0.4 for varied prose |
| `capability`, `description` | `ask`, generic | the name and card description of the question capability |

A round of the goal: read the sensors (a node is asked for its readings
now, one request per node, reused for 5 s), ask the agents that can help
(the LLM decides once per agent whether it can and writes the question),
then think once over all of it. With triggers to `act` on it proposes
one — `{reason, trigger, input}`, validated against the trigger's schema
— or none, with the reason; without, it writes an assessment. A goal
without `every` concludes in prose first and then acts on its conclusion.

### share

| value | meaning |
|---|---|
| left out | a node shares its sensors and its triggers; an agent shares its sensors |
| `false` | share nothing (the device still answers questions if it has a brain) |
| `{"sensors": true \| [names], "triggers": true \| [names], "min_trust": 0}` | exactly that; `min_trust: 1` lets only peers whose key is pinned read or fire |

Shared sensors appear on other agents as `device:sensor`, shared triggers
as `device:trigger` (agents only mirror what peers share; nodes mirror
nothing). A profile that lists `skills` by hand shares nothing it does
not list, unless it sets `share`.

### Parts

A *part* is `{"type": T, …}` where `T` is a built-in name,
`"module:Class"` for an installed module, or `"file.py:Class"` /
`"file.py:function"` for a file next to the profile. Every other key is
a constructor argument; an unknown one fails at startup with the
constructor's message. A class's `name`, `description`, `unit`,
`interval` (sensors) and `input_schema` (triggers) can be overridden
from the profile without an `__init__`.

| part | `type` | arguments |
|---|---|---|
| sensor | `simulated` | `name`, `min`, `max`, `unit`, `step`, `interval`, `description`, `start` — a random walk, for demos |
| sensor | `command` | `name`, `command`, `unit`, `interval`, `description`, `timeout` (10) — stdout is the value: JSON, a number, or text |
| sensor | `file` | `name`, `path`, `unit`, `interval`, `description`, `pattern` (regex; its first group), `scale` (1), `offset` (0) |
| sensor | `http` | `name`, `url`, `field` (dotted path into the JSON), `unit`, `interval`, `description`, `headers`, `timeout` (5), `scale`, `offset` |
| sensor | `gpio` | `name`, `pin` (BCM), `pull_up` (false), `interval`, `description` — `true` when active; needs `gpiozero` |
| sensor | `serial` | `name`, `port`, `baudrate` (9600), `request` (written first), `pattern`, `scale`, `offset`, `unit`, `interval`, `description`, `timeout` (2); needs `pyserial` |
| trigger | `console` | `name` (`print`), `description`, `input_schema` (`{text}`), `template` |
| trigger | `command` | `name`, `command`, `description`, `input_schema`, `timeout` (30) — the input arrives as JSON on stdin |
| trigger | `file` | `name`, `path`, `description`, `input_schema` (`{value}`), `template` (`{value}`; `{on:d}` writes 1/0), `append` (false) |
| trigger | `http` | `name`, `url` (`{field}` placeholders filled from the input), `method` (POST), `description`, `input_schema`, `headers`, `timeout` (10) — POST/PUT/PATCH send the input as JSON |
| trigger | `gpio` | `name`, `pin` (BCM), `description`, `active_high` (true), `max_seconds` (600) — input `{on, seconds?}`; with `seconds` it switches off again by itself |
| trigger | `serial` | `name`, `port`, `baudrate`, `description`, `input_schema` (`{text}`), `template` (`{text}`), `reply` (false), `timeout` (2) |
| reasoner | `ollama` | `model`, `base_url`, `temperature` |
| reasoner | `openai` | `model`, `base_url`, `api_key`, `temperature`, `timeout` — any OpenAI-compatible server |
| reasoner | `template` | — (no model; deterministic text, for tests and CI) |
| reasoner | `none` | — (no LLM: a node; chosen automatically for a profile without a brain) |
| transport | `localhost`, `wifi`, `bluetooth` | `interface` (wifi), `port` (47600) |

Skills, for profiles that add them by hand:

| skill | arguments | what it does |
|---|---|---|
| `brain` | as the `brain` block | what `"brain"` becomes |
| `sensor_report` | `sensors` ([names]; default its own), `min_trust` (0) | the readings as data; lists the sensors in the card. What `share.sensors` becomes |
| `offer_triggers` | `triggers` ([names]; default its own), `min_trust` (0) | lets peers fire its triggers; lists them in the card. What `share.triggers` becomes |
| `llm_answer` | `capability`, `system_prompt`, `temperature`, `sensors`, `max_chars` | answers `{question}` under a capability name of your choosing |
| `goal` | `goal`, `system_prompt`, `temperature`, `triggers`, `min_answers`, `settle` | the older one-shot goal: asks agents, concludes, may fire one trigger |
| `collect` | `interval`, `capability`, `peers`, `goal`, `system_prompt`, `temperature`, `triggers`, `remote_triggers`, `sensors`, `min_nodes` | the older polling loop: pulls every node's readings each round; offers the table as `collected_readings` |

## The LLM's behaviour

Plain text in four places shapes what the LLM does:

- **the prompt** (`brain.prompt_file`): persona and rules. Say what the
  numbers mean and what to do about them.
- **the goal** (`brain.goal`): what to achieve, with the limits that
  matter ("above 30 %", "only if the tank has more than 10 %").
- **`description`s**: of the device (what other agents' LLMs read to
  decide whether to ask it), of each sensor (what the number means), of
  each trigger (what it does and when to use it).
- **trigger schemas**: what the LLM may do and the exact shape. Every
  proposal is validated against the schema; one that does not fit is
  logged and dropped. Make the schema the safety limit.

`temperature` (0 for decisions) and the model (`loquor config set model …`)
are the only other knobs. A 3B model is right most of the time and
sometimes misjudges a comparison; a 7B one, or a larger model on another
machine (`ollama_url`, `openai_url`), is steadier.

## Reading the log

The default log is the device's story, one line per event:

```
02:07:16  greenhouse-controller-dc1068b6 met tank-node-751c33bb: I am a sensor node on the irrigation water tank… ['sensor_report', 'fire_trigger']
02:07:16  greenhouse-controller-dc1068b6: tank-node-751c33bb shares tank-node:water_level, tank-node:valve
02:12:52  greenhouse-controller-dc1068b6: asking soil-node-dcde0cd5 for sensor_report: (no input)
02:12:52  greenhouse-controller-dc1068b6: soil-node-dcde0cd5 answered: readings soil_moisture=23.9 % (0 s ago); soil_temperature=20.9 °C (0 s ago)
02:12:54  LLM  1.8 s  "Your goal: Keep the plants healthy: soil moisture above 30 %, air bel…" -> "{ \"reason\": \"Soil moisture is below the goal threshold of 30%, which …"
02:12:54  greenhouse-controller-dc1068b6: round 1: firing tank-node:valve — Soil moisture is below the goal threshold of 30%…
02:12:54  TRIGGER tank-node:valve {"seconds": 60} -> {"printed": 24}
02:14:10  greenhouse-controller-dc1068b6 lost tank-node-751c33bb (silent for 30s)
```

| line | meaning |
|---|---|
| `met …` | handshake done: keys and capability cards exchanged, encrypted from here |
| `… shares …` | a peer's sensors and triggers, now usable here as `device:name` |
| `… asks for …` / `answered …` | a request this device received and what it replied |
| `asking … for …` / `… answered:` | a request this device sent and what came back (`sensor_report` = reading a node) |
| `LLM  1.8 s  … -> …` | one model call: duration, prompt start, reply start (`loquor trace` has the whole thing) |
| `reasoning about …` | the brain deciding whether an agent in range can help its goal |
| `concluded: …` | a one-shot goal's conclusion, before it acts |
| `round N: firing …` / `round N: no action — …` | the brain's decision for that round, with its reason |
| `TRIGGER …` | a validated proposal ran, with its input and result |
| `trigger failed: …` | the trigger (or the node it lives on) refused or failed |
| `lost …` | three beacon intervals of silence; its shared parts are dropped |

More detail when you need it: `--wire no-handshake` adds one line per
encrypted envelope (`SEND`/`RECV`/`DROP`, type, size); `--wire all`
includes the handshake; `-v` adds beacons, duplicates and every sensor
reading. `loquor config set wire all` makes it the default on a device.

## Debugging, by symptom

| symptom | look at |
|---|---|
| `loquor run` exits at once | the message names the part and the fix; `loquor check` shows all problems together |
| devices never `met` | both on the same transport (`loquor config show` on each); neither `silent`; same Wi-Fi without client isolation; for Bluetooth, `loquor doctor --transport bluetooth` on both |
| `met`, but the node's sensors never appear | the node shares nothing (`"share": false`, or hand-written `skills` without `sensor_report`), or this device has no brain (nodes mirror nothing) |
| a sensor reads `not in range` | the brain names `device:sensor` for a device that is not heard; check the name with `loquor status` (it is the device's name, slugged) |
| `met` an agent, but it is never asked | `loquor trace` the `useful=False` decision and its reason — usually its `description` does not say what the asker needs |
| the answer is wrong or empty | `loquor trace NAME --full`: the exact prompt with the readings in it; most often the prompt or goal does not say how to use them |
| `no action — invalid input …` | the proposal did not match the schema; make the description say what each field is, or loosen the schema |
| a sensor shows `unavailable` | `loquor read` prints the driver's error; the device keeps running and says so in prompts |
| slow | `LLM  … s` lines are the model: a smaller model (`loquor config set model qwen2.5:1.5b`), or the LLM on a faster machine (`ollama_url`, `openai_url`); `beacon_interval 3` for faster discovery |
| `DROP … bad signature` | the peer's key changed (it deleted `~/.loquor/keys/`); it re-handshakes on its next beacon |
