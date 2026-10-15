# Weather & Clothes

Two agents in two separate processes. Both are the same `loquor` agent and
differ only in their profile. They follow the
[broadcast protocol](../../docs/PROTOCOL.md).

| Profile | Goal | What it does |
|---|---|---|
| `weather_agent.json` | none | advertises `weather_forecast`; answers with a random forecast |
| `clothing_agent.json` | recommend what to wear tomorrow | hears the forecaster, decides it can help, asks it, recommends clothes |

```
weather  ──advert (uid, weather_forecast)───────────────▶ everyone, every 10 s
clothing ──advert (uid, clothing_recommendation)────────▶ everyone, every 10 s
clothing   🧠 "the forecaster can tell me tomorrow's weather"
clothing ──request  to=<weather uid>  "What is the weather tomorrow?"──▶
weather    🧠 forecast from its system prompt (+ {random_number})
weather  ──response to=<clothing uid> "12 °C, partly cloudy"──▶
clothing   🧠 "light sweater, trousers, light jacket"   → prints the result
```

## 1. Set up (once)

From the repository root:

```bash
python3 -m venv .venv                 # Python 3.10+ (macOS `python` may be 2.7)
source .venv/bin/activate
pip install --upgrade pip
pip install -e .                      # installs the `loquor` command
ollama pull llama3.1:8b               # the model both profiles use
```

Ollama must be running (the Ollama app, or `ollama serve`).

## 2. Run on one machine

Open two terminals. In each one, `cd` to the repository root and run
`source .venv/bin/activate`, then:

```bash
# terminal 1
loquor run examples/weather_and_clothes/weather_agent.json

# terminal 2
loquor run examples/weather_and_clothes/clothing_agent.json
```

Start them in any order. Within about 15 seconds terminal 2 shows (an agent is heard at its next advert, up to 10 s):

```
clothing-advisor-53861aca heard advert from weather-forecaster-b5736995: I am a weather forecaster ...
clothing-advisor-53861aca reasoning about weather-forecaster-b5736995: useful=True — The Weather Forecaster agent can provide the weather forecast ...
clothing-advisor-53861aca -> weather-forecaster-b5736995 request: What is the weather forecast for tomorrow?
clothing-advisor-53861aca <- weather-forecaster-b5736995 response: ... 12 degrees Celsius, and partly cloudy ...

=== Goal complete ===
...
outcome   : ... I recommend wearing a lightweight, water-repellent jacket ...
```

Terminal 1 shows the same request arriving and the forecast it sent back.
Things to try:
- Restart the clothing agent several times: each run gets a new random forecast and different clothes.
- Stop the forecaster with Ctrl-C: about 30 s later (3 missed adverts) the clothing agent logs `lost weather-forecaster-…`.

### Message log

Every message an agent sends or receives is logged on one line:

```
SEND  request  clothing-advisor-4f53f11d -> weather-forecaster-b618be7c  [msg 02cb0a93, 432 B] {"request_id": "67b5…", "question": "What is the weather forecast for tomorrow?"}
RECV  response weather-forecaster-b618be7c -> clothing-advisor-4f53f11d  [msg a6b6ca11, 473 B] {"request_id": "67b5…", "answer": "Tomorrow will be partly cloudy …"}
RECV  request  clothing-advisor-ce98a5a0 -> weather-forecaster-a941d5ab  [msg 36550956, 431 B] (overheard, not for me) {…}
DROP  advert   weather-forecaster-f5ff9a7a -> *  [msg bfe19bd0, 491 B] (bad signature) {…}
```

- `SEND` / `RECV`: the message type, sender uid -> recipient uid (`*` means everyone), message id and size, then the full payload.
- `(overheard, not for me)`: the message was addressed to another agent. The agent hears it but doesn't act on it.
- `DROP`: the message was rejected (wrong pairing secret, or not a loquor message).

Every agent sends an advert every 10 s, and these fill the log. Use
`--wire no-adverts` to hide them, `--wire off` to hide the whole message
log, or `-v` to also see duplicate deliveries.

Use a different model: `loquor run <profile> --model gemma3:1b` (it works,
but its answers are less consistent than `llama3.1:8b`'s).

## 3. Run on two laptops (Wi-Fi)

Put both laptops on the same Wi-Fi network, or one laptop's hotspot (no
internet needed). Set up each one as in step 1, then:

```bash
# both laptops: the same secret
export LOQUOR_PAIRING_SECRET=<same-on-both>

# laptop A
loquor run examples/weather_and_clothes/weather_agent.json --transport wifi
# laptop B
loquor run examples/weather_and_clothes/clothing_agent.json --transport wifi
```

- If a laptop has several network interfaces, add `--interface <its Wi-Fi IP>`.
- Guest and corporate Wi-Fi often block traffic between clients; a hotspot avoids that.

## 4. Make your own agent

Copy a profile and edit it:

```json
{
  "name": "My Agent",
  "description": "One sentence other agents read to decide if you can help them.",
  "capabilities": ["short_tag"],
  "system_prompt": "Who you are, what you know, how you answer.",
  "goal": null,
  "temperature": 0.2,
  "model": "llama3.1:8b"
}
```

- `goal: null`: the agent only advertises and answers.
- A `goal` string: it also looks for agents that can help it reach that goal.
- `{random_number}` in `system_prompt` is replaced with a fresh 0–100 value on every request.

## Troubleshooting

| Message | Fix |
|---|---|
| `model 'llama3.1:8b' is not available` | start Ollama and run `ollama pull llama3.1:8b`, or use `--model <one you have>` |
| `invalid profile ...: unknown fields [...]` | typo in a profile field name |
| `no LAN interface found for wifi transport` | join a Wi-Fi network or hotspot, or pass `--interface` |
| agents never hear each other | same `--transport` and same `LOQUOR_PAIRING_SECRET` on both |
