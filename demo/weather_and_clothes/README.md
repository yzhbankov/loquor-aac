# Demo: Weather & Clothes — a node and a one-shot goal

A **Weather Station** measures the weather and has no LLM: a node. A
**Clothing Advisor** has a brain with a goal — recommend what to wear —
and no weather sensor. It meets the station, reads its sensor once,
decides, and shows an outfit card.

**Real:** both devices' software, the protocol (keys, handshake,
encryption), the reading travelling as data, the LLM decision, the
schema check on the card before it is shown. **Emulated:** the sensor
invents weather instead of reading a thermometer; the trigger prints a
card instead of driving a display.

| file | what it is |
|---|---|
| `weather_station.json` | the **node**: one sensor, no brain |
| `weather_sensor.py` | the sensor: a class with `name`, `description` and `read()` — a stand-in for a real thermometer |
| `clothing_agent.json` | the **agent**: one trigger and a `brain` with a goal, `"act": ["show_outfit"]`, no `every` (once) |
| `clothing_prompt.txt` | how the advisor's LLM behaves |
| `fancy_print.py` | the trigger: prints the outfit card — a stand-in for a display; its `input_schema` is what the LLM must fill |

```mermaid
sequenceDiagram
    participant W as Weather Station (node)
    participant C as Clothing Advisor (brain)
    participant T as show_outfit (trigger)
    W-->>C: beacon · HELLO — card: sensor weather
    Note over C: mirrors weather-station:weather; a peer is in range → the goal can run
    C->>W: sensor_report {}
    W->>C: {readings: {weather: {temperature_c, condition, wind_kph}}}
    Note over C: LLM concludes in prose, then proposes show_outfit {forecast, items, note}
    C->>T: fire(input) — after the schema check
```

## 1. Setup (once per device)

In the repo root, `./install.sh` on the advisor's device (Ollama and a
model), `./install.sh --node` on the station's (details in
[docs/INSTALL.md](../../docs/INSTALL.md)). Then `source
.venv/bin/activate` and `loquor doctor` says `Ready`.

## 2. Run on one computer

Two terminals, both in the repo root with the venv active:

```bash
loquor run demo/weather_and_clothes/weather_station.json    # terminal 1
loquor run demo/weather_and_clothes/clothing_agent.json     # terminal 2
```

Start them in any order. Stop with Ctrl-C.

### What you will see

Terminal 2 (the advisor), a real run with `qwen2.5:3b` — seven seconds
end to end:

```
02:25:41  clothing-advisor-b3c0180e met weather-station-7045418d: I am a weather station. I measure the weather outside: temperature, condition and wind. ['sensor_report']
02:25:41  clothing-advisor-b3c0180e: weather-station-7045418d shares weather-station:weather
02:25:41  clothing-advisor-b3c0180e: asking weather-station-7045418d for sensor_report: (no input)
02:25:41  clothing-advisor-b3c0180e: weather-station-7045418d answered: readings weather=temperature_c=9, condition=windy and cold, wind_kph=30 (0 s ago)
02:25:45  LLM  3.7 s  "Your goal: Recommend what to wear outside today, from the weather the…" -> "Today's weather measures a temperature of 9°C with windy and cold con…"
02:25:45  clothing-advisor-b3c0180e: concluded: Today's weather measures a temperature of 9°C with windy and cold conditions, wind speeds up to 30 kph. For your outfit, I recommend wearing a wind-resistant jacket, a thermal base layer, …
02:25:48  LLM  2.6 s  "Your goal: Recommend what to wear outside today, from the weather the…" -> "{ \"reason\": \"The weather is 9°C with windy and cold conditions, requi…"
02:25:48  clothing-advisor-b3c0180e: round 1: firing show_outfit — The weather is 9°C with windy and cold conditions, requiring appropriate outdoor clothing to stay warm and protected from the wind.

╭────────────────────────────────────────────────────────╮
│ 👕  OUTFIT FOR TODAY                                    │
│                                                        │
│ 🌤   9°C windy and cold, wind speeds up to 30 kph       │
│                                                        │
│   •  wind-resistant jacket                             │
│   •  thermal base layer                                │
│   •  insulated gloves                                  │
│   •  hat                                               │
│                                                        │
│ 💡  Remember to protect your head from the cold wind.   │
╰────────────────────────────────────────────────────────╯
```

How to read it:

| line | meaning |
|---|---|
| `met …` | the handshake is done: keys and capability cards exchanged, shared key derived |
| `… shares weather-station:weather` | the station's sensor is now usable on the advisor, under that name — not read yet |
| `asking … for sensor_report` / `… answered: readings …` | the brain needed the weather: one encrypted request, the reading as data |
| `LLM  3.7 s  … -> …` | one model call: how long, what was asked, what came back (`loquor trace "Clothing Advisor" --full` has every word) |
| `concluded: …` | a goal without `every` first concludes in prose… |
| `round 1: firing show_outfit — …` | …then proposes a trigger from the conclusion; it passed the schema check, and the card follows |

The station's terminal shows the mirror image: `clothing-advisor-… asks
for sensor_report`, and what it answered. It never calls an LLM.

From a third terminal, while they run:

```bash
loquor status                        # both: peers, readings, the goal and what it fired
loquor ask "Weather Station"         # the station's reading, from the command line
loquor ask "Clothing Advisor" "What should I wear?"   # the advisor's brain answers too, reading the station now
```

Things to try:

- Restart the advisor: a new reading, different clothes, same ids.
- Stop the station: about 30 s later the advisor logs `lost
  weather-station-…`, and its sensor is gone from `loquor status`.
- Swap the sensor for a real one: a `file`, `command` or `http` sensor
  in `weather_station.json` ([docs/HARDWARE.md](../../docs/HARDWARE.md)),
  or edit `read()` in `weather_sensor.py`.
- Make the advisor a loop: `"every": 600` in its brain — a new card every
  ten minutes, the station read each time.

## 3. Run on two devices

Two laptops are the easy stand-in; any two devices that run Python and a
local model behave the same. Both on the same Wi-Fi or one device's hotspot
(no internet needed), each set up as in step 1:

```bash
loquor run demo/weather_and_clothes/weather_station.json --transport wifi     # device A
loquor run demo/weather_and_clothes/clothing_agent.json --transport wifi    # device B
```

or set it once per device with `loquor config set transport wifi` and
drop the flag. The startup line should show `transport wifi (192.168.x.x)`
— the device's LAN address, not `127.0.0.1`. Nothing to pair: each device
finds the other by its beacons, exchanges keys in the handshake and
encrypts from there.

If macOS asks "allow incoming network connections?", click Allow.

### Over Bluetooth — no network at all

Both devices need Bluetooth and the radio libraries (`install.sh` puts
them in; `loquor doctor --transport bluetooth` confirms), then the same
commands with `--transport bluetooth`:

```bash
loquor run demo/weather_and_clothes/weather_station.json --transport bluetooth     # device A
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

Verified between a Mac and an Ubuntu laptop (with the earlier version of
this demo, where the station was an agent too): beacons both ways,
handshake, the encrypted request and reply, the goal complete — about
two minutes end to end, most of it the LLMs. What is normal in the log:
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
| `--transport localhost\|wifi\|bluetooth` | override the device config; both devices must match |
| `--interface <ip>` | `wifi`: choose the network interface |
| `--model llama3.1:8b` | the advisor: any model from `ollama list` |
| `--visibility visible\|quiet\|silent` | `quiet`: never beacon but answer; `silent`: only initiate |
| `--beacon-interval 2` | faster discovery for demos (default 10 s) |
| `--wire all\|no-handshake\|off`, `-v` | how much of the wire to log (default off) |
| `--no-trace` | do not record LLM calls to `~/.loquor/run/` |

Each of these can be made the device's default with `loquor config set`.

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `model … not pulled` / `ollama: not reachable` | start Ollama, then `ollama pull <model>` |
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
| the card ignores the weather | `loquor trace "Clothing Advisor" --full` shows the prompt with the reading in it; a small model sometimes ignores it — rerun, or a larger model |
| `peer exposes no loquor inbox` (macOS, first attempt) | stale GATT cache; the next attempt refreshes it |
| `lost …` a while after the exchange | the link dropped and the peer stopped advertising; it is found again on its next beacon |
| two devices silent, same Wi-Fi | guest/corporate Wi-Fi isolates clients and drops multicast — use a hotspot or home router |
| `DROP … bad signature` | a peer's key changed (it deleted `~/.loquor/keys/`); it will re-handshake on its next beacon |
| slow answers | a 3B model answers in 1–5 s per request on a laptop; the station adds nothing, it has no LLM |
