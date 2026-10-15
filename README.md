# loquor-aac

**loquor** — Latin for *"I speak"*, *"I talk"*, *"I say"*. **aac** — agent-to-agent communication.

Loquor gives AI agents running on different devices the ability to communicate with each other **offline**, over any local transport — Wi-Fi/IP today, with BLE, UWB, and mesh planned — with no internet access, no cloud services, and no central server.

## The idea

Independent AI-powered devices should be able to:

1. **Discover** each other automatically when they come into range,
2. **Authenticate** and exchange capabilities,
3. **Collaborate** on tasks by exchanging structured messages — each agent contributing its own private knowledge and local reasoning,
4. **Survive disconnection** — task state persists, and an unfinished exchange resumes when peers reconnect.

This is different from asking a cloud chatbot a question: two independent intelligences with *separate* knowledge perform a *coordinated* task, entirely at the edge. See [docs/VISION.md](docs/VISION.md) for the full concept and [docs/POC_PLAN.md](docs/POC_PLAN.md) for the proof-of-concept plan this repository implements.

## The loquor agent

One agent program, configured entirely by a JSON profile: name, description,
capabilities, a system prompt (its knowledge and behaviour), an optional goal
and the local model to use. Every agent follows the same
[broadcast protocol](docs/PROTOCOL.md):

1. **advertises** its unique id and capabilities every 10 seconds,
2. **listens** to every message on the channel,
3. **answers** requests addressed to its uid with its local LLM,
4. if it has a **goal**, reasons about the agents it hears, asks the ones that
   can help, and acts on the answer.

Transports: `localhost` (separate processes on one machine, no network
needed) and `wifi` (LAN multicast, across laptops) today; `bluetooth` is
planned behind the same interface.

```
loquor-aac/
├── docs/                       Vision, PoC plan, architecture, protocol spec
├── src/loquor/                 The library and the `loquor` command
├── examples/weather_and_clothes/   Two agent profiles + how to run them
└── tests/
```

## Quick start

`loquor` ships as one self-contained package for macOS and Linux, on ARM or
x86. Python, its libraries and the LLM engine (llama.cpp) are inside, so
nothing else has to be installed.

```bash
curl -fsSL https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/packaging/install.sh | sh
loquor model pull                                         # once: Qwen2.5 3B, 2.1 GB
loquor run examples/weather_and_clothes/weather_agent.json    # terminal 1
loquor run examples/weather_and_clothes/clothing_agent.json   # terminal 2
```

Within a few seconds the Clothing Advisor hears the Weather Forecaster,
decides it needs a forecast, asks for one and prints what to wear tomorrow.
See [examples/weather_and_clothes](examples/weather_and_clothes/README.md)
for what to expect, offline machines, two laptops, and writing your own agent.

### Development

```bash
python3 -m venv .venv && source .venv/bin/activate    # Python 3.10+
pip install -e ".[dev]"
python -m pytest                                      # no model needed
brew install llama.cpp      # or set LOQUOR_LLAMA_SERVER, to run agents from source
```

Building the package: see [packaging/README.md](packaging/README.md).

## Status and roadmap

- [x] Structured, validated messages (JSON + Pydantic)
- [x] HMAC-authenticated peers (paired identities; pinned per-device credentials planned)
- [x] mDNS discovery (zeroconf) + static discovery for multicast-free environments
- [x] HTTP transport over Wi-Fi/IP behind a transport-agnostic interface
- [x] Persistent task state (SQLite), idempotent retries, resume after disconnect
- [x] Local LLM reasoning via Ollama, with deterministic fallback
- [x] Prompt-configured agent with the broadcast protocol (advertise, listen, answer by uid)
- [x] `localhost` and `wifi` (LAN multicast) transports
- [x] Self-contained package with embedded llama.cpp engine (macOS arm64 built and tested)
- [ ] CI release builds for macOS x64 and Linux arm64/x64
- [ ] Verified two-laptop run over Wi-Fi / hotspot
- [ ] Test 3 benchmark: A alone vs. B alone vs. A+B on labeled cases
- [ ] Bluetooth (BLE) transport adapter (same agent, new transport)
- [ ] UWB / mesh transports, multi-peer collaboration
- [ ] TLS with pinned credentials; explicit pairing ceremony

## License

[Apache 2.0](LICENSE)
