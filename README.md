<p align="center">
  <img src="assets/loquor-logo-horizontal.svg" width="420" alt="loquor — two devices, a signal between them">
</p>

# loquor-aac

**loquor** — Latin for *"I speak"*. **aac** — agent-to-agent communication.

Devices that find each other and work together **without internet, cloud
or server**, over the local radio they have: Wi-Fi or Bluetooth.

A device is **sensors** (what it measures) + **triggers** (what it can do)
+ an optional **brain** (a local LLM with a prompt and a goal).

- A device **without a brain is a node**: no LLM, a Raspberry Pi Zero is
  enough. It shares its sensors and triggers with every agent in range.
- A device **with a brain is an agent**. It reads its own sensors and the
  nodes' — only when it needs them —, asks other agents, and fires
  triggers, its own or a node's. The LLM only *proposes* an action; code
  checks it against the trigger's schema before anything moves.

Everything is encrypted and signed, peer to peer. Where it matters: a
farm, a factory floor, a hospital waiting area, a convoy, a disaster
zone — anywhere the network is unreliable or absent.

## Get running

On a laptop, a Raspberry Pi or any Linux or macOS device with Python 3.10:

```bash
git clone https://github.com/yzhbankov/loquor-aac.git && cd loquor-aac
./install.sh                     # agent: Python, the package, Ollama, a model sized for this device
./install.sh --node              # or a node: no Ollama, no model
source .venv/bin/activate
```

Then, on one computer, the smallest pair — a node and an agent:

```bash
loquor run demo/garden/soil_node.json            # terminal 1: a node, no LLM
loquor run demo/garden/gardener.json             # terminal 2: an agent with a brain
loquor ask Gardener "Do the beds need water?"    # terminal 3
```

```
Gardener: The beds need water as the soil moisture is currently at 18.6%, which is below the threshold of 30%.
```

The gardener read the node's sensor at the moment you asked, not before.
On two devices: `loquor config set transport wifi` (same Wi-Fi or a phone
hotspot, no internet needed) or `bluetooth` (no network at all) on both.
Every install step, per OS: [docs/INSTALL.md](docs/INSTALL.md).

## Make your own

```bash
loquor init my_node --node       # a node: agent.json, sensors.py, triggers.py
loquor init my_agent             # an agent: the same plus prompt.txt and a brain
```

A **node** is a name, a description, sensors and triggers:

```json
{
  "name": "Tank Node",
  "description": "I measure the irrigation tank and can open its valve.",
  "sensors":  [ { "type": "file", "name": "water_level", "path": "/sys/bus/iio/devices/iio:device0/in_voltage0_raw",
                  "scale": 0.0244, "unit": "%", "interval": 5 } ],
  "triggers": [ { "type": "gpio", "name": "valve", "pin": 17, "description": "Open the irrigation valve." } ]
}
```

An **agent** adds a brain — a prompt, and a goal if it should act on its own:

```json
{
  "name": "Greenhouse Controller",
  "description": "I run the greenhouse.",
  "brain": {
    "prompt_file": "prompt.txt",
    "goal": "Keep soil moisture above 30 %; water through the tank's valve only if it has water.",
    "every": 30,
    "act": ["tank-node:valve"]
  }
}
```

It sees the node's sensor as `tank-node:water_level` and its valve as
`tank-node:valve`, reads one when its brain needs it and fires the other
when the brain decides to.

**Sensors and triggers** need no Python for most devices: built-in
`file` (sysfs, 1-wire), `command`, `http`, `gpio` and `serial` drivers,
one line of JSON each — recipes in [docs/HARDWARE.md](docs/HARDWARE.md).
Otherwise a function (its return value is the reading) or a small class.

**The brain's model** is a device setting, so one profile runs on a
laptop and a Pi: `loquor config set model qwen2.5:1.5b`. Ollama by
default; llama.cpp, LM Studio or vLLM with
`loquor config set reasoner openai`.

The whole model on one page: [docs/CONCEPTS.md](docs/CONCEPTS.md).

## Try things by hand

| question | command |
|---|---|
| Can this device run it? | `loquor doctor` — Python, the LLM, the radio; each failure with its fix |
| Does my profile build? Do my sensors read? | `loquor check agent.json` |
| What does this sensor read right now? | `loquor read agent.json [sensor]` |
| Does this actuator work? | `loquor fire agent.json valve '{"seconds": 10}'` — schema-checked first |
| What does that device say? | `loquor ask "Gardener" "question"` · `loquor ask "Tank Node"` (its readings) |
| What does the running agent see? | `loquor status` — peers, readings, triggers fired, LLM calls, goal |
| Why did it decide *that*? | `loquor trace "Greenhouse Controller" --full` — every prompt and reply |
| What went over the radio? | `loquor run … --wire all` (envelopes), `-v` (beacons, readings) |

## Demos

Real devices' software, real protocol, real local LLMs; only the
hardware is emulated (sensors invent readings, triggers print). Simplest
first — [demo/README.md](demo/README.md):

| demo | what it shows |
|---|---|
| [**Garden**](demo/garden/README.md) | one node, one agent: the agent reads the node only when asked |
| [**Greenhouse**](demo/greenhouse/README.md) | three nodes, one with a valve; an agent that reads them every 15 s and waters through the node's valve or opens its own vent |
| [**Weather & Clothes**](demo/weather_and_clothes/README.md) | a weather-station node and an agent that turns its reading into an outfit card, once |
| [**Emergency Triage**](demo/triage/README.md) | LLM to LLM: three chairs assess their patients, a nurse's agent asks each and orders the board |

Verified between a Mac and an Ubuntu laptop over Wi-Fi and over
Bluetooth, no network at all.

## How it works

```
   device A                                            device B
 ┌──────────────────────────────┐                     ┌──────────────────────────────┐
 │ sensors ─▶ brain ─▶ triggers │◀── Wi-Fi / BLE ───▶ │ sensors          triggers    │
 │            agent             │  beacon · handshake │        node (no brain)       │
 └──────────────────────────────┘  encrypted messages └──────────────────────────────┘
```

1. **Identity.** First run: the device makes a keypair and keeps it. Its id is a hash of its public key.
2. **Discovery.** Every 10 s it sends a 16-byte **beacon**.
3. **Handshake.** Devices that hear each other exchange keys and **capability cards** — what each offers, which sensors it shares, which triggers — and derive a shared key.
4. **Messages.** From then on every message is encrypted, signed, de-duplicated and acknowledged.
5. **Using each other.** An agent mirrors a node's sensors and triggers as `node:name`; it reads them with a `sensor_report` request and fires them with `fire_trigger`. Agents ask each other questions (`ask`).
6. **Safety.** The LLM never fires anything. It proposes `{reason, trigger, input}`; the input is validated against the trigger's schema on the agent and again on the node.

Byte-level detail: [docs/PROTOCOL.md](docs/PROTOCOL.md). Layers and
interfaces: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Develop

```bash
pip install -e ".[dev]"
python -m pytest            # no model, network or radio needed, ~8 s
```

## Documentation

| read this for | document |
|---|---|
| the idea: sensors, triggers, brain; nodes and agents | [docs/CONCEPTS.md](docs/CONCEPTS.md) |
| where it is worth using, area by area, and where it is not | [docs/APPLICATIONS.md](docs/APPLICATIONS.md) |
| installing on a laptop, a Pi, a device; Bluetooth; running at boot | [docs/INSTALL.md](docs/INSTALL.md) |
| connecting real sensors and actuators | [docs/HARDWARE.md](docs/HARDWARE.md) |
| every command, profile field and device setting; reading the log; debugging | [docs/USAGE.md](docs/USAGE.md) |
| writing sensors, triggers, skills, transports in Python; tests | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| the layers, interfaces and design decisions | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| what goes on the wire | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| why the project exists | [docs/VISION.md](docs/VISION.md) · [history](docs/history/POC_PLAN.md) |
| the logo and app icon | [assets/](assets/README.md) |

## Status

| done | next |
|---|---|
| AMP v1: identity, signed CBOR envelopes, beacons, handshake, encryption, acknowledgements, visibility | Session transports (bulk data over TCP / L2CAP) |
| Nodes without an LLM sharing sensors and triggers; agents reading them on demand and firing them remotely | Nodes pushing a reading when it crosses a threshold |
| The brain: answers, goals once or every N s, asks other agents, acts through schema-checked triggers | Pairing ceremony (QR / fingerprint) from the CLI |
| Built-in `file`, `command`, `http`, `gpio`, `serial` drivers; Ollama and OpenAI-compatible LLM servers | A node firmware for microcontrollers (ESP32) |
| Wi-Fi and Bluetooth, verified between a Mac and an Ubuntu laptop | UWB proximity; relay gossip |
| One-script install; `loquor` doctor · init · check · read · fire · ask · run · status · trace · service | A PyPI release |

## License

[Apache 2.0](LICENSE)
