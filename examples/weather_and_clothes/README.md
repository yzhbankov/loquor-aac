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

**Install loquor.** Nothing else is needed: no Python, no Ollama. The
program, its libraries and the LLM engine are all in one package for macOS or
Linux, on ARM or x86.

```bash
# from a release (downloads the build for this OS/CPU):
curl -fsSL https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/packaging/install.sh | sh

# or from a package file you were given or built yourself:
sh packaging/install.sh loquor-macos-arm64.tar.gz
```

**Install the model** (Qwen2.5 3B, 2.1 GB), once per machine:

```bash
loquor model pull                     # needs internet once; resumes if interrupted
# or, on a machine that is never online, copy the .gguf file over and:
loquor model import qwen2.5-3b-instruct-q4_k_m.gguf
```

Without either step, the first `loquor run` downloads the model itself.

## 2. Run on one machine

Copy the two profiles to the machine (they are plain JSON files), then open
two terminals:

```bash
# terminal 1
loquor run weather_agent.json
# terminal 2
loquor run clothing_agent.json
```

These use the default transport, `--transport localhost`: the agents talk
over the machine's loopback interface, and no network is needed. Agents on
other machines can't hear them. To talk between laptops, use
`--transport wifi` (section 3). Both agents must use the same transport: a
`localhost` agent and a `wifi` agent don't hear each other, even on the same
machine.

| Transport | Flag | Who can talk |
|---|---|---|
| localhost | none (default), or `--transport localhost` | agents on this machine |
| Wi-Fi / LAN | `--transport wifi` | agents on the same Wi-Fi, hotspot or wired network |
| Bluetooth | `--transport bluetooth` | planned, not available yet |

The startup line shows which transport is in use:
`weather-forecaster-1a58e8d8 | model qwen2.5-3b (embedded) | transport localhost (127.0.0.1) | Ctrl-C to stop`

Each agent starts its own copy of the model (about 5 s, about 2.5 GB of
memory each). Start them in any order. Within about 15 seconds terminal 2
shows the following (an agent is heard at its next advert, up to 10 s later):

```
clothing-advisor-12a40073 heard advert from weather-forecaster-1a58e8d8: I am a weather forecaster ...
clothing-advisor-12a40073 reasoning about weather-forecaster-1a58e8d8: useful=True — The Weather Forecaster can provide the necessary weather forecast ...
SEND  request  clothing-advisor-12a40073 -> weather-forecaster-1a58e8d8  [...] {"question": "Could you provide me with a weather forecast for tomorrow ...?"}
RECV  response weather-forecaster-1a58e8d8 -> clothing-advisor-12a40073  [...] {"answer": "Tomorrow, the temperature will be 12 degrees Celsius with partly cloudy skies."}

=== Goal complete ===
...
outcome   : ... I recommend wearing a light sweater or a jacket along with light pants or jeans ...
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

Use a different model: `loquor run <profile> --model <name or file.gguf>`
(see `loquor model list`). If you have Ollama and want to use its models,
add `--engine ollama --model llama3.1:8b`.

## 3. Run on two laptops (Wi-Fi)

Put both laptops on the same Wi-Fi network, or one laptop's hotspot (no
internet needed). Set up each one as in step 1 and copy the profile to it, then:

```bash
# both laptops: the same secret
export LOQUOR_PAIRING_SECRET=<same-on-both>

# laptop A
loquor run weather_agent.json --transport wifi
# laptop B
loquor run clothing_agent.json --transport wifi
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
  "model": "qwen2.5-3b"
}
```

- `goal: null`: the agent only advertises and answers.
- A `goal` string: it also looks for agents that can help it reach that goal.
- `{random_number}` in `system_prompt` is replaced with a fresh 0–100 value on every request.

## Troubleshooting

| Message | Fix |
|---|---|
| `model 'qwen2.5-3b' is not installed yet` | `loquor model pull` (online) or `loquor model import <file.gguf>` (offline) |
| `llama-server exited at startup` | see the engine log under `~/.loquor/logs/`; often not enough memory for the model |
| macOS: "cannot be opened because the developer cannot be verified" | the package was downloaded by a browser: `xattr -dr com.apple.quarantine ~/.local/share/loquor` (install.sh does this) |
| `invalid profile ...: unknown fields [...]` | typo in a profile field name |
| `no LAN interface found for wifi transport` | join a Wi-Fi network or hotspot, or pass `--interface` |
| agents never hear each other | same `--transport` and same `LOQUOR_PAIRING_SECRET` on both |
