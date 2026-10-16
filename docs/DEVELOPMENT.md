# Development

How to set up, test, and extend loquor. Design rationale lives in
[ARCHITECTURE.md](ARCHITECTURE.md); the wire format in [PROTOCOL.md](PROTOCOL.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # the package, editable, plus pytest
python -m pytest                 # ~3 s, no model, no network
```

Running an agent from source needs nothing more; `run_agent.py` puts `src/`
on the path. Ollama is only needed to *run* an agent with an LLM; the tests
use a scripted reasoner.

Dependencies: `aiohttp` (Ollama client), `cbor2` (envelopes), `pynacl`
(Ed25519, X25519, ChaCha20-Poly1305 via libsodium), `jsonschema` (the gate
between an LLM proposal and a trigger).

## Layout and the dependency rule

```
src/loquor/
├── protocol/      AMP v1 core — imports nothing else
├── transports/    implement protocol/transport.py — import protocol only
├── extensions/    optional message types 5-8, relay, trust — import protocol only
├── reasoning/     Reasoner interface + backends — import nothing else
├── sensors/       Sensor contract + built-ins — import nothing else (drivers)
├── triggers/      Trigger contract + built-ins — import nothing else (drivers)
├── agent/         orchestrator, skills, Context — imports everything
├── config.py      profile JSON → objects
└── cli.py
```

`tests/test_architecture.py` greps imports and fails the build if a lower
layer reaches up. If you need something from `agent/` inside `protocol/`,
the design is wrong, not the test.

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
| `test_config.py` | profiles, overrides, `module:Class`, key files |
| `test_transports.py` | memory hub, multicast round-trip, transport selection |
| `test_architecture.py` | the dependency rule |

Patterns worth copying:

- **`MemoryHub`** (`transports/memory.py`) is an in-process transport. Every
  node on the hub hears every broadcast; `hub.taps` lets a test see raw bytes.
- **`beacon_interval=0.05`** makes discovery and pruning fast in tests.
- **`ScriptedReasoner`** (`test_agent.py`) stands in for an LLM: it answers
  decision prompts with JSON and everything else with a fixed string.
- Tests that need real multicast `pytest.skip` when the environment has none
  (some CI runners).

## Profile reference

Every field of an agent profile. Only `name`, `description` and `skills`
are required; `--transport`, `--interface`, `--model` and `--visibility`
on the command line override the profile.

| field | type | meaning |
|---|---|---|
| `name` | str | human-readable name; also the key file name and the log label |
| `description` | str | one sentence other agents' LLMs read to decide whether you can help |
| `system_prompt` | str | default persona for every skill that takes a `system_prompt` |
| `visibility` | `visible` \| `quiet` \| `silent` | beacon and answer policy (PROTOCOL §3); default `visible` |
| `key_file` | path | Ed25519 seed; default `~/.loquor/keys/<name>.key`, created on first run |
| `pinned_keys` | path | level-1 trust store; default `~/.loquor/pinned_keys.json` |
| `relay` | bool | store and forward envelopes for peers out of reach (PROTOCOL §8); default false |
| `transport` | part | `localhost` (default), `wifi` (`interface` optional), or a custom class |
| `reasoner` | part | `ollama` (`model`, `base_url`, `temperature`), `template`, or a custom class |
| `sensors` | [part] | drivers that produce readings; each needs a unique `name` |
| `triggers` | [part] | drivers that perform actions; each needs a unique `name` |
| `skills` | [part] | at least one; built-ins below or custom classes |

A *part* is `{"type": T, ...kwargs}` where `T` is a built-in name,
`"module:Class"` (installed module) or `"file.py:Class"` (path relative to
the profile). The kwargs are the constructor's; unknown ones fail at
startup with the constructor's error. Sensor and trigger types may be
plain functions.

Built-in parts and their arguments:

| part | `type` | arguments |
|---|---|---|
| skill | `llm_answer` | `capability`, `system_prompt`, `temperature` (0.2), `sensors` ([names]), `max_chars` (3000) |
| skill | `goal` | `goal`, `system_prompt`, `temperature` (0.2), `triggers` ([names]), `min_answers` (1), `settle` (0 s): conclude after this many answers, or this long after the last one |
| skill | `sensor_report` | `sensors` ([names]; default all), `min_trust` (0) |
| sensor | `simulated` | `name`, `min`, `max`, `unit`, `step`, `interval`, `description`, `start` |
| sensor | `command` | `name`, `command`, `unit`, `interval`, `description`, `timeout` (10) |
| trigger | `console` | `name` (`print`), `description`, `input_schema` (`{text}`), `template` |
| trigger | `command` | `name`, `command`, `description`, `input_schema`, `timeout` (30) |
| reasoner | `ollama` | `model` (`qwen2.5:3b` or `$LOQUOR_MODEL`), `base_url`, `temperature` |
| reasoner | `template` | — |
| transport | `localhost`, `wifi` | `interface` (wifi), `port` (47600) |

## Add a skill

A skill is one class. It can answer requests (`handle`), run a loop
(`run`), or both.

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

What `ctx` offers: `reasoner.complete(system, prompt, temperature=, schema=)`,
`peers(capability=None)`, `ask(peer, capability, input, timeout=)`,
`query(capability)` (QUERY/OFFER), `wait_for_peers()`, `log()`,
`sensors` (a `SensorHub`: `readings()`, `latest()`, `read()`, `describe()`)
and `triggers` (a `TriggerHub`: `propose()`, `fire()`).
`ask` raises `RequestError` (the peer answered an error) or `TimeoutError`.
A skill lists the sensor and trigger names it uses in `self.sensors` /
`self.triggers`; the agent refuses to start if the profile lacks one.

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
    description = "Outdoor temperature from the DS18B20 on the UART."
    unit = "°C"

    def __init__(self, name="thermometer", port="/dev/ttyUSB0", baud=9600, interval=10):
        self.name, self.interval = name, interval
        self.port = serial.Serial(port, baud, timeout=2)

    async def read(self):
        line = await asyncio.to_thread(self.port.readline)      # blocking I/O off the loop
        try:
            return self.reading(float(line))
        except ValueError:
            return self.failed(f"bad frame {line!r}")           # never raise out of read()
```

Profile: `{"type": "drivers/uart_thermometer.py:UartThermometer", "port": "/dev/ttyAMA0"}`
(a path relative to the profile), or `"my_pkg.drivers:UartThermometer"` for
an installed module. Constructor kwargs come from the profile.

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

Skills consume it by name: `{"type": "llm_answer", ..., "sensors": ["thermometer"]}`
puts `thermometer: 12.4 °C (3 s ago)` at `{sensors}` in the prompt; the
`sensor_report` skill exposes it to peers as structured data.

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

    def __init__(self, port="/dev/ttyUSB1"):
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

Skills use it by name. The `goal` skill, with `"triggers": ["pump"]`, asks
the LLM for `{trigger, input, reason}` after reaching its conclusion and
fires only if the input validates. Your own skill does the same in two lines:

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

BLE sketch: `broadcast` = write to every connected central/peripheral;
`send` = write to one; beacon = advertisement payload; chunk envelopes to
the MTU with a 1-byte sequence prefix and reassemble before `on_receive`.

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

## Logging

Two loggers:

- `loquor` — the agent's own story: started, met, lost, reasoning, goal.
- `loquor.wire` — one line per envelope (`SEND`/`RECV`/`DROP` + type, ids,
  size) and one per decrypted body (`BODY request/response`). Beacons and
  duplicates at DEBUG.

`--wire no-handshake` filters HELLO/HELLO_ACK/ACK; `--wire off` silences
the wire logger; `-v` sets DEBUG.

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
| `~/.loquor/keys/<name>.key` | 32-byte Ed25519 seed, mode 0600, created on first run |
| `~/.loquor/pinned_keys.json` | `{agent_id_hex: public_key_hex}` — level-1 trust |
| `<profile dir>/*.py` | sensors, triggers and skills referenced as `"file.py:Class"` |
| `$LOQUOR_HOME` | overrides `~/.loquor` (tests use a temp dir) |
