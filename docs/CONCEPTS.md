# Concepts

Everything in loquor is one idea: **a device is sensors + triggers + an
optional brain**, and devices in radio range find each other and use each
other's parts. This page is that idea in full; it takes five minutes.

```
        a node                                   an agent
 ┌───────────────────────┐               ┌──────────────────────────────┐
 │ sensors   soil, level │──── shares ──▶│ sensors   own + soil-node:…  │
 │ triggers  valve       │──── shares ──▶│ triggers  own + tank-node:…  │
 │ brain     —           │               │ brain     prompt · goal · act│
 └───────────────────────┘               └──────────────────────────────┘
      no LLM, no Ollama                       an LLM on the device
      a Raspberry Pi Zero                     a laptop, a Pi 5, a robot
```

## The three parts

| part | what it is | you write |
|---|---|---|
| **sensor** | something the device measures: a number, a string, a small dict | one line of JSON (a built-in driver), a function, or a small class |
| **trigger** | something the device can do, with a JSON schema for its input | one line of JSON, a function, or a small class |
| **brain** | an LLM with a prompt, and optionally a goal | a `"brain"` block and a prompt file |

A device's **profile** (`agent.json`) lists them:

```json
{
  "name": "Tank Node",
  "description": "I measure the water tank and can open the irrigation valve.",
  "sensors":  [ { "type": "file", "name": "water_level", "path": "/sys/bus/iio/devices/iio:device0/in_voltage0_raw", "scale": 0.0244, "unit": "%", "interval": 5 } ],
  "triggers": [ { "type": "gpio", "name": "valve", "pin": 17, "description": "Open the irrigation valve." } ]
}
```

## Two roles

**No `brain` → a node.** It has no LLM and needs none installed. It
shares its sensors (any agent in range can read them) and its triggers
(any agent can fire them, after validating the input). It still runs the
whole protocol: identity, beacons, handshake, encryption.

**A `brain` → an agent.** It has an LLM. It shares its sensors too (not
its triggers — those are for its own brain), and its brain:

- **answers** questions from other agents, from what its sensors read at
  that moment;
- with a **goal**, **pursues** it: reads what it needs, asks agents that
  can help, thinks, and fires a trigger — or says why not.

```json
"brain": {
  "prompt_file": "prompt.txt",
  "goal": "Keep soil moisture above 30 %; water only if the tank has water.",
  "every": 30,
  "act": ["tank-node:valve"]
}
```

`every` makes it a loop (every 30 s); without it the goal runs once, when
enough peers are in range. `act` lists what it may fire; without it,
anything it can reach.

## How a node's parts become an agent's

When two devices meet, they exchange **capability cards**. A node's card
lists its sensors (name, unit, description) and its triggers (name,
description, input schema). The agent **mirrors** them into its own:

| on the node | on the agent |
|---|---|
| sensor `soil_moisture` on "Soil Node" | sensor `soil-node:soil_moisture` |
| trigger `valve` on "Tank Node" | trigger `tank-node:valve` |

From then on the brain — and anything else on the agent — uses them like
its own:

- **Reading is on demand.** Nothing is polled. When the brain needs
  `soil-node:soil_moisture` (to answer a question, or for a round of its
  goal), the agent asks the node for its readings; one request serves
  all of that node's sensors for a few seconds.
- **Firing is validated twice.** The LLM proposes `tank-node:valve
  {"seconds": 30}`; the agent checks it against the valve's schema, sends
  it, and the node checks it again before its driver runs.
- **Peers come and go.** A node that goes silent for three beacon
  intervals is dropped, and its sensors and triggers with it. A sensor
  named in a prompt that is not in range reads as "not in range".

Nodes are *read*. Agents are *asked*: a brain with a goal decides once per
agent in range whether it can help (an LLM call), writes it a question,
and uses the answer. That is how two LLMs on two devices work together —
the triage demo is that case.

## The LLM never acts

Every action goes the same way, on every device:

```
readings, answers ──▶ LLM ──▶ proposal {reason, trigger, input}
                                   │
                    schema check ──┴──▶ trigger.fire(input)   (or nothing, and the reason is logged)
```

The model writes JSON; deterministic code decides whether it is allowed.
A proposal for a trigger that is not in `act`, or with an input that does
not match the schema, is dropped. So the worst a confused small model can
do is choose a wrong *allowed* action with a *valid* input — make the
schema the safety limit (`"maximum": 120` seconds on a valve).

## Where things are configured

| what | where | example |
|---|---|---|
| who the device is and what it has | the profile, `agent.json` | sensors, triggers, brain |
| how the LLM behaves | a prompt file next to it | persona, rules, `{sensors}` |
| what other devices read about it | `description` in the profile, and each sensor's and trigger's `description` | "I measure the tank" |
| what this hardware runs | the device config, `~/.loquor/config.json` | transport, model, LLM server |

The profile is the same on every device it runs on. The device config is
written once per device (`install.sh`, `loquor config set …`), so a
laptop and a Pi run one profile with different models or radios.

## Commands for each step

| step | command |
|---|---|
| install | `./install.sh` (agent) · `./install.sh --node` (node) |
| start from a working folder | `loquor init my_agent` · `loquor init my_node --node` |
| try a sensor or a trigger by hand | `loquor read agent.json` · `loquor fire agent.json valve '{"seconds": 10}'` |
| check everything before going on the air | `loquor check agent.json` |
| connect | `loquor config set transport wifi` (or `bluetooth`) on every device |
| run | `loquor run agent.json` |
| talk to it | `loquor ask "Gardener" "Do the beds need water?"` · `loquor ask "Soil Node"` |
| see what it does | `loquor status` · `loquor trace "Gardener"` |

Next: [HARDWARE.md](HARDWARE.md) to connect real sensors and actuators,
[USAGE.md](USAGE.md) for every field, the [demos](../demo/README.md) to
see it run.
