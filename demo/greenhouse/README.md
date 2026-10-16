# Demo: Greenhouse — sensor nodes and one controller

Not every device needs a brain. Three **sensor nodes** — soil, air, water
tank — only measure and report; they run no LLM, need no Ollama, and fit
a Raspberry Pi Zero. One **controller** collects their readings every 15 s,
thinks once per round with its LLM, and acts: it runs the pump or opens
the roof vent.

**Real:** the nodes, the protocol (keys, handshake, encryption), the
readings travelling as data straight from each node to the controller,
the controller's LLM decisions, the schema check before anything fires.
**Emulated:** the instruments (`simulated` sensors that drift) and the
actuators (they print instead of switching a relay).

| file | what it is |
|---|---|
| `node_soil.json`, `node_air.json`, `node_tank.json` | **nodes**: built-in `simulated` sensors + `sensor_report`. No Python, no prompt, no LLM. |
| `controller.json` | the **agent**: `collect` every 15 s from every node, with a goal and two triggers |
| `controller_prompt.txt` | how the controller's LLM behaves |
| `actuators.py` | **triggers**: the pump and the vent (stand-ins for two relays) |

```mermaid
sequenceDiagram
    participant S as Soil Node
    participant A as Air Node
    participant T as Tank Node
    participant C as Controller
    participant P as pump / vent (triggers)
    S-->>C: beacon · HELLO/HELLO_ACK (encrypted from here)
    A-->>C: beacon · HELLO/HELLO_ACK
    T-->>C: beacon · HELLO/HELLO_ACK
    loop every 15 s
        C->>S: request sensor_report {}
        S->>C: response {readings: {soil_moisture: 24 %, soil_temperature: 21 °C}}
        C->>A: request sensor_report {}
        A->>C: response {readings: {air_temperature: 33 °C, humidity: 70 %}}
        C->>T: request sensor_report {}
        T->>C: response {readings: {water_level: 62 %}}
        Note over C: LLM reads the table of readings, proposes pump / vent / nothing
        C->>P: fire(input) — after schema validation
    end
```

Nothing a node sends passes through a model: `sensor_report` answers
with the numbers, in an encrypted envelope addressed to the device that
asked. The controller's LLM is the only one in the picture.

## 1. Setup

On the controller's device: `./install.sh` (Ollama and a model). On a
node's device: `./install.sh --node` — no Ollama, no model, done in a
minute. On one computer for the demo, the normal install covers all four.

## 2. Run on one computer

Four terminals in the repo root with the venv active. Nodes first (any
order), then the controller:

```bash
loquor run demo/greenhouse/node_soil.json
loquor run demo/greenhouse/node_air.json
loquor run demo/greenhouse/node_tank.json
loquor run demo/greenhouse/controller.json
```

### What you will see

A node's terminal is quiet: it starts, meets the controller, and answers
every 15 s:

```
soil-node-9f1e2a3b | id 9f1e2a3b… | model none (none) | transport localhost (127.0.0.1) | visible | Ctrl-C to stop
12:00:02  soil-node-9f1e2a3b met greenhouse-controller-3d42fca5: I run the greenhouse… ['collected_readings']
12:00:03  greenhouse-controller-3d42fca5 asks for sensor_report: {}
12:00:03  soil-node-9f1e2a3b: answered greenhouse-controller-3d42fca5: {"readings": {"soil_moisture": {"value": 24.0, "unit": "%", …
```

The controller's terminal, one round:

```
12:00:03  greenhouse-controller-3d42fca5: collected from soil-node-9f1e2a3b: soil_moisture=24.0 % (1 s ago); soil_temperature=21.3 °C (1 s ago)
12:00:03  greenhouse-controller-3d42fca5: collected from air-node-7c0d11ee: air_temperature=33.5 °C (0 s ago); humidity=70.2 % (0 s ago)
12:00:03  greenhouse-controller-3d42fca5: collected from tank-node-51ab09c2: water_level=62.0 % (2 s ago)
12:00:07  LLM  3.8 s  "Your goal: Keep the plants healthy: soil moisture above 30 %…" -> "{ \"trigger\": \"pump\", \"input\": { \"seconds\": 30 }, \"reason\": \"soil_moisture…"
12:00:07  greenhouse-controller-3d42fca5: round 1: firing pump — soil_moisture is 24 %, below the 30 % limit, and the tank has 62 %

💧  PUMP ON for 30 s

12:00:07  TRIGGER pump {"seconds": 30} -> {"watered_for_s": 30}
```

and, when nothing is out of range, `round 2: no action — all readings
within limits`. The simulated sensors drift, so over a few minutes you
will see the vent open as the air warms past 35 °C and the pump stay off
once the soil reading climbs.

From a fifth terminal:

```bash
loquor status "Greenhouse Controller"     # the table of every node's latest readings, rounds, last action
loquor status "Soil Node"                 # a node: its readings and who asked
loquor trace "Greenhouse Controller"      # the exact table the LLM saw and what it proposed
```

Things to try:
- Change `"start"` of `soil_moisture` in `node_soil.json` to 45 and restart that node: the pump stays off.
- Set `"start"` of `water_level` to 5: the soil is dry but the controller must not pump (the goal says so). `loquor trace` shows its reasoning.
- Drop `"goal"` and `"triggers"` from `controller.json`: the controller only collects, needs no LLM, and still serves `collected_readings` to other agents.
- Replace a `simulated` sensor with `{"type": "command", "command": "…"}`: a real instrument, still no Python.

## 3. Run on several devices

Each node on its own board with `./install.sh --node` and `loquor config
set transport wifi` (or `bluetooth`); the controller on any device with a
model. `loquor service install node_soil.json --now` keeps a node running
after a reboot. Nodes and controller must share one transport.

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| controller logs nothing after `met` | the node must offer `sensor_report`: `loquor status <node>` shows `offers`; `collect` asks only peers that have it |
| `no readings from … : no response` | the node is gone or slow; it is asked again next round. `loquor status` on the node shows whether it is alive |
| `round N: no action` every time although a reading is out of range | `loquor trace "Greenhouse Controller" --full`: the table the LLM saw; tighten `controller_prompt.txt` or the goal, or a larger model |
| `no trigger fired: invalid input` | the proposal did not fit the schema (e.g. `seconds` out of 5–120); the reason is logged, nothing fires |
| a node device fails `loquor doctor` on Ollama | `loquor config set llm false` (what `install.sh --node` does); a node needs none |
| the controller refuses to start: `configured as a sensor node` | that device has `llm false`; run the controller on a device with Ollama, or `loquor config set llm true` |
