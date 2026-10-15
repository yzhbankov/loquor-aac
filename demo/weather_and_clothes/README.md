# Demo: Weather & Clothes

Two agents, each with its own local LLM, standing in for two devices. The
**Weather Forecaster** can answer "what's the weather tomorrow?". The
**Clothing Advisor** has a goal — recommend what to wear — but no weather.
It finds the forecaster, asks, and recommends.

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

Needs Python 3.10+ and [Ollama](https://ollama.com). In the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b          # the model both profiles use, ~2 GB
```

Check: `ollama list` shows `qwen2.5:3b`.

## 2. Run on one computer

Two terminals, both in the repo root with the venv active:

```bash
python run_agent.py demo/weather_and_clothes/weather_agent.json    # terminal 1
python run_agent.py demo/weather_and_clothes/clothing_agent.json   # terminal 2
```

Start them in any order. Stop with Ctrl-C.

### What you will see

Terminal 2 (the advisor), about 15 s end to end on a laptop used for the demo:

```
clothing-advisor-3d42fca5 | id 3d42fca5… | model qwen2.5:3b (ollama) | transport localhost (127.0.0.1) | visible | Ctrl-C to stop
RECV  HELLO      6b2a6c20 -> *  [msg f9a98f54, 410 B]
clothing-advisor-3d42fca5 met weather-forecaster-6b2a6c20: I am a weather forecaster… ['weather_forecast']
SEND  HELLO_ACK  clothing-advisor-3d42fca5 -> weather-forecaster-6b2a6c20  [msg d4deddc6, 420 B]
clothing-advisor-3d42fca5: reasoning about weather-forecaster-6b2a6c20: useful=True — it can provide the forecast…
BODY  request  -> weather-forecaster-6b2a6c20 {"capability": "weather_forecast", "input": {"question": "What is the weather forecast for tomorrow?"}}
SEND  MSG        clothing-advisor-3d42fca5 -> weather-forecaster-6b2a6c20  [msg ae2c0b22, 303 B]
RECV  ACK        weather-forecaster-6b2a6c20 -> clothing-advisor-3d42fca5  [msg 62a17bf6, 143 B]
RECV  MSG        weather-forecaster-6b2a6c20 -> clothing-advisor-3d42fca5  [msg 34fa3960, 304 B]
BODY  response <- weather-forecaster-6b2a6c20 {"output": {"answer": "Tomorrow will be partly cloudy with a high of 12 degrees Celsius."}}

=== Goal complete ===
goal      : Recommend what to wear tomorrow…
asked     : Weather Forecaster (weather-forecaster-6b2a6c20)
answer    : Tomorrow will be partly cloudy with a high of 12 degrees Celsius.
outcome   : …I recommend a light sweater, jeans and a light jacket…
```

How to read it:

| line | meaning |
|---|---|
| `id 3d42fca5…` | this agent's identity, derived from its key in `~/.loquor/keys/`. Same on every run. |
| `RECV HELLO` / `SEND HELLO_ACK` | the handshake: keys and capability cards exchanged, shared key derived |
| `met …` | the peer is now known: name, description, capabilities |
| `reasoning about …` | the advisor's LLM deciding whether this peer can help its goal |
| `BODY request/response` | what was said, in the clear, *before* encryption / *after* decryption |
| `SEND/RECV MSG` | the encrypted envelope on the wire (303 B — nothing readable in it) |
| `ACK` | delivery confirmation for each MSG |
| `firing show_outfit — …` / `TRIGGER show_outfit {…}` | the LLM's proposal passed schema validation and the trigger ran; the outfit card follows |

Terminal 1 with `-v` also shows `SENSOR weather: temperature_c=12, condition=…`
every 5 s: the reading the forecast was grounded in.

Terminal 1 shows the mirror image: the request arriving, its LLM answering,
the response leaving.

Things to try:
- Restart the advisor: a new random forecast, different clothes, same ids.
- Ask the forecaster for raw data instead of prose: any skill can
  `ctx.ask(peer, "sensor_report", {"sensor": "weather"})` and get the numbers.
- Swap the sensor for a real one: copy `weather_sensor.py`, read your
  instrument in `read()`, change one line in `weather_agent.json`.
- Stop the forecaster: ~30 s later the advisor logs `lost weather-forecaster-…`.
- `--wire no-handshake` hides HELLO/ACK lines; `--wire off` hides all envelopes; `-v` also shows beacons.

## 3. Run on two devices

Two laptops are the easy stand-in; any two devices that run Python and a
local model behave the same. Both on the same Wi-Fi or one device's hotspot
(no internet needed), each set up as in step 1:

```bash
# device A
python run_agent.py demo/weather_and_clothes/weather_agent.json --transport wifi

# device B
python run_agent.py demo/weather_and_clothes/clothing_agent.json --transport wifi
```

The startup line should show `transport wifi (192.168.x.x)` — the device's
LAN address, not `127.0.0.1`. Nothing to pair: each agent finds the other by
its beacons, exchanges keys in the handshake and encrypts from there.

If macOS asks "allow incoming network connections?", click Allow.

### Fully offline test

1. While online, do step 1 on both devices and run step 2 once on each.
2. Use a router with its uplink unplugged, or an Android hotspot with mobile
   data off (an iPhone hotspot usually needs data on).
3. `curl -m 5 https://example.com` must fail; `ping <other device>` must work.
4. Run step 3.

## 4. Options

| flag | effect |
|---|---|
| `--transport localhost\|wifi` | override the profile; both agents must match |
| `--interface <ip>` | `wifi`: choose the network interface |
| `--model llama3.1:8b` | any model from `ollama list` |
| `--visibility visible\|quiet\|silent` | `quiet`: never beacon but answer; `silent`: only initiate |
| `--beacon-interval 2` | faster discovery for demos (default 10 s) |
| `--wire all\|no-handshake\|off`, `-v` | how much of the wire to log |

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `model ... is not available in Ollama` | start Ollama, then `ollama pull <model>` |
| `ModuleNotFoundError` | activate the venv, `pip install -r requirements.txt` |
| agents never `met` each other | same `--transport` on both; neither `--visibility silent`; wait one beacon interval |
| `no LAN interface found for wifi transport` | join a Wi-Fi network or hotspot, or pass `--interface` |
| two devices silent, same Wi-Fi | guest/corporate Wi-Fi isolates clients and drops multicast — use a hotspot or home router |
| `DROP … bad signature` | a peer's key changed (it deleted `~/.loquor/keys/`); it will re-handshake on its next beacon |
| slow answers | on one computer both agents share one Ollama; a 3B model answers in 1–5 s per request on a laptop |
