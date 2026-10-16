# Demo: Weather & Clothes

Two agents, each with its own local LLM, standing in for two devices. The
**Weather Forecaster** can answer "what's the weather tomorrow?". The
**Clothing Advisor** has a goal — recommend what to wear — but no weather.
It finds the forecaster, asks, and recommends.

**Real:** the agents, the protocol (keys, handshake, encryption), the LLM decisions.
**Emulated:** the hardware — the sensor invents weather instead of reading a
thermometer; the trigger prints a card instead of driving a display.

Laptops are just the quickest hardware to demo on. Think of the two agents
as a robot and a base station, two sensor nodes, or a handheld meeting a
machine: the protocol, the profiles and the code are identical.

The forecaster has a **sensor** (`weather_sensor.py`: invents weather —
stand-in for a real thermometer); the advisor has a **trigger**
(`fancy_print.py`: prints an outfit card — stand-in for a display or a
relay). Both are one small class next to the profile.

```mermaid
sequenceDiagram
    participant S as weather sensor
    participant W as Weather Forecaster
    participant C as Clothing Advisor
    participant T as show_outfit trigger
    Note over W,C: both start; each generates or loads its key
    S-->>W: reading every 5 s {temperature_c, condition, wind_kph}
    W-->>C: beacon (id prefix + Bloom of "weather_forecast")
    C->>W: HELLO (public key, card)
    W->>C: HELLO_ACK (public key, card)
    Note over W,C: both derive the shared key; everything below is encrypted
    Note over C: LLM: "can this peer help my goal?" → yes, ask weather_forecast
    C->>W: MSG request {capability: weather_forecast, input: {question}}
    W->>C: ACK
    Note over W: LLM answers from the latest reading in its prompt
    W->>C: MSG response {output: {answer: "12 °C, cloudy"}}
    C->>W: ACK
    Note over C: LLM turns the forecast into clothes → "=== Goal complete ==="
    Note over C: LLM proposes show_outfit {forecast, items, note}; schema validated
    C->>T: fire(input) → the card is printed
```

## 1. Setup (once per device)

In the repo root, `./install.sh` — Python, the package, Ollama and a
model sized for the device (details in [docs/INSTALL.md](../../docs/INSTALL.md)).
Then `source .venv/bin/activate` and `loquor doctor` says `Ready`.

## 2. Run on one computer

Two terminals, both in the repo root with the venv active:

```bash
loquor run demo/weather_and_clothes/weather_agent.json    # terminal 1
loquor run demo/weather_and_clothes/clothing_agent.json   # terminal 2
```

Start them in any order. Stop with Ctrl-C. `loquor check <profile>`
first, if you want to see the parts and a sensor reading before going on
the air.

### What you will see

Terminal 2 (the advisor), about 20 s end to end on a laptop:

```
clothing-advisor-3d42fca5 | id 3d42fca5… | model qwen2.5:3b (ollama) | transport localhost (127.0.0.1) | visible | Ctrl-C to stop
status: loquor status "Clothing Advisor" · trace: ~/.loquor/run/clothing-advisor.trace.jsonl
12:00:03  clothing-advisor-3d42fca5 met weather-forecaster-6b2a6c20: I am a weather forecaster… ['weather_forecast', 'sensor_report']
12:00:07  LLM  4.1 s  "Another agent on the local network advertised itself: - name: Weather…" -> "{ \"useful\": true, \"reason\": \"The Weather Forecaster can provide…"
12:00:07  clothing-advisor-3d42fca5: reasoning about weather-forecaster-6b2a6c20: useful=True — it can provide the forecast
12:00:07  clothing-advisor-3d42fca5: asking weather-forecaster-6b2a6c20 for weather_forecast: What is the weather forecast for tomorrow?
12:00:09  clothing-advisor-3d42fca5: weather-forecaster-6b2a6c20 answered: Tomorrow will be partly cloudy with a high of 12 degrees Celsius.
12:00:12  LLM  2.6 s  "Your goal: Recommend what to wear tomorrow…" -> "Tomorrow will be partly cloudy at 12 °C: a light sweater, jeans and…"
12:00:12  clothing-advisor-3d42fca5:
=== Goal complete ===
goal      : Recommend what to wear tomorrow…
asked     : Weather Forecaster
outcome   : …I recommend a light sweater, jeans and a light jacket…

12:00:15  LLM  3.0 s  "Your goal: Recommend what to wear tomorrow…" -> "{ \"trigger\": \"show_outfit\", \"input\": { \"forecast\": \"Partly cloudy, 12 °C\"…"
12:00:15  clothing-advisor-3d42fca5: firing show_outfit — the recommendation is final
12:00:15  TRIGGER show_outfit {"forecast": "Partly cloudy, 12 °C", "items": ["light sweater", "jeans", "light jacket"]} -> {"items": 3}
```

followed by the outfit card. How to read it:

| line | meaning |
|---|---|
| `id 3d42fca5…` | this agent's identity, derived from its key in `~/.loquor/keys/`. Same on every run. |
| `met …` | the handshake is done: keys and capability cards exchanged, shared key derived; the peer is known by name, description, capabilities |
| `LLM  4.1 s  … -> …` | one model call: how long, what was asked, what came back (`loquor trace "Clothing Advisor" --full` has every word) |
| `reasoning about …` | the advisor's LLM deciding whether this peer can help its goal |
| `asking … for …` / `… answered:` | the encrypted request and its reply, in the clear on this side |
| `firing show_outfit — …` / `TRIGGER show_outfit {…}` | the LLM's proposal passed schema validation and the trigger ran; the outfit card follows |

Terminal 1 shows the mirror image: `clothing-advisor-… asks for
weather_forecast: …`, its own `LLM` line, `answered clothing-advisor-…`.

From a third terminal, while they run:

```bash
loquor status                          # both agents: peers, readings, LLM calls, goal
loquor trace "Weather Forecaster" --full   # the exact prompt with the reading in it, and the answer
```

To see the protocol itself — HELLO/HELLO_ACK, every encrypted envelope
with its size, the ACKs — add `--wire all` (`--wire no-handshake` skips
the handshake lines); `-v` also shows beacons and every sensor reading.

Things to try:
- Restart the advisor: a new random forecast, different clothes, same ids.
- Ask the forecaster for raw data instead of prose: any skill can
  `ctx.ask(peer, "sensor_report", {"sensor": "weather"})` and get the numbers.
- Swap the sensor for a real one: copy `weather_sensor.py`, read your
  instrument in `read()`, change one line in `weather_agent.json`.
- Stop the forecaster: ~30 s later the advisor logs `lost weather-forecaster-…`.
- Change the forecaster's persona: edit the `system_prompt` in `weather_agent.json`, run again, and compare the answers with `loquor trace`.

## 3. Run on two devices

Two laptops are the easy stand-in; any two devices that run Python and a
local model behave the same. Both on the same Wi-Fi or one device's hotspot
(no internet needed), each set up as in step 1:

```bash
loquor run demo/weather_and_clothes/weather_agent.json --transport wifi     # device A
loquor run demo/weather_and_clothes/clothing_agent.json --transport wifi    # device B
```

or set it once per device with `loquor config set transport wifi` and
drop the flag. The startup line should show `transport wifi (192.168.x.x)`
— the device's LAN address, not `127.0.0.1`. Nothing to pair: each agent
finds the other by its beacons, exchanges keys in the handshake and
encrypts from there.

If macOS asks "allow incoming network connections?", click Allow.

### Over Bluetooth — no network at all

Both devices need Bluetooth and the radio libraries (`install.sh` puts
them in; `loquor doctor --transport bluetooth` confirms), then the same
commands with `--transport bluetooth`:

```bash
loquor run demo/weather_and_clothes/weather_agent.json --transport bluetooth     # device A
loquor run demo/weather_and_clothes/clothing_agent.json --transport bluetooth    # device B
```

The startup line shows `transport bluetooth (ble)`. The beacon rides in the
BLE advertisement as the device's local name; the messages go over a GATT
connection, chunked to the link's MTU. Discovery takes one beacon interval
plus a connection, so allow ~20 s before `met …` appears.

Before the first run, on **both** devices:

- **Forget any old pairing between the two laptops** (macOS: System
  Settings → Bluetooth → the other laptop → Forget; Linux:
  `bluetoothctl remove <the Mac's address>`). A stale bond shows up as
  `Peer removed pairing information` on macOS. loquor never pairs.
- **macOS:** allow Bluetooth for your terminal when asked (System Settings
  → Privacy & Security → Bluetooth).
- **Linux:** BlueZ 5.50+ with `bluetoothd` running, and set the adapter to
  LE only so BlueZ does not try a classic connection to a dual-mode laptop
  (`br-connection-canceled` / `br-connection-unknown` errors):

  ```
  # /etc/bluetooth/main.conf
  [General]
  ControllerMode = le
  ```
  then `sudo systemctl restart bluetooth`.

Verified between a Mac and an Ubuntu laptop: beacons both ways, handshake,
the encrypted request and reply, `=== Goal complete ===` — about two
minutes end to end, most of it the two LLMs. What is normal in the log:
the first connection attempt may fail once (`stale GATT cache` on macOS,
`Not connected` right after `connected (mtu 23)` on Linux) and succeed on
the retry a few seconds later; `next broadcast attempt in N s` is the
back-off doing its job. If it does not work, both logs are the most useful
thing to report; add `-v` only if asked, it is very verbose.

### Fully offline test

1. While online, do step 1 on both devices and run step 2 once on each.
2. Use a router with its uplink unplugged, or an Android hotspot with mobile
   data off (an iPhone hotspot usually needs data on).
3. `curl -m 5 https://example.com` must fail; `ping <other device>` must work.
4. Run step 3.

## 4. Options

| flag | effect |
|---|---|
| `--transport localhost\|wifi\|bluetooth` | override the device config; both agents must match |
| `--interface <ip>` | `wifi`: choose the network interface |
| `--model llama3.1:8b` | any model from `ollama list` |
| `--visibility visible\|quiet\|silent` | `quiet`: never beacon but answer; `silent`: only initiate |
| `--beacon-interval 2` | faster discovery for demos (default 10 s) |
| `--wire all\|no-handshake\|off`, `-v` | how much of the wire to log (default off) |
| `--no-trace` | do not record LLM calls to `~/.loquor/run/` |

Each of these can be made the device's default with `loquor config set`.

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `model ... is not available in Ollama` | start Ollama, then `ollama pull <model>` |
| `ModuleNotFoundError` | activate the venv; `./install.sh` again if in doubt |
| anything else at startup | `loquor doctor` (the device) and `loquor check <profile>` (the agent) print the fix |
| agents never `met` each other | same `--transport` on both; neither `--visibility silent`; wait one beacon interval |
| `no LAN interface found for wifi transport` | join a Wi-Fi network or hotspot, or pass `--interface` |
| `bluetooth transport needs the radio libraries` | `pip install -e ".[ble]"` in the venv, or `./install.sh` |
| Bluetooth: never `met`, no errors | Bluetooth off or not allowed for the terminal (macOS: System Settings → Privacy & Security → Bluetooth); on Linux check `bluetoothctl show` says `Powered: yes` |
| Bluetooth: `met` but requests time out | the GATT connection failed; move the devices closer, restart both agents (they reconnect lazily) |
| `Peer removed pairing information` (macOS) | forget the other laptop in Bluetooth settings on both sides; loquor does not pair |
| `br-connection-canceled` / `br-connection-busy` (Linux) | `./install.sh --ble-le-mode` (sets `ControllerMode = le` and restarts `bluetooth`) |
| `Characteristic … was not found` / `Multiple Characteristics` | the other side runs an older loquor that registered its service twice; update it and restart |
| `connected (mtu 23)` then `Not connected` (Linux) | BlueZ reused the other side's inbound link, which cannot carry our writes; it retries with its own connection within a few seconds |
| the whole exchange takes a minute or more | the `LLM  … s` lines show where the time goes: a CPU-only laptop wants `loquor config set model qwen2.5:1.5b`; `beacon_interval 3` on both finds peers faster |
| the forecaster says it has no weather | `loquor trace "Weather Forecaster" --full` shows the prompt with the reading in it; a small model sometimes ignores it — rerun, or a larger model |
| `peer exposes no loquor inbox` (macOS, first attempt) | stale GATT cache; the next attempt refreshes it |
| `lost …` a while after the exchange | the link dropped and the peer stopped advertising; it is found again on its next beacon |
| two devices silent, same Wi-Fi | guest/corporate Wi-Fi isolates clients and drops multicast — use a hotspot or home router |
| `DROP … bad signature` | a peer's key changed (it deleted `~/.loquor/keys/`); it will re-handshake on its next beacon |
| slow answers | on one computer both agents share one Ollama; a 3B model answers in 1–5 s per request on a laptop |
