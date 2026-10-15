# Weather & Clothes

Two agents: the **Weather Forecaster** answers with a forecast, the
**Clothing Advisor** asks it for one and prints what to wear tomorrow.

## Setup (once per machine)

Needs Python 3.10+ and [Ollama](https://ollama.com). In the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b
```

## Run on one machine

Two terminals, both in the repo root with `source .venv/bin/activate`:

```bash
python run_agent.py demo/weather_and_clothes/weather_agent.json    # terminal 1
python run_agent.py demo/weather_and_clothes/clothing_agent.json   # terminal 2
```

Within ~15 s terminal 2 prints `=== Goal complete ===` with the forecast and
the clothes. Stop with Ctrl-C.

## Run on two machines

Both machines on the same Wi-Fi (or one's hotspot), each set up as above:

```bash
# machine A
python run_agent.py demo/weather_and_clothes/weather_agent.json --transport wifi

# machine B
python run_agent.py demo/weather_and_clothes/clothing_agent.json --transport wifi
```

Each agent gets a keypair on first run (`~/.loquor/keys/`), finds the other
by its beacons, exchanges keys and encrypts everything after that.

## Options

- `--model llama3.1:8b` — any model from `ollama list`
- `--wire no-handshake` — hide HELLO/ACK envelopes in the log
- `--visibility quiet` — never beacon; still answers
- `--interface <ip>` — pick the network interface for `wifi`

## Troubleshooting

| Problem | Fix |
|---|---|
| `model ... is not available in Ollama` | start Ollama, then `ollama pull <model>` |
| `ModuleNotFoundError` | activate the venv, `pip install -r requirements.txt` |
| agents don't hear each other | same `--transport` on both; neither set to `--visibility silent` |
| two machines still silent | guest/corporate Wi-Fi blocks devices; use a phone hotspot |
