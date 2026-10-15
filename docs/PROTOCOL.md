# Loquor broadcast protocol — `loquor-broadcast/0.1`

A protocol for independent agents that share one local channel. There is no
server and no prior configuration: agents learn about each other only from
what they hear.

## 1. The agent

An agent is one process with:

| | |
|---|---|
| **uid** | unique id, generated at start: `<slug-of-name>-<8 hex>` (e.g. `weather-forecaster-1e0c7f69`) |
| **profile** | JSON file defining what the agent is and how it behaves (§5) |
| **reasoner** | a local LLM served by Ollama driven by the profile's `system_prompt` |
| **directory** | the agents it has heard recently (uid → name, description, capabilities) |

Every agent runs the same rules (§3); only the profile differs.

## 2. Messages

All messages are JSON objects with the same envelope:

| field | type | meaning |
|---|---|---|
| `protocol` | string | `"loquor-broadcast/0.1"` |
| `type` | string | `advert`, `request` or `response` |
| `msg_id` | string | unique per message (used to drop duplicate deliveries) |
| `sender` | string | uid of the sender |
| `sender_name` | string | human-readable name of the sender |
| `recipient` | string \| null | uid of the addressee; `null` for adverts (everyone) |
| `payload` | object | depends on `type` (below) |
| `sent_at` | number | Unix time, seconds |
| `signature` | string | HMAC-SHA256 (hex) over the canonical JSON of all other fields |

Canonical JSON = keys sorted, separators `,` and `:`, UTF-8.
The HMAC key is the pairing secret shared by the agents (`LOQUOR_PAIRING_SECRET`).

| type | recipient | payload |
|---|---|---|
| `advert` | `null` | `{"name", "description", "capabilities": [..]}` |
| `request` | target uid | `{"request_id", "question"}` |
| `response` | requester uid | `{"request_id", "answer"}` |

Example — the clothing agent asks the forecaster:

```json
{
  "protocol": "loquor-broadcast/0.1",
  "type": "request",
  "msg_id": "6f1c…",
  "sender": "clothing-advisor-fb444c02",
  "sender_name": "Clothing Advisor",
  "recipient": "weather-forecaster-1e0c7f69",
  "payload": {"request_id": "a93e…", "question": "What is the weather forecast for tomorrow?"},
  "sent_at": 1760565491.2,
  "signature": "4be0…"
}
```

## 3. Rules every agent follows

1. **Advertise.** Send an `advert` at start and then every 10 s (configurable with `--advert-interval`).
2. **Listen to everything.** Drop a message if it is not valid JSON of this
   format, its signature does not verify, its `sender` is this agent, or its
   `msg_id` was already seen.
3. **Track peers.** On an `advert`, add or refresh the sender in the
   directory. Remove a peer after 3 missed advert intervals (30 s by default).
4. **Act only on what is addressed to you.** A `request` or `response` whose
   `recipient` is another uid is ignored (overheard).
5. **Answer requests.** On a `request` for this uid, the reasoner answers the
   `question` using the profile's `system_prompt`; send a `response` to the
   sender with the same `request_id`.
6. **Idempotency.** If a `request_id` from the same sender arrives again,
   resend the cached response — never reason again, never send a different
   answer. Ignore it while the first copy is still being answered.
7. **Pursue the goal** (only if the profile has one):
   - For each newly heard agent, ask the reasoner once: *can this agent help
     with my goal, and what should I ask it?* (JSON: `useful`, `reason`,
     `question`).
   - If useful, send it a `request`. Without a response, resend the same
     `request_id` (3 attempts within the request timeout, default 30 s).
   - With the answer, the reasoner completes the goal. The goal is done once;
     the agent keeps advertising and answering.
   - If the decision cannot be parsed, or the peer never answers, the peer is
     reconsidered on its next advert.

Only rule 5's answer text, rule 7's decision and the final outcome come from
the LLM. Message format, addressing, retries, deduplication and state are
fixed code — the model cannot break the protocol.

## 4. Transports

The protocol needs one thing from a transport: **a shared channel where every
agent hears every message.** Agents on different transports do not hear each
other.

| transport | status | channel | reach |
|---|---|---|---|
| `localhost` | ✅ implemented | UDP multicast `239.255.76.76:47600` on the loopback interface | processes on one machine; works with no network at all |
| `wifi` | ✅ implemented, to be tested on two laptops | same multicast group on the LAN interface, TTL 1 | machines on one Wi-Fi network or hotspot; never leaves the LAN |
| `bluetooth` | planned | BLE (see below) | devices within radio range, no Wi-Fi needed |

Messages must fit in one datagram (limit 16 KB; keep answers short).

**Wi-Fi notes.** Some networks block multicast between clients ("client
isolation": guest, corporate and some public Wi-Fi). For demos, use a
hotspot from one of the laptops or a home router. Both machines need the
same `LOQUOR_PAIRING_SECRET`.

**Bluetooth (BLE) plan.** BLE isn't a single broadcast channel, so the
adapter will split the roles:

- `advert` → BLE advertisement packets. These are tiny (31 bytes legacy,
  about 255 with extended advertising), so they will carry only the uid and
  capability tags. The full advert (description) is read on demand from a
  GATT characteristic.
- `request` / `response` → GATT writes and notifications to the agent whose
  uid is advertised. Addressing by uid stays the same.
- Libraries: `bleak` (client/central) plus a peripheral implementation (e.g.
  `bless`), because each agent must be both.

The agent code (§3) stays unchanged; only the transport adapter changes.
Keeping `capabilities` short tags with a one-line `description` makes that
switch painless.

## 5. Profile

```json
{
  "name": "Weather Forecaster",
  "description": "I provide weather forecasts: temperature and conditions for today or tomorrow.",
  "capabilities": ["weather_forecast"],
  "system_prompt": "You are the Weather Forecaster agent… random number: {random_number} …",
  "goal": null,
  "temperature": 1.0,
  "model": "qwen2.5:3b"
}
```

| field | required | meaning |
|---|---|---|
| `name` | ✅ | human-readable name; also the uid prefix |
| `description` | ✅ | one sentence other agents read to decide whether you can help them |
| `capabilities` | ✅ | short tags, e.g. `weather_forecast` |
| `system_prompt` | ✅ | knowledge and behaviour; guides answers and decisions |
| `goal` | | what this agent tries to achieve; `null` = only advertise and answer |
| `temperature` | | LLM temperature for answers (default 0.2; decisions always use 0) |
| `model` | | Ollama model name, e.g. `qwen2.5:3b` (default `qwen2.5:1.5b`, or `$LOQUOR_MODEL`) |

`{random_number}` in `system_prompt` is replaced with a fresh 0–100 integer on
every request. LLMs are poor random generators, so use it when answers must
vary.

## 6. Security (PoC level)

- All agents share one pairing secret. Anyone who has it can join and
  impersonate any uid.
- There is no replay protection beyond `msg_id` deduplication within one
  run, and no encryption: messages are readable on the network.
- Next steps: per-agent keypairs that sign adverts, pinned during pairing,
  and encrypted payloads.
