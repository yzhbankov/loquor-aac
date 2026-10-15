# Architecture

loquor is a small platform for agents that talk to each other over local
transports with no server and no internet, speaking **AMP v1**, the Agent
Mesh Protocol ([PROTOCOL.md](PROTOCOL.md)). This document is the design:
the layers, the interfaces that are the whole contract between them, and
what is deliberately left out.

Where to look for what:

| question | document |
|---|---|
| what goes on the wire, byte by byte, with sequence diagrams | [PROTOCOL.md](PROTOCOL.md) |
| how to run and what the demo shows | [../demo/weather_and_clothes/README.md](../demo/weather_and_clothes/README.md) |
| how to test, add a skill / transport / extension, conventions | [DEVELOPMENT.md](DEVELOPMENT.md) |
| why the project exists and the PoC plan | [VISION.md](VISION.md), [POC_PLAN.md](POC_PLAN.md) |

## Principles

1. **One protocol, many transports.** Identity, envelopes, discovery and
   the handshake are defined once; a transport only moves bytes to an address.
   No protocol logic depends on a specific radio.
2. **Structured messages, optional language.** Agents exchange
   `{capability, input}` → `{output}` dicts inside encrypted envelopes. Free
   text is one kind of input, not the only kind.
3. **Deterministic core, LLM at the edge.** The protocol node and the agent
   route, retry and track peers without a model. Only skills call the
   reasoner; no agent or LLM logic leaks into protocol code.
4. **Extend by adding a file, configure by editing JSON.** A skill, reasoner
   or transport is one class selected by `"type": "module:Class"`.
5. **Build a layer when a demo needs it.** Sensors come with the first
   sensor demo.

## Layers

```
 profile.json
      │
┌─────▼──────────────────────────────────────────────────────────────┐
│ agent  — orchestrator (no business logic)                          │
│   capability dispatch · RequestTracker · trust check               │
│   skills/  llm_answer · goal · <yours>            ← business logic │
│            each gets a Context: reasoner, peers, ask(), query()    │
└──────┬───────────────────────────────┬──────────────────┬──────────┘
       │                               │                  │
┌──────▼───────────────┐   ┌───────────▼──────┐   ┌───────▼──────┐
│ extensions           │   │ reasoning        │   │ sensors      │ (later)
│ query · session      │   │ Reasoner         │   │ Sensor       │
│ relay · trust        │   │ ollama · template│   │              │
└──────┬───────────────┘   └──────────────────┘   └──────────────┘
┌──────▼───────────────────────────────────────┐
│ protocol  (AMP v1 core)                      │
│ identity · envelope · discovery · card       │
│ crypto · node · trust levels · transport port│
└──────┬───────────────────────────────────────┘
┌──────▼───────────────────────────────────────┐
│ transports — implement the port, bytes only  │
│ multicast (localhost, wifi) · memory · ble   │
└──────────────────────────────────────────────┘
```

**Dependency rule** (enforced by `tests/test_architecture.py`):

| layer | may import |
|---|---|
| `protocol` | only itself — no radio, no LLM, no agent |
| `transports` | `protocol` (the transport port) |
| `reasoning` | only itself |
| `extensions` | `protocol` |
| `agent`, `config`, `cli` | everything |

```
src/loquor/
├── protocol/     identity.py envelope.py discovery.py card.py crypto.py node.py trust.py transport.py
├── transports/   multicast.py (localhost, wifi) · memory.py (tests)        (ble.py planned)
├── extensions/   query.py · session.py · relay.py · trust.py
├── reasoning/    base.py · ollama.py · template.py
├── agent/        agent.py · tracker.py · context.py · skill.py · skills/{llm_answer,goal}.py
├── config.py     profile JSON → objects ("type" lookup)
└── cli.py
run_agent.py · demo/ · tests/ (one file per section + the dependency rule)
```

## Interfaces

### Transport port — `protocol/transport.py`

```python
Address = Any          # opaque, transport-specific: an ip:port tuple, a BLE handle

class Transport:
    max_payload: int
    async def start(self, on_receive: Callable[[bytes, Address], None]) -> None
    async def broadcast(self, data: bytes) -> None          # everyone in range: beacons, HELLO
    async def send(self, to: Address, data: bytes) -> None  # one peer: everything else
    async def stop(self) -> None
```

The port belongs to the protocol; `transports/` implements it. Multicast
implements `send` as `broadcast` (processes on one host share a port; the
envelope's `to` does the addressing). BLE will implement `broadcast` as a
write to each connection and `send` as a write to one, with chunking inside
the adapter and beacons as advertisements.

### Protocol node — `protocol/node.py`

One `Node` per agent; it owns the transport and does everything AMP
requires: beacons, dedup, signature checks, HELLO/HELLO_ACK, pair keys,
encryption, ACKs, visibility. The layers above see:

```python
class Node:
    peers: PeerDirectory;  keys: dict[agent_id, bytes];  peer_heard: asyncio.Event
    def on_message(handler: (Peer, dict) -> Awaitable)   # decrypted MSG bodies
    def on_peer(handler: (Peer) -> Awaitable)            # after each handshake
    def on_overheard(handler: (Envelope) -> None)        # envelopes for others (relays)
    def register(type: int, handler)                      # extensions own their types
    async def send(peer, body: dict, *, wait_ack=False)   # encrypted MSG
    async def send_type(type, body: bytes, peer=None)     # extension envelopes
    async def hello(peer=None, *, ack=False)
    def update_card(card)                                 # bumps the beacon seq
```

### Reasoner — `reasoning/base.py`

```python
class Reasoner:
    async def complete(self, system: str, prompt: str, *,
                       temperature: float | None = None, schema: dict | None = None) -> str
```

### Skill and Context — `agent/skill.py`, `agent/context.py`

```python
class Skill:
    capability: str = ""                 # advertised in the card; "" = this skill only runs
    description: str = ""
    input_schema: dict | None = None     # published in the card
    output_schema: dict | None = None
    min_trust: int = 0                   # enforced at dispatch
    async def handle(self, input: dict, ctx: Context) -> dict
    async def run(self, ctx: Context) -> None

class Context:
    uid, name, reasoner, sensors
    def peers(self, capability=None) -> list[Peer]          # handshaken peers
    async def ask(self, peer, capability, input, timeout=None) -> dict
    async def query(self, capability, timeout=1.0) -> list[Peer]   # QUERY/OFFER
    async def wait_for_peers(self) -> None
    def log(self, msg, *args)
```

### Extensions — `extensions/`

Each takes a `Node` and registers handlers for its types:

| extension | types | what it adds |
|---|---|---|
| `QueryExtension` | 5, 6 | `query(capability)`: who offers it, on demand |
| `SessionExtension` | 7, 8 | negotiate a bulk link; `SessionTransport` interface (none implemented) |
| `RelayExtension` | — | store-and-forward for addressees out of reach; `RelayStore` interface |
| `TrustExtension` | — | sets `peer.trust` from `TrustStore`s (`PinnedKeys`) and a `ProximityProvider` stub |

The agent enables query, sessions and trust always; relay when the profile
says `"relay": true`.

## Agent internals — `agent/`

| class | responsibility |
|---|---|
| `Agent` | builds the card from its skills, runs a `Node`, dispatches request bodies to skills by capability after the trust check, `ask()` with retries |
| `RequestTracker` | outbound `request_id → Future`; inbound `(sender, request_id) → cached reply`, so a retried request gets the same answer and the skill never runs twice |

Flow of one request:

```
skill A: ctx.ask(peer, "weather_forecast", {"question": ...})
  agent A: body {kind: request, request_id, capability, input} → node.send(peer)   (retried up to 3×)
    node A: MSG envelope, body encrypted with the A–B pair key, signed → transport.send(peer.address)
    node B: verify sig, decrypt, ACK → agent B: tracker.cached? resend : trust ok? skill.handle(input)
    agent B: body {kind: response, output} → node.send(peer A)
  agent A: tracker.resolve(request_id, output) → ctx.ask returns output
```

## Profile — `config.py`

```json
{
  "name": "Weather Station",
  "description": "One sentence other agents read to decide whether you can help them.",
  "system_prompt": "Default persona for every skill that takes one.",
  "visibility": "visible",
  "key_file": "~/.loquor/keys/weather-station.key",
  "pinned_keys": "~/.loquor/pinned_keys.json",
  "relay": false,
  "transport": { "type": "wifi" },
  "reasoner":  { "type": "ollama", "model": "qwen2.5:3b" },
  "skills": [
    { "type": "llm_answer", "capability": "weather_forecast", "temperature": 1.0 },
    { "type": "my_farm.irrigation:Irrigation" }
  ]
}
```

Every part is `{"type": ..., **constructor kwargs}`; `type` is a built-in
name or `"module:Class"`. Only `name`, `description` and `skills` are
required. Command-line `--transport`, `--interface`, `--model` and
`--visibility` override the profile.

## What is deliberately not here

- **No discovery service.** Beacons and HELLO over the transport *are*
  discovery.
- **No persistence beyond keys and pins.** Request state and pair keys live
  for one run.
- **No event bus.** Skills call `ctx.ask`; the agent calls `skill.handle`.
- **No protocol pluggability.** The envelope and its four core types are
  fixed; the codec is CBOR. Devices speaking industrial protocols belong in
  the sensors layer, not as peers.

## Roadmap

1. **Bluetooth** — `transports/ble.py`: beacons as BLE advertisements,
   envelopes over GATT/L2CAP with chunking. Nothing above the transport changes.
2. **Session transports** — TCP on the same Wi-Fi, then BLE L2CAP.
3. **Gossip wire message** between relays (type ≥ 16), and UWB proximity.
4. **Sensors** with the first sensor demo; `Context.sensors` is reserved.
