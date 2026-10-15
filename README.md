# loquor-aac

**loquor** — Latin for *"I speak"*, *"I talk"*, *"I say"*. **aac** — agent-to-agent communication.

AI agents on different **devices** — robots, machines, sensor nodes,
vehicles, handhelds — that find each other, authenticate and collaborate
**offline**, over whatever local radio they have: Wi-Fi today, Bluetooth
next. No internet, no cloud, no central server. Each agent runs its own
local LLM and keeps its own knowledge; together they do what neither could
alone.

```
   device A  (robot, sensor node, …)          device B  (machine, handheld, …)
 ┌──────────────────┐    any local link    ┌───────────────────┐
 │   agent + skills │◀──  Wi-Fi · BLE  ──▶│   agent + skills  │
 │   local LLM      │  beacon · HELLO ·    │   local LLM       │
 │   own knowledge  │  encrypted MSG/ACK   │   own knowledge   │
 └──────────────────┘                      └───────────────────┘
```

The demo in this repo runs two such agents on two laptops (or one) because
that is the fastest way to see it work; nothing in the code knows or cares
that it is a laptop. The same agent, profile and protocol run on anything
with Python and a local model.

## How it works, in one minute

1. **Identity.** On first run an agent generates an Ed25519 keypair and
   keeps it in `~/.loquor/keys/`. Its id is a hash of its public key.
2. **Discovery.** Every 10 s it broadcasts a 16-byte **beacon**: id prefix +
   a Bloom filter of what it can do. Small enough for a BLE advertisement.
3. **Handshake.** Hearing an unknown beacon, an agent sends **HELLO** (public
   key + capability card). The other verifies and answers **HELLO_ACK**.
   Both derive a shared key (X25519).
4. **Messages.** From then on every message between the two is encrypted
   (ChaCha20-Poly1305), signed, deduplicated, and acknowledged.
5. **Skills.** A request names a *capability*; the agent dispatches it to the
   skill that owns it, which may call the local LLM. A skill with a *goal*
   looks at the peers it hears, decides who can help, asks, and acts.
6. **Sensors and triggers.** A sensor is a driver that produces readings
   (a thermometer on a UART, a GPIO, a subprocess); a trigger is a driver
   that performs an action (a relay, a display, a script). The LLM sees the
   readings and *proposes* a trigger with an input; the agent validates the
   proposal against the trigger's schema and fires it. Both are optional,
   both are one class or one function, both are selected in the profile.

The wire protocol is **AMP v1** (Agent Mesh Protocol). Sequence diagrams and
every byte on the wire: [docs/PROTOCOL.md](docs/PROTOCOL.md). How the code is
layered: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it (5 minutes)

Needs **Python 3.10+** and **[Ollama](https://ollama.com)** running locally.

```bash
git clone https://github.com/yzhbankov/loquor-aac.git && cd loquor-aac
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b                                            # once, ~2 GB

python run_agent.py demo/weather_and_clothes/weather_agent.json   # terminal 1
python run_agent.py demo/weather_and_clothes/clothing_agent.json  # terminal 2
```

Within ~15 s terminal 2 prints `=== Goal complete ===` with the forecast it
got and the clothes it recommends. Two devices on one Wi-Fi: add
`--transport wifi` on both. Full walkthrough, what the log lines mean, and
troubleshooting: [demo/weather_and_clothes/README.md](demo/weather_and_clothes/README.md).

## Write your own agent

An agent is a JSON profile. Built-in parts are picked by name; your own by
`"module:Class"`:

```json
{
  "name": "Soil Station",
  "description": "I report soil moisture and decide whether to water.",
  "reasoner": { "type": "ollama", "model": "qwen2.5:3b" },
  "sensors":  [ { "type": "simulated", "name": "soil", "unit": "%", "min": 5, "max": 60, "interval": 30 } ],
  "triggers": [ { "type": "drivers/relay.py:PumpRelay", "port": "/dev/ttyUSB1" } ],
  "skills": [
    { "type": "llm_answer", "capability": "soil_report", "sensors": ["soil"],
      "system_prompt": "You report soil moisture from your sensor:\n{sensors}" },
    { "type": "sensor_report" },
    { "type": "my_farm.irrigation:Irrigation" }
  ]
}
```

```python
# my_farm/irrigation.py
from loquor import Skill

class Irrigation(Skill):
    sensors = ("soil",)                            # checked at startup
    triggers = ("pump",)

    async def run(self, ctx):                      # background loop
        while True:
            soil = await ctx.sensors.read("soil")
            forecast = "unknown"
            for peer in ctx.peers("weather_forecast"):
                forecast = (await ctx.ask(peer, "weather_forecast", {"question": "Rain tomorrow?"}))["answer"]
            proposal = await ctx.triggers.propose(          # the LLM proposes; the schema decides
                "You run the irrigation.", f"{soil.text()}\nForecast: {forecast}", allowed=["pump"])
            if proposal:
                await ctx.triggers.fire(proposal.trigger, proposal.input)
            await ctx.wait_for_peers()
```

A sensor is a class with `read()`; a trigger is a class with `fire()` and a
JSON schema for its input. The agent only ever talks to them through that
contract, so what is behind it — a UART, a GPIO, a subprocess — is the
driver's business.

Working examples: the demo's [`weather_sensor.py`](demo/weather_and_clothes/weather_sensor.py)
and [`fancy_print.py`](demo/weather_and_clothes/fancy_print.py); a UART
thermometer and a serial relay in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md),
with the rules for writing your own and every profile field.

## Develop

```bash
pip install -e ".[dev]"
python -m pytest            # 65 tests, no model or network needed, ~3 s
```

Layout, how to add a skill / transport / extension, logging, conventions:
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

```
loquor-aac/
├── src/loquor/
│   ├── protocol/      AMP v1 core: identity, envelope, discovery, card, crypto, node
│   ├── transports/    localhost + wifi (UDP multicast), memory (tests); BLE planned
│   ├── extensions/    QUERY/OFFER, sessions, relay, trust levels
│   ├── reasoning/     Reasoner interface: Ollama, template
│   ├── sensors/       Sensor contract: simulated, command; yours
│   ├── triggers/      Trigger contract: console, command; yours
│   ├── agent/         orchestrator + skills (llm_answer, goal, sensor_report)
│   ├── config.py      profile JSON → objects
│   └── cli.py
├── run_agent.py       run one agent from a profile
├── demo/              the weather & clothes demo
├── docs/              PROTOCOL · ARCHITECTURE · DEVELOPMENT · VISION · POC_PLAN
└── tests/             one file per protocol section + the dependency rule
```

## Status

| done | next |
|---|---|
| AMP v1 core: persisted identity, signed CBOR envelopes, beacons, HELLO handshake, encryption, ACK, visibility | Bluetooth transport (beacons as advertisements, envelopes over GATT/L2CAP) |
| Extensions: QUERY/OFFER, session negotiation, relay store-and-forward, trust levels with pinned keys | Session transports (TCP on Wi-Fi, BLE L2CAP); relay gossip message |
| `localhost` and `wifi` transports; agent with skills, trust enforcement, idempotent answers | Pairing ceremony (QR / fingerprint) to pin keys from the CLI |
| Sensors and triggers: driver contracts, LLM proposals validated by JSON schema, `sensor_report` on the mesh | A watch skill: readings → LLM → trigger on a timer, no peer needed |
| Demo verified on one computer over Wi-Fi multicast | Two-device run; UWB proximity |

Why this exists and where it is going: [docs/VISION.md](docs/VISION.md),
[docs/POC_PLAN.md](docs/POC_PLAN.md).

## License

[Apache 2.0](LICENSE)
