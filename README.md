# loquor-aac

**loquor** — Latin for *"I speak"*, *"I talk"*, *"I say"*. **aac** — agent-to-agent communication.

Loquor gives AI agents running on different devices the ability to communicate with each other **offline**, over any local transport — Wi-Fi/IP today, with BLE, UWB, and mesh planned — with no internet access, no cloud services, and no central server.

## The idea

Independent AI-powered devices should be able to:

1. **Discover** each other automatically when they come into range,
2. **Authenticate** and exchange capabilities,
3. **Collaborate** on tasks by exchanging structured messages — each agent contributing its own private knowledge and local reasoning.

This is different from asking a cloud chatbot a question: two independent intelligences with *separate* knowledge perform a *coordinated* task, entirely at the edge. See [docs/VISION.md](docs/VISION.md) for the full concept and [docs/POC_PLAN.md](docs/POC_PLAN.md) for the proof-of-concept plan this repository implements.

## The platform

One agent program, configured by a JSON profile, speaking **AMP v1** (the
Agent Mesh Protocol, [docs/PROTOCOL.md](docs/PROTOCOL.md)) and built from
layers with one small interface each ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):

| layer | what it is | ships with |
|---|---|---|
| **protocol** | Ed25519 identity, signed CBOR envelopes, 16-byte beacons, HELLO handshake, ChaCha20-Poly1305 between peers | types `HELLO` / `HELLO_ACK` / `MSG` / `ACK` |
| **transports** | move bytes to everyone in range or to one peer | `localhost`, `wifi` (multicast); `bluetooth` planned |
| **extensions** | optional protocol features | `QUERY`/`OFFER`, sessions, relay, trust levels |
| **reasoning** | a local LLM behind a one-method interface | Ollama, deterministic template |
| **agent** | orchestrator: dispatch requests to **skills** by capability, enforce trust, ask with retries | skills `llm_answer`, `goal` |

Business logic lives in skills. Your own skill, reasoner or transport is one
Python class, selected in the profile by `"type": "module:Class"`.

```
loquor-aac/
├── docs/                        Vision, PoC plan, architecture, protocol spec
├── src/loquor/                  protocol/ transports/ extensions/ reasoning/ agent/ config.py cli.py
├── run_agent.py                 Runs one agent from a profile JSON
├── demo/weather_and_clothes/    Two agent profiles + how to run them
└── tests/
```

## Quick start

Needs Python 3.10+ and [Ollama](https://ollama.com) running locally.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b                                        # once
python run_agent.py demo/weather_and_clothes/weather_agent.json    # terminal 1
python run_agent.py demo/weather_and_clothes/clothing_agent.json   # terminal 2
```

Within a few seconds the Clothing Advisor hears the Weather Forecaster,
decides it needs a forecast, asks for one and prints what to wear tomorrow.
See [demo/weather_and_clothes](demo/weather_and_clothes/README.md)
for running on two machines. Profile fields: [docs/PROTOCOL.md](docs/PROTOCOL.md).

### Tests

```bash
pip install -e ".[dev]"
python -m pytest                                      # no model needed
```

## Status and roadmap

- [x] AMP v1 core: persisted Ed25519 identity, signed CBOR envelopes, beacons with capability Bloom filter, HELLO handshake, encrypted bodies, ACKs, visibility policies
- [x] Extensions: QUERY/OFFER, session negotiation (interface only), relay store-and-forward, trust levels with pinned keys
- [x] `localhost` and `wifi` (LAN multicast) transports
- [x] Agent with skills: capability dispatch, trust enforcement, idempotent answers, retries
- [x] Local LLM reasoning via Ollama, with deterministic fallback
- [x] Profiles with built-in and custom (`module:Class`) parts
- [ ] Verified two-laptop run over Wi-Fi / hotspot
- [ ] Bluetooth (BLE) transport adapter (same agent, new transport)
- [ ] Session transports (TCP on Wi-Fi, BLE L2CAP); relay gossip wire message
- [ ] UWB proximity (trust level 2)
- [ ] Sensors layer with a first sensor demo

## License

[Apache 2.0](LICENSE)
