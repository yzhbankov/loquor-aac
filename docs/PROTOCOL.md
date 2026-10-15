# AMP v1 — Agent Mesh Protocol, as implemented

This document describes exactly what `src/loquor/protocol/` implements.
Sections marked **(extension)** are optional; **(stub)** means the interface
exists and the behaviour is minimal. Nothing here depends on a particular
radio: a transport only carries bytes (§10).

## 1. Identity — `identity.py`

- Each agent has an **Ed25519 keypair**, generated once and persisted as a
  32-byte seed file (mode 0600), by default `~/.loquor/keys/<name>.key`.
- `agent_id` = first 16 bytes of SHA-256(public key).
- All addressing uses `agent_id`. Transport addresses (IP tuples, BLE
  handles) are hints for where to send bytes, never identity.
- The same key, converted to X25519, is used for pairwise encryption (§5).

## 2. Envelope — `envelope.py`

The only mandatory wire unit. A CBOR array in fixed order:

```
[ver, type, msg_id, from, to, ttl, ts, body, sig]
```

| field | type | meaning |
|---|---|---|
| `ver` | uint | `1` |
| `type` | uint | message type (§4); unknown values decode and are ignored |
| `msg_id` | 16 bytes | random; receivers drop repeats (LRU of 4096) |
| `from` | 16 bytes | sender `agent_id` |
| `to` | 16 bytes | recipient `agent_id`, or 16 zero bytes = broadcast |
| `ttl` | uint | hop budget; a relay decrements it and drops at 0 (default 4) |
| `ts` | uint | unix seconds |
| `body` | bytes | plaintext for HELLO/HELLO_ACK/ACK and extension types; encrypted for MSG |
| `sig` | 64 bytes | Ed25519 over CBOR `[ver, type, msg_id, from, to, ts, body]` |

Rules:

- **Maximum encoded size 64 KB**; larger envelopes are neither sent nor decoded.
- **`ttl` is not signed.** The spec asks both that relays decrement `ttl` and
  that the signature cover all prior fields; a relay cannot re-sign as the
  origin, so `ttl` is the one field left out. Every other field is covered.
- A receiver drops: its own envelopes, repeats, envelopes addressed to another
  id, invalid signatures, and anything but HELLO/HELLO_ACK from a peer it has
  not handshaken with.
- Envelopes are connectionless; no link-level session is required to receive one.

## 3. Discovery — `discovery.py`

**Beacon** — 16 bytes, sized for a BLE advertisement, sent by *visible*
agents every `beacon_interval` (default 10 s):

```
"AM" | ver=1 | agent_id[:8] | bloom (4 bytes) | seq (1 byte)
```

- `bloom`: 32-bit Bloom filter of the agent's capability ids, k = 2, bits
  `SHA-256(id)[0] % 32` and `SHA-256(id)[1] % 32`. ~5 % false positives at
  4 capabilities, ~15 % at 8. A hit means "ask"; HELLO is authoritative.
- `seq`: the capability document's version; bumps on every change. A peer
  that sees a new `seq` sends HELLO again to refresh the card.

A beacon is not signed and carries no name; it only says "someone with this
id prefix may offer these capabilities". On hearing an unknown prefix a node
broadcasts HELLO (at most once per `beacon_interval` per prefix).

**Visibility** (per agent, same protocol for all):

| policy | beacons | answers QUERY | answers unsolicited HELLO | initiates |
|---|---|---|---|---|
| `visible` | yes | yes | yes | yes |
| `quiet` | no | yes | yes | yes |
| `silent` | no | no | no | yes |

A node forgets a peer after 3 beacon intervals without hearing it (beacon,
HELLO or any valid envelope), and discards the pair key.

## 4. Message types — `node.py`

| type | name | core | body |
|---|---|---|---|
| 1 | `HELLO` | ✅ | CBOR `{pk: 32B, card: {...}, seq: uint, ext: [uint]}` — plaintext |
| 2 | `HELLO_ACK` | ✅ | same + `chosen: [uint]` (extension intersection) — plaintext |
| 3 | `MSG` | ✅ | application payload, encrypted (§5) |
| 4 | `ACK` | ✅ | the acknowledged `msg_id` (16 bytes) — plaintext, unicast |
| 5 | `QUERY` | extension | CBOR `{capability: str, nonce: 8B}` — broadcast, plaintext |
| 6 | `OFFER` | extension | CBOR `{capability: str, nonce: 8B}` — unicast reply, plaintext |
| 7 | `SESSION_REQ` | extension | not yet implemented |
| 8 | `SESSION_ACK` | extension | not yet implemented |
| 16+ | reserved | | decoded, ignored |

Handshake:

1. A receives HELLO from B: checks `SHA-256(pk)[:16] == from`, verifies the
   signature with `pk`, validates the card; stores B at trust level 0; derives
   the pair key; replies HELLO_ACK unicast.
2. B receives HELLO_ACK: same checks; stores A; derives the same key.

Both directions may start at once (both heard each other's beacon); a HELLO
from an already-known peer simply refreshes it and is acknowledged again.

Every unicast MSG is acknowledged with ACK. A sender may wait for the ACK
(`Node.send(..., wait_ack=True)`); the agent layer's request/response retry
does not depend on it.

Unknown types from handshaken peers are ignored with a debug log; the node
keeps working. Extensions register a handler per type (`Node.register`).

**QUERY / OFFER — `extensions/query.py`.** "Who offers capability X?"
A node broadcasts QUERY; every handshaken `visible` or `quiet` node whose
card lists the capability replies OFFER with the same nonce. The asker
collects OFFERs for a timeout (default 1 s) and adds the peers it already
knew to have the capability. The beacon Bloom filter is the hint; this is
the authoritative check.

## 5. Encryption — `crypto.py`

After HELLO/HELLO_ACK:

```
shared = X25519(my_ed25519_key → x25519, their_pk → x25519)
key    = SHA-256("AMP-v1-pair" | shared | min(id_a, id_b) | max(id_a, id_b))
```

Every MSG body between that pair is **ChaCha20-Poly1305 (IETF)**:

```
body = nonce (12 random bytes) | ciphertext + tag
AAD  = CBOR [ver, type, msg_id, from, to]
```

The header is the associated data, so a body cannot be replayed under
another envelope. Relays never decrypt: they read only the header. Keys
live for one run (a lost peer's key is discarded; a restart re-handshakes).

## 6. Capability document — `card.py`

Carried in HELLO as CBOR. A subset of the A2A Agent Card without the
web-only fields (no endpoint URL, no OAuth):

```
{
  "name": str, "description": str, "version": str,
  "skills": [ { "id": str, "name": str, "description": str, "tags": [str],
                "input_schema": {...}, "output_schema": {...}, "min_trust": uint } ],
  "llm":    { "model": str, "context_window": uint, "modalities": [str] },
  "extensions": [uint]
}
```

Empty and default values are omitted. Capabilities are the skill ids; the
beacon Bloom filter is derived from them. The agent builds the card from
its skills (`capability`, `description`, `input_schema`, `output_schema`,
`min_trust`) and its reasoner (`model`).

## 7. Sessions **(extension, not yet implemented)**

SESSION_REQ `{purpose, transport_hint}` / SESSION_ACK `{transport, params}`
negotiated inside envelopes. Core messaging never depends on a session.

## 8. Relay / gossip **(extension, not yet implemented)**

Nodes advertising `relay` store envelopes with `to != self`, bounded queue,
expired by `ttl`/age; on contact, exchange `msg_id` Bloom filters and send
the difference.

## 9. Trust levels — `trust.py`

| level | name | established by |
|---|---|---|
| 0 | `HEARD` | a valid signature (every handshaken peer) |
| 1 | `VERIFIED` | out-of-band confirmation of the public key (e.g. QR) — store interface pending |
| 2 | `PROXIMATE` | UWB distance bounding — interface pending |

A skill declares `min_trust`; it is published in the card and **enforced at
dispatch**: a request from a peer below the level gets
`error{insufficient_trust}`.

## 10. Transports — `transport.py` (port), `transports/`

The protocol needs two operations from a link: `broadcast(bytes)` to everyone
in range and `send(address, bytes)` to one peer. The node stores the address
a peer was last heard from and hands it back; it never interprets it.

| transport | status | channel |
|---|---|---|
| `localhost` | implemented | UDP multicast `239.255.76.76:47600` on loopback |
| `wifi` | implemented | same group on the LAN interface, TTL 1 |
| `memory` | implemented | in-process hub for tests |
| `bluetooth` | planned | BLE connections, chunked to the MTU; beacons as advertisements |

On multicast, `send` is a broadcast (processes on one host share the port);
the envelope's `to` field does the addressing.

## 11. Application bodies (agent layer, not protocol)

What the agent puts inside MSG, encrypted. Documented here because the demo
shows it; the protocol does not interpret it.

```
{"kind": "request",  "request_id": str, "capability": str, "input": {...}}
{"kind": "response", "request_id": str, "output": {...}}
{"kind": "error",    "request_id": str, "code": str, "detail": str}
```

Error codes: `no_such_capability`, `skill_failed`, `insufficient_trust`,
`too_large`. A request is retried with the same `request_id` (3 attempts
within 30 s); the responder caches its reply per `(sender, request_id)`
and never runs a skill twice for one request.
