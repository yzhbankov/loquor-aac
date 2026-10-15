# Loquor protocol — `loquor/1`

A protocol for independent agents that share a local channel. There is no
server and no prior configuration: agents learn about each other only from
what they hear. The protocol is the same on every transport; a transport only
moves bytes (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## 1. The agent

An agent is one process with:

| | |
|---|---|
| **uid** | unique id, generated at start: `<slug-of-name>-<8 hex>` (e.g. `weather-forecaster-1e0c7f69`) |
| **profile** | JSON file defining what the agent is and which skills it runs (§5) |
| **skills** | the capabilities it answers for, and the loops it runs |
| **directory** | the agents it has heard recently (uid → name, description, capabilities, address) |

Every agent runs the same rules (§3); only the profile differs.

## 2. Messages

All messages are JSON objects with the same envelope:

| field | type | meaning |
|---|---|---|
| `protocol` | string | `"loquor/1"` |
| `type` | string | `advert`, `request`, `response` or `error` |
| `msg_id` | string | unique per message (used to drop duplicate deliveries) |
| `sender` | string | uid of the sender |
| `sender_name` | string | human-readable name of the sender |
| `recipient` | string \| null | uid of the addressee; `null` for adverts (everyone) |
| `payload` | object | depends on `type` (below) |
| `sent_at` | number | Unix time, seconds |
| `signature` | string | HMAC-SHA256 (hex) over the canonical JSON of all other fields |

Canonical JSON = keys sorted, separators `,` and `:`, UTF-8.
The HMAC key is the pairing secret shared by the agents (`LOQUOR_PAIRING_SECRET`).
A message is at most **4096 bytes** encoded, on every transport.

| type | recipient | payload |
|---|---|---|
| `advert` | `null` | `{"name", "description", "capabilities": ["tag", ...]}` |
| `request` | target uid | `{"request_id", "capability", "input": {...}}` |
| `response` | requester uid | `{"request_id", "output": {...}}` |
| `error` | requester uid | `{"request_id", "code", "detail"}` |

`input` and `output` are structured objects defined by the capability. The
built-in `llm_answer` skill uses `{"question": str}` → `{"answer": str}`.

Error codes: `no_such_capability` (the agent has no skill for it),
`skill_failed` (the skill raised or returned something that is not an object),
`too_large` (the response did not fit the size cap).

Example — the clothing agent asks the forecaster:

```json
{
  "protocol": "loquor/1",
  "type": "request",
  "msg_id": "6f1c…",
  "sender": "clothing-advisor-fb444c02",
  "sender_name": "Clothing Advisor",
  "recipient": "weather-forecaster-1e0c7f69",
  "payload": {
    "request_id": "a93e…",
    "capability": "weather_forecast",
    "input": {"question": "What is the weather forecast for tomorrow?"}
  },
  "sent_at": 1760565491.2,
  "signature": "4be0…"
}
```

## 3. Rules every agent follows

1. **Advertise.** Send an `advert` at start and then every 10 s (configurable
   with `--advert-interval`). `capabilities` are the capabilities of its skills.
2. **Listen to everything.** Drop a message if it is not valid JSON of this
   format, its `protocol` differs, its signature does not verify, its
   `sender` is this agent, or its `msg_id` was already seen.
3. **Track peers.** On an `advert`, add or refresh the sender in the
   directory, with the transport address it came from. Remove a peer after
   3 missed advert intervals (30 s by default).
4. **Act only on what is addressed to you.** A `request`, `response` or
   `error` whose `recipient` is another uid is ignored (overheard).
5. **Answer requests.** On a `request` for this uid, dispatch `input` to the
   skill whose capability matches; send its `output` as a `response` with the
   same `request_id`. No such skill → `error{no_such_capability}`; the skill
   fails → `error{skill_failed}`.
6. **Idempotency.** If a `request_id` from the same sender arrives again,
   resend the cached reply — never run the skill again, never send a
   different answer. Ignore it while the first copy is still being answered.
7. **Ask with retries.** A request without a response is resent with the same
   `request_id` (3 attempts within the request timeout, default 30 s). An
   `error` ends the wait immediately.

Only the content of answers and decisions comes from skills (and their LLM).
Message format, addressing, retries, deduplication and peer tracking are fixed
code — a model cannot break the protocol.

## 4. Transports

The protocol needs two things from a transport: a way to reach everyone in
range (`broadcast`, for adverts) and a way to reach one peer (`send`, for the
rest). Agents on different transports do not hear each other.

| transport | status | channel | reach |
|---|---|---|---|
| `localhost` | implemented | UDP multicast `239.255.76.76:47600` on loopback | processes on one machine; no network needed |
| `wifi` | implemented, two-laptop run pending | same multicast group on the LAN interface, TTL 1 | machines on one Wi-Fi, hotspot or wired LAN |
| `bluetooth` | planned | BLE connections, chunked to the MTU | devices in radio range, no Wi-Fi |

**Wi-Fi notes.** Some networks block multicast between clients ("client
isolation": guest, corporate and some public Wi-Fi). For demos, use a hotspot
or a home router. Both machines need the same `LOQUOR_PAIRING_SECRET`.

## 5. Profile

```json
{
  "name": "Clothing Advisor",
  "description": "I recommend what clothes to wear based on the temperature and weather.",
  "system_prompt": "You are the Clothing Advisor agent …",
  "reasoner": { "type": "ollama", "model": "qwen2.5:3b" },
  "skills": [
    { "type": "llm_answer", "capability": "clothing_recommendation" },
    { "type": "goal", "goal": "Recommend what to wear tomorrow …" }
  ]
}
```

| field | required | meaning |
|---|---|---|
| `name` | ✅ | human-readable name; also the uid prefix |
| `description` | ✅ | one sentence other agents read to decide whether you can help them |
| `skills` | ✅ | list of `{"type", ...kwargs}`; `type` is `llm_answer`, `goal` or `"module:Class"` |
| `system_prompt` | | default for every skill that takes one |
| `reasoner` | | `{"type": "ollama", "model": ...}` (default) or `{"type": "template"}` |
| `transport` | | `{"type": "localhost"}` (default) or `{"type": "wifi", "interface": ...}` |

Built-in skills:

| skill | arguments | behaviour |
|---|---|---|
| `llm_answer` | `capability`, `system_prompt`, `temperature` (0.2) | answers `{"question"}` with the reasoner |
| `goal` | `goal`, `system_prompt`, `temperature` (0.2) | finds a peer that can help, asks it, completes the goal once |

`{random_number}` in a `system_prompt` is replaced with a fresh 0–100 integer
on every request. LLMs are poor random generators, so use it when answers
must vary.

## 6. Security (PoC level)

- All agents share one pairing secret. Anyone who has it can join and
  impersonate any uid.
- There is no replay protection beyond `msg_id` deduplication within one
  run, and no encryption: messages are readable on the network.
- Next steps: per-agent keypairs pinned during pairing, encrypted payloads.
