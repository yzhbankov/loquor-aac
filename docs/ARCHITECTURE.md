# Architecture (PoC v0.1)

This document describes what is implemented in `src/loquor/` and how the layers fit together. The design rule throughout: **the agent layer is independent of discovery and transport**, so future transports (BLE, UWB, mesh) are adapters, not rewrites.

```
            ┌─────────────────────────────────────────┐
            │                 Agent                   │
            │  capabilities · task state machine ·    │
            │  ask()/resume_pending() · find_peers()  │
            └──┬───────────┬───────────┬──────────┬───┘
               │           │           │          │
        ┌──────┴───┐ ┌─────┴────┐ ┌────┴─────┐ ┌──┴──────────┐
        │Discovery │ │Transport │ │ Identity │ │  TaskStore  │
        │ zeroconf │ │  HTTP/IP │ │HMAC pair- │ │   SQLite   │
        │ / static │ │ (BLE…)   │ │ing secret│ │            │
        └──────────┘ └──────────┘ └──────────┘ └─────────────┘
                                       │
                               ┌───────┴───────┐
                               │   Reasoner    │
                               │Ollama/template│
                               └───────────────┘
```

## Modules

### `messages.py` — the wire format

One Pydantic model, `Envelope`, carries every message; `type` selects the semantics:

| Type | Direction | Meaning |
|---|---|---|
| `task_request` | requester → server | start a task: capability + observations |
| `info_request` | server → requester | evidence missing; names what's `needed` and why |
| `info_response` | requester → server | measurements answering an `info_request` |
| `task_result` | server → requester | final structured result |
| `error` | either | validation/auth/processing failure |

All inbound messages are schema-validated before any logic runs. Task/capability vocabulary follows A2A concepts (tasks with ids and lifecycle, capabilities advertised at discovery).

### `identity.py` — peer authentication

PoC model: paired identities with a pre-shared pairing secret; every envelope is HMAC-SHA256-signed over its canonical JSON and verified on receipt. Unauthenticated messages are rejected before any handler runs. Production path: per-device keypairs, pinned credentials from an explicit pairing ceremony, TLS on the transport.

### `store.py` — persistent task state

SQLite table of tasks keyed by `task_id`, with `role` (`requester`/`server`), state, and merged JSON data (observations, collected measurements, result). This is what makes interruption survivable:

- The **requester** marks a task `interrupted` when the peer becomes unreachable and retries it later (`resume_pending`) with the *same* `task_id`.
- The **server** persists per-task progress and answers a retried `task_request` for a completed task idempotently from the store — no duplicate work, no duplicate results.

### `discovery.py` — finding peers

`Discovery` interface with two implementations:

- `ZeroconfDiscovery` — mDNS/DNS-SD (`_loquor._tcp.local.`), advertising agent id and capabilities in TXT records. How two laptops on the same Wi-Fi find each other with zero configuration.
- `StaticDiscovery` — fixed peer list for multicast-free environments (containers, CI) and deterministic tests.

### `transports/` — moving envelopes

`Transport` interface: `start(handler) -> port`, `send(peer, envelope) -> envelope`. The shipped `HttpTransport` POSTs signed envelopes to `/loquor/v1/message` over Wi-Fi/IP (aiohttp both sides) and serves `/loquor/v1/capabilities`. A `PeerUnreachable` exception is the uniform signal for disconnection, whatever the transport. BLE (Bleak client + a peripheral implementation) is the planned second adapter.

### `reasoning.py` — local models

`Reasoner` interface with `OllamaReasoner` (local Ollama HTTP API, small quantized 1.5B–3B models) and a deterministic `TemplateReasoner` fallback. The reasoner **articulates** hypotheses and recommendations; it never decides protocol state. Deterministic logic owns task states, retries, permissions, and the final structured output — which is also why the whole system is testable in CI without a model.

### `agent.py` — the state machine

`Agent` composes the above. Server side, a capability handler receives a `TaskContext` (observations + evidence so far) and returns either a result `dict` or `NeedInfo(needed, reason)`; the agent persists state transitions (`in_progress` → `awaiting_info` → … → `completed`) and replies accordingly. Requester side, `ask()` drives the loop: send request, answer `info_request`s via an info provider callback, persist every step, surface the final result — and park the task as `interrupted` on `PeerUnreachable` for `resume_pending()` to finish later.

## The demo (`examples/two_machines_one_problem/`)

- `pump_agent.py` — Device A: detects the vibration anomaly from its trend data, finds a `maintenance_diagnostics` peer, supplies motor-current and spectrum measurements on request, cross-checks the returned hypothesis against local trends, emits the final structured recommendation.
- `inspection_agent.py` — Device B: searches its maintenance records, requests the measurements it lacks, selects a failure-mode hypothesis deterministically from the combined evidence, attaches repair instructions and record references.
- `run_demo.py` — both agents in one process on loopback (CI-friendly); `--interrupt` demonstrates disconnect-and-resume. The real two-laptop setup runs the two scripts with `--mdns`.

## Test coverage vs. the PoC's three tests

1. **Truly offline** — everything runs on loopback/LAN with no external calls; the only optional network dependency is a *local* Ollama. Covered by `tests/test_demo.py` and `tests/test_collaboration.py`.
2. **Interrupted collaboration** — `test_interrupt_and_resume` kills the server mid-exchange, restarts it on the same store, and verifies resume with no duplicates; also runnable interactively via `run_demo.py --interrupt`.
3. **Collaboration adds intelligence** — not yet implemented: needs a labeled case set and an A-alone/B-alone/A+B benchmark harness. Tracked on the roadmap.
