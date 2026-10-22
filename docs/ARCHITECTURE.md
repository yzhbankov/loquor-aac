# Architecture

loquor is a small platform for agents that talk to each other over local
transports with no server and no internet, speaking **AMP v1**, the Agent
Mesh Protocol ([PROTOCOL.md](PROTOCOL.md)). This document is the design:
the layers, the interfaces that are the whole contract between them, and
what is deliberately left out.

Where to look for what:

| question | document |
|---|---|
| the model in one page: sensors, triggers, brain; nodes and agents | [CONCEPTS.md](CONCEPTS.md) |
| what goes on the wire, byte by byte, with sequence diagrams | [PROTOCOL.md](PROTOCOL.md) |
| how to run and what the demos show | [../demo/README.md](../demo/README.md) |
| how to test, add a skill / transport / extension, conventions | [DEVELOPMENT.md](DEVELOPMENT.md) |
| why the project exists and the PoC plan | [VISION.md](VISION.md), [history/POC_PLAN.md](history/POC_PLAN.md) |

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
4. **Configure by editing JSON, extend by adding a file.** A device is
   sensors + triggers + an optional brain in one profile; built-in drivers
   cover files, commands, HTTP, GPIO and serial. Anything else — a
   sensor, trigger, skill, reasoner or transport — is one class selected
   by `"type": "file.py:Class"`.
5. **Sensors in, triggers out, LLM in between — never in control.** A
   sensor is a driver that produces readings; a trigger is a driver that
   performs an action. The LLM reads readings and *proposes* a trigger with
   an input; deterministic code validates the proposal against the trigger's
   schema before anything fires.
6. **Nodes are read, agents are asked.** A device without a brain shares
   its sensors and triggers as data; an agent mirrors them and reads them
   only when its brain needs them. Agents ask each other questions in
   plain language. No polling, no prose where numbers will do.

## Layers

```
 profile.json
      │
┌─────▼─────────────────────────────────────────────────────────────────────┐
│ agent  — orchestrator (no business logic)                                  │
│   capability dispatch · RequestTracker · trust check                       │
│   SensorHub (own + peers' sensors)  TriggerHub (propose → validate → fire) │
│   mirror: peers' shared sensors/triggers → RemoteSensor / RemoteTrigger    │
│   skills/  brain · sensor_report · offer_triggers · <yours>   ← logic      │
│            (llm_answer · goal · collect: single-purpose, older)            │
│            each gets a Context: reasoner, peers, ask(), sensors, triggers  │
└──────┬──────────────────┬──────────────────┬─────────────────┬────────────┘
       │                  │                  │                 │
┌──────▼───────────┐ ┌────▼─────────┐ ┌──────▼───────┐ ┌───────▼────────┐
│ extensions       │ │ reasoning    │ │ sensors      │ │ triggers       │
│ query · session  │ │ Reasoner     │ │ Sensor       │ │ Trigger        │
│ relay · trust    │ │ ollama ·     │ │ simulated ·  │ │ console ·      │
│                  │ │ openai ·     │ │ command ·    │ │ command ·      │
│                  │ │ none ·       │ │ file · http ·│ │ file · http ·  │
│                  │ │ template     │ │ gpio · serial│ │ gpio · serial  │
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
├── reasoning/    base.py · ollama.py · openai.py · none.py (nodes) · template.py · trace.py (records every call)
├── sensors/      base.py (Sensor, Reading, FunctionSensor) · builtin.py (simulated, command, file, http)
│                 hardware.py (gpio, serial)
├── triggers/     base.py (Trigger, FunctionTrigger) · builtin.py (console, command, file, http)
│                 hardware.py (gpio, serial)
├── agent/        agent.py (+ mirroring) · tracker.py · context.py · skill.py · sensors.py · triggers.py
│                 remote.py (a peer's sensor or trigger as a local one)
│                 skills/{brain,sensor_report,offer_triggers,llm_answer,goal,collect}.py
├── settings.py   device config: ~/.loquor/config.json (transport · LLM backend and model · beacons · logging)
├── config.py     profile JSON → objects (brain/share → skills; "type" lookup) · profile.schema.json
├── tools/        doctor.py · check.py · scaffold.py (init) · ask.py · status.py · service.py
└── cli.py        loquor run · check · read · fire · ask · doctor · init · status · trace · config · service
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
    def on_lost(handler: (Peer) -> None)                 # a peer silent for 3 beacon intervals
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
    def __init__(self, *, name=None, description=None, unit=None, interval=...)  # profile overrides
    async def read(self) -> Reading        # required; return self.failed(msg) instead of raising
    async def run(self, publish) -> None   # optional: push loop; default polls read() every interval
```

### Trigger — `triggers/base.py`

```python
class Trigger:
    name: str
    description: str                       # what it does and when to use it — the LLM reads this
    input_schema: dict                     # JSON schema; proposals are validated against it
    def __init__(self, *, name=None, description=None, input_schema=None)       # profile overrides
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
    async def readings(self, names=None)        # stored for polled sensors, fresh for on-demand, in parallel;
                                                # a name not in the hub reads as "not in range"
    def describe(self, readings) -> str         # prompt-ready lines
    def add(sensor); remove(name); local_names() # peers' sensors come and go at runtime

class TriggerHub:                               # ctx.triggers
    def describe(self, names=None) -> str       # catalog for the prompt: name, description, schema
    async def propose(self, system, situation, allowed=None) -> Proposal
        # asks the reasoner for JSON {reason, trigger ∈ allowed ∪ none, input} — reason first,
        # so a small model reasons before it chooses; validates input against the trigger's
        # schema; anything off → Proposal(None, reason)
    async def fire(self, name, input) -> dict   # validates again, runs, logs; TriggerError on failure
    def add(trigger); remove(name); local_names()
```

The flow every skill with sensors and triggers follows:

```
sensor.read() ──▶ SensorHub (latest) ──▶ describe() ──▶ LLM prompt
                                                           │
LLM JSON {reason, trigger, input} ──▶ TriggerHub.propose() validates ──▶ fire() ──▶ trigger.fire()
```

The `brain` uses both: it puts the readings at `{sensors}` when it
answers a question, and for its goal reads them, asks agents, and
proposes one of the triggers in `act`. `sensor_report` answers
`{"sensor"?}` → `{"readings": {...}}` so peers get numbers, not prose,
and lists the sensors in the card; `offer_triggers` does the same for
triggers and fires them on a validated `fire_trigger` request.

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
skill A: ctx.ask(peer, "ask", {"question": ...})
  agent A: body {kind: request, request_id, capability, input} → node.send(peer)   (retried up to 3×)
    node A: MSG envelope, body encrypted with the A–B pair key, signed → transport.send(peer.address)
    node B: verify sig, decrypt, ACK → agent B: tracker.cached? resend : trust ok? skill.handle(input)
    agent B: body {kind: response, output} → node.send(peer A)
  agent A: tracker.resolve(request_id, output) → ctx.ask returns output
```

## Profile and device config — `config.py`, `settings.py`

Two files configure a running device, on purpose:

- the **profile** (`agent.json`) says what the device *is*: name,
  description, sensors, triggers, brain. It travels with the device's
  code and is the same on every device.
- the **device config** (`~/.loquor/config.json`) says what the device
  *has*: which transport, which LLM backend and model its hardware runs,
  how often to beacon, how much to log. `install.sh` writes it once.

A profile may pin a transport, a reasoner or a beacon interval; the
command line overrides both. `build_agent()` applies the precedence,
turns `brain` and `share` into skills (`skill_specs()`: a `Brain`,
`SensorReport`, `OfferTriggers`), and instantiates every part through
the one `"type"` lookup:

```json
{
  "name": "Greenhouse Controller",
  "description": "One sentence other devices read to decide whether you can help them.",
  "sensors":  [ { "type": "file", "name": "cpu_temp", "path": "/sys/class/thermal/thermal_zone0/temp", "scale": 0.001 } ],
  "triggers": [ { "type": "drivers.py:Vent" } ],
  "brain":    { "prompt_file": "prompt.txt", "goal": "…", "every": 15, "act": ["vent", "tank-node:valve"] }
}
```

Every part is `{"type": ..., **constructor kwargs}`; `type` is a built-in
name, `"module:Class"` for an installed module, or `"file.py:Class"` for a
file next to the profile. Sensors and triggers may be plain functions.
`profile.schema.json` validates the whole document before anything is
built. Every field: [USAGE.md](USAGE.md#profile).

## Operator tools — `tools/`, `cli.py`

The agent stays unaware of them; they read what it exposes:

- `check` builds a profile and reads each sensor once through the hubs;
  `read` and `fire` do one part by hand.
- `ask` (`tools/ask.py`) is a throwaway, silent agent with no skills: it
  waits for the target's handshake, sends one request, prints the answer.
- `status` serialises `snapshot(agent)` — peers from the node's
  directory, readings from the `SensorHub`, `TriggerHub.history`, the
  `TracingReasoner`'s last calls, goal results — to a file every two
  seconds; `loquor status` reads the file. No socket: works under a
  service manager and on every OS.
- `trace` reads the `TracingReasoner`'s JSON lines.
- `doctor` checks the device; `scaffold` writes a starter agent;
  `service` writes a systemd or launchd unit.

## Two roles: node and agent

Nothing in the protocol distinguishes them; the profile does. An
**agent** has a brain, so a reasoner. A **node** has none (reasoner
`none`, chosen automatically when no skill's `needs_reasoner` is true)
and offers only data and action skills: `sensor_report` (its readings;
its sensors listed in the card entry's `sensors` key via
`Skill.advertise`) and `offer_triggers` (its triggers, under the entry's
`triggers` key). Both beacon, handshake and encrypt the same way; a
node's card simply has no `llm` entry.

An agent **mirrors** what its peers share (`Agent._mirror_peer`, on every
HELLO): each shared sensor becomes a `RemoteSensor` in its `SensorHub`
and each shared trigger a `RemoteTrigger` in its `TriggerHub`, named
`<peer>:<name>` (`agent/remote.py`). `Node.on_lost` drops them when the
peer goes silent. A `RemoteSensor` is on demand: reading it sends
`sensor_report` to the node, and a `RemoteSource` per node reuses the
answer for every sensor of that node for 5 s. A proposal for a
`RemoteTrigger` is validated by the agent's hub, carried as a
`fire_trigger` request, validated again by the node's hub and fired
there. Nodes do not mirror (they have nothing to think with), and a
skill's defaults use only *local* parts (`local_names()`), so a device
never re-shares what it mirrored.

The `brain` reads nodes' mirrored sensors by default and *asks* agents
(peers with a capability that is not data); an agent's shared sensors
are read only when named. This keeps the LLM where decisions are made
and lets the smallest boards join the mesh; a microcontroller firmware
would implement beacon, HELLO, `sensor_report` and `fire_trigger` and
nothing else.

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
4. **Pushed readings** — a node notifies its subscribers when a reading
   crosses a threshold, so an agent's brain can wake on an event instead
   of a round.
5. **A PyPI release**, so a device needs `pip install loquor-aac[ble,hw]`
   and no clone.
6. **A node firmware** for microcontrollers (ESP32: libsodium, CBOR,
   BLE advertising): beacon, HELLO, `sensor_report` and `fire_trigger` only.
