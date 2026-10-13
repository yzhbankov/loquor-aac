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

## What's here now (PoC v0.1)

A working Python implementation of the first PoC, **"Two Machines, One Problem"**: two independent agents — a *Pump Agent* with live sensor data and an *Inspection Agent* with maintenance history — discover each other on a local network, negotiate over additional evidence, and jointly produce a structured diagnostic recommendation. Either side can disconnect mid-task and the exchange resumes without duplicate results.

```
loquor-aac/
├── docs/                 Idea, PoC plan, architecture
├── src/loquor/           The library: agent, discovery, transports, messages,
│                         identity, persistent task store, reasoning backends
├── examples/
│   └── two_machines_one_problem/   The PoC demo (two device agents + runner)
└── tests/                End-to-end and unit tests
```

The layers are deliberately independent: the agent does not know which transport or discovery mechanism it runs on, so new transports (BLE next) are adapters, not rewrites.

```python
agent = Agent("pump-01", pairing_secret=secret)
agent.add_capability("machine_diagnostics", handle_diagnosis)
agent.on_peer_discovered(handle_peer)
await agent.start()

peers = await agent.find_peers(capability="maintenance_diagnostics")
result = await agent.ask(peers[0], "maintenance_diagnostics", data=sensor_observations)
```

## Quick start

Requires Python 3.10+.

```bash
pip install -e ".[dev]"

# Full collaborative diagnosis, both agents in one process on loopback:
cd examples/two_machines_one_problem
python run_demo.py

# Test 2 — interrupted collaboration: B disconnects mid-task, then resumes:
python run_demo.py --interrupt

# Run the test suite:
python -m pytest
```

### Two real machines

On laptop B (knows the maintenance history):

```bash
export LOQUOR_PAIRING_SECRET=<shared-secret>
python examples/two_machines_one_problem/inspection_agent.py --mdns
```

On laptop A (detects the anomaly) on the same Wi-Fi — one laptop can host a hotspot for the other; no internet is needed:

```bash
export LOQUOR_PAIRING_SECRET=<shared-secret>
python examples/two_machines_one_problem/pump_agent.py --mdns
# or, where multicast/mDNS is unavailable:
python examples/two_machines_one_problem/pump_agent.py --peer <laptop-B-ip>:8771
```

### Local LLM (optional)

If [Ollama](https://ollama.com) is running locally with a small quantized model (e.g. `ollama pull qwen2.5:1.5b`), both agents use it to articulate their reasoning. Without it they fall back to deterministic templates — **protocol state is always controlled by deterministic application logic, never by the model**, so the collaboration works and is testable either way.

## Status and roadmap

- [x] Structured, validated messages (JSON + Pydantic)
- [x] HMAC-authenticated peers (paired identities; pinned per-device credentials planned)
- [x] mDNS discovery (zeroconf) + static discovery for multicast-free environments
- [x] HTTP transport over Wi-Fi/IP behind a transport-agnostic interface
- [x] Persistent task state (SQLite), idempotent retries, resume after disconnect
- [x] Local LLM reasoning via Ollama, with deterministic fallback
- [ ] Test 3 benchmark: A alone vs. B alone vs. A+B on labeled diagnostic cases
- [ ] BLE transport adapter (second transport; same agent application)
- [ ] UWB / mesh transports, multi-peer collaboration
- [ ] TLS with pinned credentials; explicit pairing ceremony

## License

[Apache 2.0](LICENSE)
