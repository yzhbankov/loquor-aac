# Development

How to set up, test, and extend loquor in Python. Using it — the
commands, the device config, every profile field — is in
[USAGE.md](USAGE.md); connecting hardware without code in
[HARDWARE.md](HARDWARE.md); the layers and interfaces in
[ARCHITECTURE.md](ARCHITECTURE.md); the wire format in
[PROTOCOL.md](PROTOCOL.md).

## Setup

```bash
./install.sh --no-ollama         # or: python3 -m venv .venv && pip install -e ".[dev,ble]"
source .venv/bin/activate
pip install -e ".[dev]"          # pytest
python -m pytest                 # ~8 s, no model, no network, no radio
```

Ollama is only needed to *run* an agent with an LLM; the tests use
scripted reasoners. `python run_agent.py` runs the CLI from the source
tree without installing.

Dependencies: `aiohttp` (LLM clients, `http` drivers), `cbor2`
(envelopes), `pynacl` (Ed25519, X25519, ChaCha20-Poly1305 via
libsodium), `jsonschema` (the gate between an LLM proposal and a
trigger). Optional: `.[ble]` (bleak, bless) for Bluetooth, `.[hw]`
(gpiozero, pyserial) for the `gpio` and `serial` drivers.

## The dependency rule

The layers and the file tree are in [ARCHITECTURE.md](ARCHITECTURE.md#layers).
`tests/test_architecture.py` greps imports and fails the build if a lower
layer reaches up: `protocol` imports nothing else; `transports` and
`extensions` only `protocol`; `reasoning`, `sensors`, `triggers` only
themselves. If you need something from `agent/` inside `protocol/`, the
design is wrong, not the test.

## Tests

One file per protocol section plus the agent and config:

| file | covers |
|---|---|
| `test_identity.py` | §1 keys, agent_id, persistence |
| `test_envelope.py` | §2 CBOR round-trip, sign/verify, tamper, ttl, size, dedup, unknown types |
| `test_discovery.py` | §3 beacon layout, Bloom false-positive rate, visibility, directory |
| `test_handshake.py` | §4-5 HELLO/HELLO_ACK, encryption, ACK, forged/tampered/stranger rejection, policies, card refresh |
| `test_extensions.py` | QUERY/OFFER, sessions, relay store + forwarding, pinned keys, trust |
| `test_sensors.py` | the sensor contract, polling, built-ins, `SensorHub` |
| `test_triggers.py` | the trigger contract, built-ins, `TriggerHub.propose` validation and `fire` |
| `test_agent.py` | dispatch, errors, trust at dispatch, retries, idempotent answers, sensors in prompts, goal firing a trigger, multicast end to end |
| `test_config.py` | profiles, overrides, `module:Class`, key files; every demo profile builds |
| `test_transports.py` | memory hub, multicast round-trip, transport selection |
| `test_ble.py` | beacon-in-name codec, chunking/reassembly, the BLE transport and a full handshake + exchange over a fake radio |
| `test_settings.py` | device config: defaults, file, validation, precedence over the profile and under the command line |
| `test_tools.py` | tracing reasoner, status snapshot and file, doctor, check, init, service units |
| `test_cli.py` | every `loquor` subcommand short of a real radio or model |
| `test_demo_triage.py` | the triage demo end to end with a scripted clinician |
| `test_brain.py` | `brain` and `share` in profiles; class parts without `__init__`; remote sensors read on demand and cached; the brain answering, pursuing a goal every N s, asking an agent, firing a node's trigger; peers lost |
| `test_nodes.py` | nodes without an LLM, the `collect` skill, nodes' triggers fired remotely, `init --node`, `llm false` |
| `test_drivers.py` | the `file`, `http`, `gpio`, `serial` sensors and triggers (fake GPIO and serial libraries, a local HTTP server) |
| `test_openai.py` | the OpenAI-compatible reasoner against a local fake server; its device settings and doctor checks |
| `test_demos.py` | the garden, greenhouse and weather demos as written, end to end with a scripted LLM; `loquor ask` |
| `test_architecture.py` | the dependency rule |

Patterns worth copying:

- **`MemoryHub`** (`transports/memory.py`) is an in-process transport. Every
  node on the hub hears every broadcast; `hub.taps` lets a test see raw bytes.
- **`beacon_interval=0.05`** makes discovery and pruning fast in tests.
- **`ScriptedReasoner`** (`test_agent.py`) and `Scripted` (`test_demos.py`)
  stand in for an LLM: they answer decision prompts with JSON and read
  numbers out of the prompt like a careful model would.
- **`demo_agents()`** (`test_demos.py`) builds real profiles on the memory
  hub — the way to test a profile you ship.
- Tests that need real multicast `pytest.skip` when the environment has none
  (some CI runners).

## Profile reference

Every field, every built-in part and its arguments, and the device
config are in [USAGE.md](USAGE.md#profile). The schema that validates
profiles is `src/loquor/profile.schema.json`; a change to a profile
field changes the schema, the table in USAGE.md and `test_config.py`
in the same PR.

## Add a skill

Most devices need none: the `brain` answers, pursues a goal and acts;
`share` exposes sensors and triggers. Write a skill for behaviour that is
neither — a fixed control loop with no LLM, a custom capability with a
structured answer. A skill is one class. It can answer requests
(`handle`), run a loop (`run`), or both; a profile adds it under
`"skills"`.

```python
from loquor import Skill, Trust

class SoilReport(Skill):
    capability = "soil_report"                       # advertised; requests dispatch here
    description = "Current soil moisture in percent."
    input_schema = {"type": "object"}                # published in the card (optional)
    output_schema = {"type": "object", "properties": {"moisture": {"type": "number"}}}
    min_trust = Trust.HEARD                          # or VERIFIED / PROXIMATE

    def __init__(self, sensor_id: str = "s1"):       # kwargs come from the profile
        self.sensor_id = sensor_id

    async def handle(self, input, ctx):
        return {"moisture": 23.5, "sensor": self.sensor_id}

    async def run(self, ctx):                        # optional background loop
        while True:
            for peer in ctx.peers("weather_forecast"):
                out = await ctx.ask(peer, "weather_forecast", {"question": "Rain tomorrow?"})
                ctx.log("forecast: %s", out["answer"])
            await ctx.wait_for_peers()               # returns on the next beacon/HELLO
```

A skill that never calls the reasoner sets `needs_reasoner = False`
(`sensor_report` and `offer_triggers` do); a profile made only of such
skills runs as a node without an LLM. A skill that exposes something
peers should know about overrides `advertise(agent)` and returns extra
keys for its card entry (`sensor_report` lists its sensors,
`offer_triggers` its triggers with their schemas). What `ctx` offers: `reasoner.complete(system, prompt, temperature=, schema=)`,
`peers(capability=None)`, `ask(peer, capability, input, timeout=)`,
`query(capability)` (QUERY/OFFER), `wait_for_peers()`, `log()`,
`sensors` (a `SensorHub`: `readings()`, `latest()`, `read()`, `describe()`,
`local_names()`), `triggers` (a `TriggerHub`: `propose()`, `fire()`,
`local_names()`), and `remote_sensors()` / `remote_triggers()` — the
peers' parts mirrored into the hubs, by hub name. On an agent the hubs
hold `soil-node:soil_moisture` next to its own sensors; reading one asks
the node.
`ask` raises `RequestError` (the peer answered an error) or `TimeoutError`.
A skill lists the sensor and trigger names it uses in `self.sensors` /
`self.triggers`; the agent refuses to start if the profile lacks a local
one (`device:name` ones are checked at runtime: a node may not be in
range yet).

Use it from a profile:

```json
{ "type": "my_farm.soil:SoilReport", "sensor_id": "north" }
```

Rules: raise to send the requester `error{skill_failed}`; return a dict;
keep responses under ~60 KB (the envelope cap is 64 KB); never block the
event loop (use `await`).

## Add a sensor

A sensor is a driver: it owns its device (serial port, I²C, GPIO, HTTP, a
file, a subprocess) and hands the agent readings through one contract.

```python
# drivers/uart_thermometer.py
import asyncio
import serial                                    # pyserial; any library you like
from loquor.sensors import Sensor

class UartThermometer(Sensor):
    name = "thermometer"
    description = "Outdoor temperature from the DS18B20 on the UART."
    unit = "°C"
    interval = 10

    def __init__(self, port="/dev/ttyUSB0", baud=9600, **fields):
        super().__init__(**fields)               # name, description, unit, interval from the profile
        self.port = serial.Serial(port, baud, timeout=2)

    async def read(self):
        line = await asyncio.to_thread(self.port.readline)      # blocking I/O off the loop
        try:
            return self.reading(float(line))
        except ValueError:
            return self.failed(f"bad frame {line!r}")           # never raise out of read()
```

Profile: `{"type": "drivers/uart_thermometer.py:UartThermometer", "port": "/dev/ttyAMA0", "interval": 30}`
(a path relative to the profile), or `"my_pkg.drivers:UartThermometer"` for
an installed module. Constructor kwargs come from the profile. A sensor
that needs no arguments of its own needs no `__init__` at all: the base
class takes `name`, `description`, `unit` and `interval`. (For a serial
line, the built-in `serial` sensor may already do: [HARDWARE.md](HARDWARE.md#serial).)

A plain function is enough for simple cases:

```python
def cpu_temp():
    """CPU temperature."""
    return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
# {"type": "drivers/misc.py:cpu_temp", "name": "cpu", "unit": "°C", "interval": 30}
```

Rules:
1. `read()` returns a `Reading` (`self.reading(value)`) or `self.failed(msg)`.
   Never raise; a broken sensor is "no data", not a crash.
2. `value` is JSON-safe: number, string, or a flat dict. Units in `unit`.
3. Blocking calls go through `asyncio.to_thread`.
4. Set `interval` to be polled into the store; override `run(publish)` for a
   device that pushes; leave both out for on-demand only.
5. Import nothing from loquor but `loquor.sensors`.
6. Test it with `SensorHub([YourSensor(fake_port)])` — no hardware in tests.

The brain reads it (`thermometer: 12.4 °C (3 s ago)` at `{sensors}` in
its prompt), and `share` exposes it to peers as structured data, listed
with its unit and description in the capability card.

## Add a trigger

A trigger is a driver for an action. Its `input_schema` is the contract the
LLM is held to: the hub validates every proposal against it before `fire()`
runs, so `fire()` can trust its input.

```python
# drivers/relay.py
import asyncio
import serial
from loquor.triggers import Trigger, TriggerError

class PumpRelay(Trigger):
    name = "pump"
    description = "Switch the irrigation pump on or off. Safe to repeat."
    input_schema = {
        "type": "object",
        "properties": {"on": {"type": "boolean"}, "seconds": {"type": "integer", "minimum": 1, "maximum": 600}},
        "required": ["on"],
    }

    def __init__(self, port="/dev/ttyUSB1", **fields):
        super().__init__(**fields)               # name, description, input_schema from the profile
        self.port = serial.Serial(port, 9600, timeout=2)

    async def fire(self, input):
        frame = b"ON\n" if input["on"] else b"OFF\n"
        await asyncio.to_thread(self.port.write, frame)
        ack = await asyncio.to_thread(self.port.readline)
        if ack.strip() != b"OK":
            raise TriggerError(f"relay answered {ack!r}")
        return {"on": input["on"]}
```

Profile: `{"type": "drivers/relay.py:PumpRelay", "port": "/dev/ttyUSB1"}`.
Or a function: `{"type": "drivers/misc.py:beep", "name": "beep", "description": "Sound the buzzer.", "input_schema": {"type": "object"}}`.

Rules:
1. `name`, `description`, `input_schema` are required. Write the description
   for the LLM: what it does, when to use it, whether repeating is safe.
2. `fire()` does one thing, returns a small dict, raises on failure
   (`TriggerError` or anything — the hub wraps it; the agent logs it).
3. Blocking calls through `asyncio.to_thread`; bound the time.
4. Import nothing from loquor but `loquor.triggers`.
5. Test it with `TriggerHub([YourTrigger(fake_port)]).fire(name, input)` for
   valid and invalid input; the invalid one must never reach `fire()`.

The brain uses it by name: with `"act": ["pump"]` it asks the LLM for
`{reason, trigger, input}` and fires only if the input validates. On a
node, `share` offers it to agents in range as `<node>:pump`, validated
again on the node before it runs. Your own skill does the same in two
lines:

```python
proposal = await ctx.triggers.propose(system_prompt, situation, allowed=["pump"])
if proposal:
    await ctx.triggers.fire(proposal.trigger, proposal.input)
```

or skips the LLM entirely: `await ctx.triggers.fire("pump", {"on": False})`.

## Add a transport

Implement the port in `protocol/transport.py`:

```python
from loquor.protocol.transport import Transport, Address, OnReceive

class MyLink(Transport):
    max_payload = 4096                       # chunk inside if the medium is smaller

    async def start(self, on_receive: OnReceive) -> None: ...   # call on_receive(bytes, address)
    async def broadcast(self, data: bytes) -> None: ...         # everyone in range
    async def send(self, to: Address, data: bytes) -> None: ... # one peer
    async def stop(self) -> None: ...
```

- Beacons are raw 16-byte datagrams starting with `b"AM"`; everything else
  is a CBOR envelope. Your `on_receive` just passes bytes up.
- `Address` is yours: whatever you need to reach a peer again. The node
  stores it with the peer and hands it back to `send`.
- Register it in `transports/__init__.py` (`make_transport`) for a short
  name, or use `"transport": {"type": "my_pkg.link:MyLink"}` in a profile.

The BLE transport (`transports/ble.py`) is the worked example of a
non-broadcast medium: beacon in the advertisement's local name, envelopes
chunked (`stream, index, total`) into a GATT inbox characteristic, one lazily
opened link per peer, `broadcast` = write to everyone heard recently. Its
radio stack is behind `BleBackend` (`start_server`, `advertise`,
`start_scan`, `open`, `stop`); `tests/test_ble.py` runs the whole protocol
over an in-memory backend, which is how to test a transport without
hardware. Run it for real with `pip install -e ".[ble]"` and
`--transport bluetooth` (macOS: allow Bluetooth for your terminal when
asked; Linux: BlueZ ≥ 5.50, `bluetoothd` running, `ControllerMode = le` in
`/etc/bluetooth/main.conf`; both: forget any old pairing between the two
machines). Two things the first real run taught: the GATT service must be
registered once per process (CoreBluetooth adds a second copy on a server
restart, and peers then find two characteristics), and connects to one
peer must be serialised (BlueZ answers `br-connection-busy` otherwise).

## Add an extension

Extensions own envelope types. Register a handler; the node calls it for
envelopes of that type from handshaken peers, signature already verified:

```python
from loquor.protocol import Node, Type

class Ping:
    TYPE = 16                                    # 16+ is reserved for new types

    def __init__(self, node: Node):
        self.node = node
        node.register(self.TYPE, self._on_ping)  # also lists 16 in the card's extensions

    async def ping(self, peer):
        await self.node.send_type(self.TYPE, b"ping", peer)

    async def _on_ping(self, env, peer, address):
        ...
```

Bodies of extension types are plaintext. If an extension needs secrecy,
put its payload inside a MSG body instead (`node.send(peer, {...})`).
Other hooks: `node.on_peer(handler)` (after each handshake) and
`node.on_overheard(handler)` (envelopes addressed to someone else).

## Add a reasoner

```python
from loquor import Reasoner

class MyLLM(Reasoner):
    name = "mine"
    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str: ...
```

`schema` asks for JSON matching a JSON schema; return plain text if you
can't enforce it — callers validate. Profile: `"reasoner": {"type": "my.llm:MyLLM"}`.
Before writing one, check whether the server speaks the OpenAI API: the
built-in `openai` reasoner covers llama.cpp, LM Studio, vLLM and LocalAI.

## Logging and tracing

Two loggers:

- `loquor` — the agent's story: started, met, lost, asks/answered,
  asking/answered, `LLM` calls, reasoning, `TRIGGER`, goal. This is what a
  user sees by default; write new lines for a reader who does not know the
  protocol.
- `loquor.wire` — one line per envelope (`SEND`/`RECV`/`DROP` + type, ids,
  size) and one per decrypted body (`BODY request/response`). Off by
  default; `--wire no-handshake` / `all` or `loquor config set wire …`.
  Beacons and duplicates at DEBUG (`-v`).

`TracingReasoner` (`reasoning/trace.py`) wraps the reasoner when tracing
is on and writes every call to `~/.loquor/run/<name>.trace.jsonl`; the
agent's `StatusWriter` (`tools/status.py`) dumps a snapshot every two
seconds. If you add state a user would want to see — a new skill's
progress, a new hub — add it to `snapshot()` and `format_status()`.

## Conventions

- Python 3.10+, `from __future__ import annotations`, type hints on public
  functions, docstrings explain *why*.
- The protocol never imports a transport or a reasoner. The agent never
  parses envelopes. Tests enforce the first; reviews the second.
- A change to what goes on the wire updates `PROTOCOL.md` in the same PR.
- Commits: one section or one behaviour each; CI (GitHub Actions) runs the
  tests on Python 3.10 and 3.12.

## Files on disk

| path | what |
|---|---|
| `~/.loquor/config.json` | device config: transport, LLM backend and model, beacon interval, logging (`settings.py`) |
| `~/.loquor/keys/<name>.key` | 32-byte Ed25519 seed, mode 0600, created on first run |
| `~/.loquor/pinned_keys.json` | `{agent_id_hex: public_key_hex}` — level-1 trust |
| `~/.loquor/run/<name>.json` | a running agent's status snapshot; removed on a clean stop |
| `~/.loquor/run/<name>.trace.jsonl` | the LLM calls of its last run |
| `<profile dir>/*.py` | sensors, triggers and skills referenced as `"file.py:Class"` |
| `$LOQUOR_HOME` | overrides `~/.loquor` (tests use a temp dir) |
