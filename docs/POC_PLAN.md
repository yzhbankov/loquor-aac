# PoC Plan — "Two Machines, One Problem"

The goal is to demonstrate that two independent AI-powered devices can discover each other, exchange knowledge, and solve a problem together **without internet access, a central server, or knowing about each other in advance**. Built on two laptops first, not specialized hardware.

## 1. The scenario: two intelligent machines meet

| | |
|---|---|
| **Device A — Pump Agent** | Knows real-time machine health, sensor measurements, and recent operating trends |
| **Device B — Inspection Agent** | Knows maintenance history, previous failures, and repair instructions |

Each device runs its own AI agent and stores its own private knowledge; they communicate directly and offline. For the prototype, both machines are simulated by laptop applications — no real pumps or robots required.

### How the demo works

1. **Device A detects a problem.** Its local sensor data indicates an abnormal vibration pattern. Its AI agent determines that more information is needed to diagnose the cause.
2. **Device B appears nearby.** Device B becomes reachable on a local offline network. Both agents discover each other automatically and exchange capabilities. A learns that B can provide equipment maintenance diagnostics.
3. **The agents start negotiating.** A asks B whether it has information that could explain the vibration. B searches its local maintenance history and discovers that a bearing was recently replaced. B requests an additional motor-current measurement. A obtains it locally and sends the result.
4. **Both agents contribute to the diagnosis.** B suggests shaft misalignment as a possible cause, supported by the maintenance record. A checks whether its sensor trends are consistent with that explanation. They produce a structured diagnostic recommendation with supporting evidence.
5. **Device B disconnects.** The connection disappears. Both devices retain their local information and task state, and the system can resume an unfinished exchange on reconnection.

This is different from asking ChatGPT a question: two independent intelligences have separate knowledge and perform a coordinated task.

## 2. The architecture

```
   Device A                          Device B
 ┌───────────────────┐            ┌───────────────────────┐
 │ Local LLM         │            │ Local LLM             │
 │ Simulated Sensors │            │ Maintenance Records   │
 │ Agent SDK         │            │ Agent SDK             │
 └─────────┬─────────┘            └──────────┬────────────┘
           │                                 │
           └────── Local Wi-Fi / IP ─────────┘
    Discovery · Authentication · Structured Messages · Task Recovery

        No cloud services or central application server
```

The important decision is to make the agent and networking layers **independent**. The SDK exposes an interface like:

```python
agent = Agent("pump-01")
agent.add_capability("machine_diagnostics")
agent.on_peer_discovered(handle_peer)
await agent.start()
```

And collaboration looks like:

```python
peers = await agent.find_peers(capability="maintenance_diagnostics")
result = await agent.ask(peer=peers[0], task="diagnose", data=sensor_observations)
```

## 3. Technology selection

| Component | Planned | Implemented (see [PROTOCOL.md](PROTOCOL.md)) |
|---|---|---|
| Hardware | Two laptops, ideally 16 GB RAM each | same |
| Programming language | Python | Python 3.10+ |
| AI models | Local quantized 1.5B–3B models | `qwen2.5:3b` by default, any Ollama model |
| AI runtime | [Ollama](https://ollama.com) | Ollama; deterministic template for tests |
| Discovery | mDNS (zeroconf) | 16-byte beacons over the transport itself (no mDNS) |
| Initial transport | Wi-Fi/IP, HTTP(S) | UDP multicast on loopback / LAN; BLE next |
| Structured messages | JSON + Pydantic | CBOR envelopes, Ed25519-signed, ChaCha20-Poly1305 bodies |
| Persistent task state | SQLite | not yet; requests are retried and answered idempotently within a run |
| Authentication | Paired identities and pinned credentials | per-device Ed25519 keys; trust levels 0/1/2, pinned keys = level 1 |
| Interface | Simple web dashboard or terminal logs | terminal logs |

For version one, use one laptop as a Wi-Fi hotspot and connect the second — neither requires internet. (This still requires a local Wi-Fi link; it is not yet a fully infrastructure-free multi-radio implementation.)

Task and capability concepts are borrowed from [A2A](https://github.com/a2aproject/A2A) rather than creating a new semantic standard.

## 4. What the agents actually exchange

Not long natural-language conversations — a few structured message types. For example, A sends:

```json
{
  "type": "task_request",
  "task_id": "diagnosis-001",
  "capability": "maintenance_diagnostics",
  "observations": {
    "vibration_mm_s": 8.2,
    "temperature_c": 87
  }
}
```

B can respond with a request for additional measurements, then return its diagnostic hypothesis and references to the maintenance records used.

Both agents validate incoming messages. The LLM can interpret evidence and propose hypotheses, but **deterministic application logic controls task states, retries, permissions, and final output**.

## 5. The three tests that would convince us

1. **Truly offline.** Disconnect both computers from the internet. They must still discover each other and solve the diagnosis task using locally available models and knowledge.
2. **Interrupted collaboration.** Disconnect Device B midway through a task, then reconnect it. The task must resume without losing its state or producing duplicate results.
3. **Collaboration adds intelligence.** Run a set of labeled diagnostic cases with A alone, B alone, and A+B collaborating. Measure diagnostic accuracy, evidence quality, latency, and resource consumption. This tests whether the additional agent actually improves the result rather than merely exchanging data.

## 6. First two weeks (two developers using AI coding assistance)

| Days | Target |
|---|---|
| 1–2 | Two local AI agents with different knowledge; basic reasoning and reproducible diagnostic cases |
| 3–4 | Discovery and task exchange over Wi-Fi: automatic discovery, capability advertisement, structured messages |
| 5–7 | Complete collaborative diagnosis: multi-turn task, additional evidence requests, diagnostic result |
| 8–10 | Resilience and security: peer authentication, message validation, persistent tasks and reconnection |

These are targets, not guaranteed delivery estimates. BLE, UWB, mesh routing, and a polished user interface are postponed until this works reliably.

## 7. The next PoC: a second transport

Once the initial demo works, add **BLE** as the second transport. That enables another demonstration: a mobile agent approaches an unfamiliar device, discovers its identity and capabilities, establishes a trusted connection, collaborates, then leaves.

BLE implementation is more involved than a simple Python client: [Bleak](https://bleak.readthedocs.io) handles GATT client functionality, while a peripheral/server implementation is also required. After that, test whether the same agent application can function over Wi-Fi and BLE by changing only transport adapters — this demonstrates the part of the concept that a conventional LAN-connected multi-agent system doesn't address.

## Recommendation

Start with two laptops, two real local models, separate knowledge sources, Wi-Fi communication, and collaborative diagnosis. Get that working end to end before spending time on hardware or many transport protocols.

The first milestone is two intelligences that discover and genuinely help each other. The second is making that collaboration reliable and independent of the underlying transport.

---

*Implementation status: see [ARCHITECTURE.md](ARCHITECTURE.md) for what this repository already implements of this plan, and the [README](../README.md) roadmap for what's next.*
