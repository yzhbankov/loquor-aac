# loquor-aac

**loquor** — Latin for *"I speak"*, *"I talk"*, *"I say"*. **aac** — agent-to-agent communication.

AI agents on different devices that find each other, authenticate and
collaborate **offline** — over Wi-Fi today, Bluetooth next — with no
internet, no cloud and no central server. Each agent runs its own local LLM
and keeps its own knowledge; together they do what neither could alone.

```
   laptop A                                   laptop B
 ┌──────────────────┐     same Wi-Fi /     ┌───────────────────┐
 │ Clothing Advisor │◀───── hotspot ──────▶│ Weather Forecaster│
 │  (qwen2.5 via    │  beacon · HELLO ·    │  (qwen2.5 via     │
 │   Ollama)        │  encrypted MSG/ACK   │   Ollama)         │
 └──────────────────┘                      └───────────────────┘
      "what do I wear tomorrow?"  ──▶  "12 °C, cloudy"  ──▶  "light jacket, jeans"
```

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
got and the clothes it recommends. Two machines on one Wi-Fi: add
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
  "skills": [
    { "type": "llm_answer", "capability": "soil_report",
      "system_prompt": "You report soil moisture. Reading: {random_number} %." },
    { "type": "my_farm.irrigation:Irrigation" }
  ]
}
```

```python
# my_farm/irrigation.py
from loquor import Skill

class Irrigation(Skill):
    capability = "irrigation_plan"

    async def run(self, ctx):                      # background loop
        while True:
            for forecaster in ctx.peers("weather_forecast"):
                out = await ctx.ask(forecaster, "weather_forecast", {"question": "Rain tomorrow?"})
                ctx.log("forecast: %s", out["answer"])
            await ctx.wait_for_peers()
```

Profile fields, skill interface and extension points: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Develop

```bash
pip install -e ".[dev]"
python -m pytest            # 50 tests, no model or network needed, ~3 s
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
│   ├── agent/         orchestrator + skills (llm_answer, goal)
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
| Demo verified on one machine over Wi-Fi multicast | Two-laptop run; UWB proximity; sensors layer |

Why this exists and where it is going: [docs/VISION.md](docs/VISION.md),
[docs/POC_PLAN.md](docs/POC_PLAN.md).

## License

[Apache 2.0](LICENSE)
