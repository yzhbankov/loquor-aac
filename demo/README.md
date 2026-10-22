# Demos

Simplest first. Each runs on one computer in a few terminals (the
default transport, `localhost`), and on several devices with
`loquor config set transport wifi` or `bluetooth` on each. Real software,
real protocol, real local LLMs; only the hardware is emulated — sensors
invent plausible readings, triggers print.

| demo | devices | what it shows | Python to write |
|---|---|---|---|
| [**garden**](garden/README.md) | 1 node, 1 agent | the smallest pair: the agent reads the node's sensor only when you ask it a question (`loquor ask`) | none |
| [**greenhouse**](greenhouse/README.md) | 3 nodes, 1 agent | a goal every 15 s: the agent reads three nodes and fires a trigger *on* a node (the tank's valve) or its own | none |
| [**weather_and_clothes**](weather_and_clothes/README.md) | 1 node, 1 agent | a one-shot goal: read once, conclude, show a card through a trigger with a schema | 1 sensor, 1 trigger |
| [**triage**](triage/README.md) | 4 agents | LLM to LLM: three chairs judge their own patients, a nurse's agent asks each and orders the board | 1 sensor, 1 trigger |

What each profile type looks like:

```json
{ "name": "Soil Node", "description": "…", "sensors": [ … ] }                                  // a node
{ "name": "Gardener",  "description": "…", "brain": { "prompt_file": "gardener_prompt.txt" } } // an agent that answers
{ "name": "Controller", "description": "…", "triggers": [ … ],
  "brain": { "prompt_file": "…", "goal": "…", "every": 15, "act": ["vent", "tank-node:valve"] } } // an agent with a goal
```

Before the first run: `./install.sh` in the repo root, then
`source .venv/bin/activate` ([docs/INSTALL.md](../docs/INSTALL.md)). To
see what any of them is doing while it runs: `loquor status`,
`loquor trace "<name>" --full`.
