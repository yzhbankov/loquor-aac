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
5. **Sensors in, triggers out, LLM in between — never in control.** A
   sensor is a driver that produces readings; a trigger is a driver that
   performs an action. The LLM reads readings and *proposes* a trigger with
   an input; deterministic code validates the proposal against the trigger's
   schema before anything fires.

## Layers

```
 profile.json
      │
┌─────▼─────────────────────────────────────────────────────────────────────┐
│ agent  — orchestrator (no business logic)                                  │
│   capability dispatch · RequestTracker · trust check                       │
│   SensorHub (latest readings)      TriggerHub (propose → validate → fire)  │
│   skills/  llm_answer · goal · sensor_report · <yours>    ← business logic │
│            each gets a Context: reasoner, peers, ask(), sensors, triggers  │
└──────┬──────────────────┬──────────────────┬─────────────────┬────────────┘
       │                  │                  │                 │
┌──────▼───────────┐ ┌────▼─────────┐ ┌──────▼───────┐ ┌───────▼────────┐
│ extensions       │ │ reasoning    │ │ sensors      │ │ triggers       │
│ query · session  │ │ Reasoner     │ │ Sensor       │ │ Trigger        │
│ relay · trust    │ │ ollama ·     │ │ simulated ·  │ │ console ·      │
│                  │ │ template     │ │ command ·    │ │ command ·      │
│                  │ │              │ │ <your driver>│ │ <your driver>  │
└──────┬───────────┘ └──────────────┘ └──────────────┘ └────────────────┘
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
| `sensors`, `triggers` | only themselves — drivers know nothing about agents, peers or LLMs |
| `agent`, `config`, `settings`, `tools`, `cli` | everything |

```
src/loquor/
├── protocol/     identity.py envelope.py discovery.py card.py crypto.py node.py trust.py transport.py
├── transports/   multicast.py (localhost, wifi) · ble.py (bluetooth) · memory.py (tests)
├── extensions/   query.py · session.py · relay.py · trust.py
├── reasoning/    base.py · ollama.py · template.py · trace.py (records every call)
├── sensors/      base.py (Sensor, Reading, FunctionSensor) · builtin.py (simulated, command)
├── triggers/     base.py (Trigger, FunctionTrigger) · builtin.py (console, command)
├── agent/        agent.py · tracker.py · context.py · skill.py · sensors.py · triggers.py
│                 skills/{llm_answer,goal,sensor_report}.py
├── settings.py   device config: ~/.loquor/config.json (transport · model · beacon interval · logging)
├── config.py     profile JSON → objects ("type" lookup) · profile.schema.json
├── tools/        doctor.py · check.py · scaffold.py (init) · status.py · service.py
└── cli.py        loquor run · check · doctor · init · status · trace · config · service
install.sh · run_agent.py · demo/ · tests/ (one file per section + the dependency rule)
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
envelope's `to` does the addressing). BLE (`transports/ble.py`) implements
`broadcast` as a write to every peer heard recently plus re-advertising,
`send` as a write to one peer's GATT inbox, chunking to the MTU inside the
adapter, and the beacon as the advertisement's local name. The radio stack
sits behind a `BleBackend` seam (`bleak` + `bless`, or an in-memory fake
for tests).

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

### Sensor — `sensors/base.py`

```python
@dataclass
class Reading:
    sensor: str; value: Any; unit: str = ""; at: float; error: str | None = None
    def text(self) -> str                  # "thermometer: 12.4 °C (3 s ago)" — what the LLM sees

class Sensor:
    name: str; description: str; unit: str
    interval: float | None = None          # poll every N s into the store; None = on demand
    async def read(self) -> Reading        # required; return self.failed(msg) instead of raising
    async def run(self, publish) -> None   # optional: push loop; default polls read() every interval
```

### Trigger — `triggers/base.py`

```python
class Trigger:
    name: str
    description: str                       # what it does and when to use it — the LLM reads this
    input_schema: dict                     # JSON schema; proposals are validated against it
    async def fire(self, input: dict) -> dict
```

Both may also be plain functions (`FunctionSensor`, `FunctionTrigger` wrap
them). Both import nothing but their base module: a UART thermometer and a
relay over a serial line are written the same way as the demo's random
weather and terminal card.

### Hubs — `agent/sensors.py`, `agent/triggers.py`

```python
class SensorHub:                                # ctx.sensors
    def latest(self, name) -> Reading | None    # from the store, no I/O
    async def read(self, name) -> Reading       # fresh, also stored
    async def readings(self, names=None)        # stored for polled sensors, fresh for on-demand
    def describe(self, readings) -> str         # prompt-ready lines

class TriggerHub:                               # ctx.triggers
    def describe(self, names=None) -> str       # catalog for the prompt: name, description, schema
    async def propose(self, system, situation, allowed=None) -> Proposal
        # asks the reasoner for JSON {trigger ∈ allowed ∪ null, input, reason};
        # validates input against the trigger's schema; anything off → Proposal(None, reason)
    async def fire(self, name, input) -> dict   # validates again, runs, logs; TriggerError on failure
```

The flow every skill with sensors and triggers follows:

```
sensor.read() ──▶ SensorHub (latest) ──▶ describe() ──▶ LLM prompt
                                                           │
LLM JSON {trigger, input, reason} ──▶ TriggerHub.propose() validates ──▶ fire() ──▶ trigger.fire()
```

Skills that use them: `llm_answer` with `"sensors": [...]` puts the readings
at `{sensors}` in its prompt; `goal` gathers answers from one or many peers (`min_answers`, `settle`) and,
with `"triggers": [...]`, proposes and fires one with its conclusion; `sensor_report` answers
`{"sensor"?}` → `{"readings": {...}}` so peers get numbers, not prose.

### Skill and Context — `agent/skill.py`, `agent/context.py`

```python
class Skill:
    capability: str = ""                 # advertised in the card; "" = this skill only runs
    description: str = ""
    input_schema: dict | None = None     # published in the card
    output_schema: dict | None = None
    min_trust: int = 0                   # enforced at dispatch
    sensors: tuple[str, ...] = ()        # names it uses; checked at startup
    triggers: tuple[str, ...] = ()
    async def handle(self, input: dict, ctx: Context) -> dict
    async def run(self, ctx: Context) -> None

class Context:
    uid, name, reasoner
    sensors: SensorHub; triggers: TriggerHub
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

## Profile and device config — `config.py`, `settings.py`

Two files configure a running agent, on purpose:

- the **profile** (`agent.json`) says what the agent *is*: name,
  description, persona, sensors, triggers, skills. It travels with the
  agent's code and is the same on every device.
- the **device config** (`~/.loquor/config.json`) says what the device
  *has*: which transport, which model its hardware can run, how often to
  beacon, how much to log. `install.sh` writes it once.

A profile may pin a transport, a reasoner or a beacon interval; the
command line overrides both. `build_agent()` applies the precedence and
instantiates every part through the one `"type"` lookup:

```json
{
  "name": "Weather Station",
  "description": "One sentence other agents read to decide whether you can help them.",
  "system_prompt_file": "prompt.txt",
  "sensors":  [ { "type": "weather_sensor.py:RandomWeather", "name": "weather", "interval": 5 } ],
  "triggers": [ { "type": "console", "name": "print" } ],
  "skills": [
    { "type": "llm_answer", "capability": "weather_forecast", "sensors": ["weather"] },
    { "type": "sensor_report" },
    { "type": "my_farm.irrigation:Irrigation" }
  ]
}
```

Every part is `{"type": ..., **constructor kwargs}`; `type` is a built-in
name, `"module:Class"` for an installed module, or `"file.py:Class"` for a
file next to the profile. Sensors and triggers may be plain functions.
`profile.schema.json` validates the whole document before anything is
built. Every field: [USAGE.md](USAGE.md#profile).

## Operator tools — `tools/`, `cli.py`

The agent stays unaware of them; they read what it exposes:

- `check` builds a profile and reads each sensor once through the hubs.
- `status` serialises `snapshot(agent)` — peers from the node's
  directory, readings from the `SensorHub`, `TriggerHub.history`, the
  `TracingReasoner`'s last calls, goal results — to a file every two
  seconds; `loquor status` reads the file. No socket: works under a
  service manager and on every OS.
- `trace` reads the `TracingReasoner`'s JSON lines.
- `doctor` checks the device; `scaffold` writes a starter agent;
  `service` writes a systemd or launchd unit.

## What is deliberately not here

- **No discovery service.** Beacons and HELLO over the transport *are*
  discovery.
- **No persistence beyond keys and pins.** Request state and pair keys live
  for one run.
- **No event bus.** Skills call `ctx.ask`; the agent calls `skill.handle`.
- **No protocol pluggability.** The envelope and its four core types are
  fixed; the codec is CBOR. Devices speaking industrial protocols are
  sensors or triggers of an agent, not peers.
- **No reading history.** The hub keeps the latest reading per sensor; a
  time series store is a later implementation behind the same hub.

## Roadmap

1. **Bluetooth polish** — verified macOS ↔ Ubuntu end to end; next are
   per-central addressing on stacks that expose it, and an L2CAP session
   transport for bulk data.
2. **Session transports** — TCP on the same Wi-Fi, then BLE L2CAP.
3. **Gossip wire message** between relays (type ≥ 16), and UWB proximity.
4. **Watch skill** — "every N seconds, put these readings in front of the
   LLM, let it pick a trigger": the sensor → LLM → trigger loop with no
   peer involved. All the parts exist; it is a 30-line skill.
5. **A PyPI release**, so a device needs `pip install loquor-aac[ble]`
   and no clone.
