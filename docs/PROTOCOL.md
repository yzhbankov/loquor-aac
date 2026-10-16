# AMP v1 — Agent Mesh Protocol, as implemented

This document describes exactly what `src/loquor/protocol/` implements.
Sections marked **(extension)** are optional; **(stub)** means the interface
exists and the behaviour is minimal. Nothing here depends on a particular
radio: a transport only carries bytes (§10).

## 0. The protocol in two diagrams

**Life of two agents on one channel.** Beacons are tiny and unsigned; the
handshake carries keys and capability cards; everything after it is
encrypted and acknowledged.

```mermaid
sequenceDiagram
    participant A as Agent A
    participant B as Agent B
    loop every beacon_interval (visible agents)
        A-->>B: beacon "AM" ver id[:8] bloom seq  (16 B, broadcast, unsigned)
    end
    Note over B: unknown id prefix → say hello
    B->>A: HELLO {pk, card, seq, ext}  (broadcast, signed)
    Note over A: SHA-256(pk)[:16] == from? signature ok? → store peer, derive pair key
    A->>B: HELLO_ACK {pk, card, seq, ext, chosen}  (unicast, signed)
    Note over B: same checks → store peer, derive the same pair key
    Note over A,B: trust = 0 (heard); 1 if pk pinned; 2 if UWB-proximate
    A->>B: MSG  body = ChaCha20-Poly1305(pair key, header as AAD)
    B->>A: ACK  body = msg_id
    B->>A: MSG  (reply, encrypted)
    A->>B: ACK
    opt extensions
        A->>B: QUERY {capability, nonce}  (broadcast) → B->>A: OFFER
        A->>B: SESSION_REQ {purpose, hint} → B->>A: SESSION_ACK {transport, params}
    end
    Note over A,B: no beacon/envelope from a peer for 3 intervals → forget it, drop its key
```

**What a receiving node does with every datagram.**

```
bytes arrive
  ├─ 16 B starting "AM"?  ──▶ beacon ──▶ known prefix & same seq → refresh last_seen
  │                                   └─ unknown prefix or new seq → send HELLO (rate-limited)
  └─ CBOR envelope?  (else DROP: not an envelope / unsupported ver / > 64 KB)
       ├─ from == me                 → DROP (own, looped back)
       ├─ msg_id seen before         → DROP (duplicate)
       ├─ to ∉ {me, broadcast}       → overheard: hand to relay (if any), stop
       ├─ type HELLO / HELLO_ACK     → pk hashes to from? sig ok with pk? card valid?
       │                               → store peer, derive key, trust level, reply HELLO_ACK if HELLO
       ├─ sender not handshaken      → DROP (no key to verify)
       ├─ signature invalid          → DROP
       ├─ type MSG                   → decrypt (header is AAD) → ACK → deliver body to the agent
       ├─ type ACK                   → resolve the waiting sender
       ├─ registered extension type  → its handler
       └─ anything else              → ignore (reserved / future)
```

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
| 7 | `SESSION_REQ` | extension | CBOR `{purpose: str, hint: str, nonce: 8B}` — unicast, plaintext |
| 8 | `SESSION_ACK` | extension | CBOR `{nonce: 8B, transport: str, params: {...}}` — unicast, plaintext |
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

## 7. Sessions **(extension)** — `extensions/session.py`

A bulk or stream link is negotiated inside envelopes, WebRTC-style:

1. A sends SESSION_REQ `{purpose, hint, nonce}` (hint: a transport name,
   e.g. `wifi`).
2. B picks the hinted transport if it has one, else any it has, else answers
   `"none"`; it replies SESSION_ACK `{nonce, transport, params}` where
   `params` is whatever A needs to connect.

Session transports (TCP/QUIC on the same network preferred, BLE L2CAP as
fallback) implement the `SessionTransport` interface (`offer(peer) ->
params`). **None is implemented yet**, so every negotiation today ends in
`"none"`: keep using core messaging, which never depends on a session.

## 8. Relay / gossip **(extension, partial)** — `extensions/relay.py`

A node whose card says `relay: true` keeps envelopes it overhears that are
addressed to someone else: `ttl` decremented (dropped when spent), bounded
queue (256), expired by age (10 min). When it meets the addressee
(HELLO/HELLO_ACK) it forwards the stored envelopes unchanged — the origin's
signature still verifies, since `ttl` is not signed, and the relay never
decrypts. The addressee must have handshaken with the origin to verify.
A node records the address a peer's envelope *arrived from*, so a peer
reached through a relay is answered through that relay until it is heard
directly again.

Gossip between relays has its building blocks (`msg_id` Bloom filter of
256 bits, k = 3; `missing(their_bloom)`) but **no wire message yet**; it
will use a type ≥ 16.

## 9. Trust levels — `trust.py`

| level | name | established by |
|---|---|---|
| 0 | `HEARD` | a valid signature (every handshaken peer) |
| 1 | `VERIFIED` | `PinnedKeys`: the public key was confirmed out of band (QR, fingerprint) and pinned in `~/.loquor/pinned_keys.json` |
| 2 | `PROXIMATE` | `ProximityProvider` interface (UWB distance bounding) — **stub, nobody is proximate yet** |

`TrustExtension` sets `peer.trust` on every handshake from the stores it has.

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
| `bluetooth` | implemented, verified macOS ↔ Ubuntu | beacon = advertisement local name (base64url of the 16 bytes); envelopes written to a GATT inbox characteristic, chunked to the MTU with a 6-byte `stream, index, total` header; a peer with a live link counts as heard even when it stops advertising |

On multicast, `send` is a broadcast (processes on one host share the port);
the envelope's `to` field does the addressing.

On BLE every node is both peripheral (advertises its beacon, hosts the inbox)
and central (scans, connects to the peers it hears, writes). A write carries
no sender identity on most stacks, so an envelope that arrives by write has
no address: the node answers at the address it learned from that peer's
beacon (`_heard_at` by id prefix). `broadcast` writes to every peer heard
in the last 60 s and re-advertises a beacon; `send` writes to one link,
opened lazily and reopened after a failure.

## 11. Application bodies (agent layer, not protocol)

What the agent puts inside MSG, encrypted. Documented here because the demo
shows it; the protocol does not interpret it.

```
{"kind": "request",  "request_id": str, "capability": str, "input": {...}}
{"kind": "response", "request_id": str, "output": {...}}
{"kind": "error",    "request_id": str, "code": str, "detail": str}
```

Error codes: `no_such_capability`, `skill_failed`, `insufficient_trust`,
`too_large`. The built-in capability `sensor_report` answers
`{"sensor"?: name}` with `{"readings": {name: {value, unit, at, error?}}}` —
an agent's sensors, as structured data, to any peer allowed to ask. A request is retried with the same `request_id` (3 attempts
within 30 s); the responder caches its reply per `(sender, request_id)`
and never runs a skill twice for one request.
