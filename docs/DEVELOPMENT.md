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

Dependencies, all three: `aiohttp` (Ollama client), `cbor2` (envelopes),
`pynacl` (Ed25519, X25519, ChaCha20-Poly1305 via libsodium).

## Layout and the dependency rule

```
src/loquor/
├── protocol/      AMP v1 core — imports nothing else
├── transports/    implement protocol/transport.py — import protocol only
├── extensions/    optional message types 5-8, relay, trust — import protocol only
├── reasoning/     Reasoner interface + backends — import nothing else
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
| `test_agent.py` | dispatch, errors, trust at dispatch, retries, idempotent answers, multicast end to end |
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
`query(capability)` (QUERY/OFFER), `wait_for_peers()`, `log()`.
`ask` raises `RequestError` (the peer answered an error) or `TimeoutError`.

Use it from a profile:

```json
{ "type": "my_farm.soil:SoilReport", "sensor_id": "north" }
```

Rules: raise to send the requester `error{skill_failed}`; return a dict;
keep responses under ~60 KB (the envelope cap is 64 KB); never block the
event loop (use `await`).

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
| `$LOQUOR_HOME` | overrides `~/.loquor` (tests use a temp dir) |
