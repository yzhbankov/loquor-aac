# Architecture

loquor is a small platform for agents that talk to each other over local
transports with no server and no internet. This document is the design:
the layers, the five interfaces that are the whole contract between them,
and what is deliberately left out.

## Principles

1. **One protocol, many transports.** Message format, signing and addressing
   are defined once; a transport only moves bytes to an address.
2. **Structured messages, optional language.** Agents exchange
   `{capability, input}` → `{output}` dicts. Free text is one kind of input,
   not the only kind.
3. **Deterministic core, LLM at the edge.** The agent routes, retries and
   tracks peers without a model. Only skills call the reasoner.
4. **Extend by adding a file, configure by editing JSON.** No plugin
   framework, no registries beyond a dict of names.
5. **Build a layer when a demo needs it.** Sensors come with the first
   sensor demo.

## Layers

```
 profile.json
      │
┌─────▼─────────────────────────────────────────────────────────┐
│ agent  — orchestrator (no business logic)                     │
│   PeerDirectory   RequestTracker   dispatch(capability→skill) │
│                                                               │
│   skills/  llm_answer · goal · <yours>        ← business logic│
│            each gets a Context: reasoner, peers, ask()        │
└───┬───────────────────┬───────────────────────┬───────────────┘
    │                   │                       │
┌───▼────────┐    ┌─────▼──────┐          ┌─────▼──────┐
│ protocol   │    │ reasoning  │          │ sensors    │ (later)
│ Message    │    │ Reasoner   │          │ Sensor     │
│ sign/verify│    │ ollama     │          │            │
│ codec      │    │ template   │          │            │
└───┬────────┘    └────────────┘          └────────────┘
┌───▼────────────────────────────────┐
│ transports — bytes + Address only  │
│ multicast (localhost, wifi) · ble  │
│ memory (tests)                     │
└────────────────────────────────────┘
```

**Dependency rule:** `agent` imports everything; nothing imports `agent`;
`protocol`, `transports` and `reasoning` do not import each other.
`tests/test_architecture.py` enforces it.

```
src/loquor/
├── protocol/     message.py  signing.py  codec.py
├── transports/   base.py  multicast.py  memory.py       (ble.py planned)
├── reasoning/    base.py  ollama.py  template.py
├── agent/        agent.py  directory.py  tracker.py  context.py  skill.py
│   └── skills/   llm_answer.py  goal.py
├── config.py     profile JSON → objects ("type" lookup)
└── cli.py
run_agent.py · demo/ · tests/ (one file per package + the dependency rule)
```

## Interfaces

These are the whole contract. Everything else is implementation.

### Transport — `transports/base.py`

```python
Address = Any          # opaque, transport-specific: an ip:port tuple, a BLE handle

class Transport:
    max_payload: int   # bytes; the agent refuses larger messages
    async def start(self, on_receive: Callable[[bytes, Address], None]) -> None
    async def broadcast(self, data: bytes) -> None          # everyone in range: adverts
    async def send(self, to: Address, data: bytes) -> None  # one peer: request/response
    async def stop(self) -> None
```

Two shapes because real media come in both. Multicast implements `send` as
`broadcast` (processes on one host share a port, so the group address is the
only one that reaches them all; the message carries the recipient's uid
anyway). BLE will implement `broadcast` as a write to each connection and
`send` as a write to one, with chunking inside the adapter.

| transport | status | reach |
|---|---|---|
| `localhost` | implemented | processes on one machine; no network needed |
| `wifi` | implemented | machines on one Wi-Fi, hotspot or wired LAN (multicast, TTL 1) |
| `memory` | implemented | tests: in-process hub |
| `bluetooth` | planned | devices in radio range, no Wi-Fi |

### Protocol — `protocol/`

```python
class Message(BaseModel):
    protocol: str = "loquor/1"
    type: "advert" | "request" | "response" | "error"
    msg_id: str                    # unique; duplicates are dropped
    sender: str                    # uid
    sender_name: str
    recipient: str | None          # uid; None for adverts
    payload: dict
    sent_at: float
    signature: str | None          # HMAC-SHA256 over the canonical JSON of the rest
```

| type | payload |
|---|---|
| `advert` | `{name, description, capabilities: [tag, ...]}` |
| `request` | `{request_id, capability, input: {...}}` |
| `response` | `{request_id, output: {...}}` |
| `error` | `{request_id, code, detail}` — codes: `no_such_capability`, `skill_failed`, `too_large` |

- **Addressing is by uid**, inside the message. A transport address is only a
  hint for where to send the bytes.
- **`MAX_MESSAGE_SIZE` = 4 KB**, a protocol constant independent of any
  transport, so messages stay portable to small radios.
- **`signing.py`** signs with the shared pairing secret today. Per-device keys
  are a change inside that one module.
- **`codec.py`** is `encode(Message) -> bytes` / `decode(bytes) -> Message`.
  One JSON codec ships; a compact or encrypted one is a second implementation
  of these two functions.

Full details and the rules every agent follows: [PROTOCOL.md](PROTOCOL.md).

### Reasoner — `reasoning/base.py`

```python
class Reasoner:
    async def complete(self, system: str, prompt: str, *,
                       temperature: float | None = None, schema: dict | None = None) -> str
```

`ollama` (a locally served model) and `template` (deterministic, no LLM)
ship. The reasoner articulates answers and decisions inside skills; it never
touches protocol state.

### Skill and Context — `agent/skill.py`, `agent/context.py`

```python
class Skill:
    capability: str = ""                           # advertised; "" = this skill only runs
    async def handle(self, input: dict, ctx: Context) -> dict   # answer a request
    async def run(self, ctx: Context) -> None                   # optional background loop

class Context:
    uid, name, reasoner
    sensors: dict                                  # reserved for the sensors layer
    def peers(self, capability: str | None = None) -> list[Peer]
    async def ask(self, peer: Peer, capability: str, input: dict, timeout=None) -> dict
    async def wait_for_peers(self) -> None         # returns when the next advert is heard
    def log(self, msg, *args)
```

Business logic lives in skills. A skill that raises makes the requester
receive `error{skill_failed}`; the agent never dies because a skill did.

## Agent internals — `agent/`

Three small classes, each testable alone:

| class | responsibility |
|---|---|
| `PeerDirectory` | adverts in → `Peer(uid, name, description, capabilities, address, last_seen)`; forgotten after 3 missed adverts |
| `RequestTracker` | outbound `request_id → Future`; inbound `(sender, request_id) → cached reply`, so a retried request gets the same answer and the skill never runs twice |
| `Agent` | start/stop the transport, advert loop, verify + dedupe inbound, dispatch a request to the skill with that capability, `ask()` with retries |

Flow of one request:

```
skill A: ctx.ask(peer, "weather_forecast", {"question": ...})
  agent A: request{request_id, capability, input} → transport.send(peer.address)   (retried up to 3×)
    agent B: verify, dedupe → tracker.cached? resend : skills["weather_forecast"].handle(input, ctx)
    agent B: response{request_id, output} → transport.send(address of the request)
  agent A: tracker.resolve(request_id, output) → ctx.ask returns output
```

## Skills that ship

| skill | `handle` | `run` |
|---|---|---|
| `llm_answer` | `{"question"}` → `{"answer"}` via the reasoner with a `system_prompt` | — |
| `goal` | — | for each peer heard: decide with the reasoner whether it can help and what to ask; `ask`; reason over the answer; done once |

A user skill is one file:

```python
# my_farm/irrigation.py
from loquor import Skill

class Irrigation(Skill):
    capability = "irrigation_plan"

    async def run(self, ctx):
        while True:
            for forecaster in ctx.peers("weather_forecast"):
                out = await ctx.ask(forecaster, "weather_forecast", {"question": "Rain tomorrow?"})
                ctx.log("forecast: %s", out["answer"])
            await ctx.wait_for_peers()
```

## Profile — `config.py`

```json
{
  "name": "Weather Station",
  "description": "One sentence other agents read to decide whether you can help them.",
  "system_prompt": "Default persona for every skill that takes one.",
  "transport": { "type": "wifi" },
  "reasoner":  { "type": "ollama", "model": "qwen2.5:3b" },
  "skills": [
    { "type": "llm_answer", "capability": "weather_forecast", "temperature": 1.0 },
    { "type": "my_farm.irrigation:Irrigation" }
  ]
}
```

Every part is `{"type": ..., **constructor kwargs}`. `type` is a built-in name
(`localhost`, `wifi`; `ollama`, `template`; `llm_answer`, `goal`) or
`"module:Class"` for your own. That lookup is the entire extension mechanism.
`transport` defaults to `localhost`, `reasoner` to Ollama. Command-line
`--transport`, `--interface` and `--model` override the profile.

## What is deliberately not here

- **No discovery layer.** Adverts over the transport *are* discovery.
- **No persistence.** Request state lives in memory for the life of the
  process. Add a store when a demo needs resume-after-restart.
- **No event bus.** Skills call `ctx.ask`; the agent calls `skill.handle`.
  Two directions, no pub-sub.
- **No multi-hop routing.** A peer is someone whose advert you heard directly.
  A mesh transport may route internally and present a flat channel.
- **No protocol pluggability.** The codec boundary exists; the semantics
  (advert / request / response) are fixed. Interop with foreign agent
  protocols is out of scope; devices speaking industrial protocols belong in
  the sensors layer, not as peers.

## Security (PoC level)

All agents share one pairing secret; anyone who has it can join and
impersonate any uid. No replay protection beyond `msg_id` deduplication
within a run, no encryption. Signing sits in the protocol layer, so per-device
keys and encrypted payloads are local changes when they come.

## Roadmap

1. **Bluetooth** — `transports/ble.py`: `bleak` for scanning/connecting,
   a peripheral implementation for accepting connections, chunking to the
   MTU. Nothing above the transport changes.
2. **Sensors** — `sensors/` with the first sensor demo; `Context.sensors`
   is reserved for it.
3. **Two-laptop verification** over Wi-Fi / hotspot.
4. **Security** — per-device keys, pairing ceremony, encrypted payloads.
