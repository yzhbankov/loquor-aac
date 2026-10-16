# Install

How to put loquor on a device: a laptop, a Raspberry Pi, an industrial
PC, anything that runs Linux or macOS with Python 3.10 or newer. One
script does it; this page says what it does, what to do when it cannot,
and how to keep the agent running after a reboot.

## 1. The script

```bash
git clone https://github.com/yzhbankov/loquor-aac.git && cd loquor-aac
./install.sh
```

What it does, in order, skipping anything already done:

| step | what | how |
|---|---|---|
| Python 3.10+ | finds one or installs it | `apt` / `dnf` / `pacman` / Homebrew |
| BlueZ (Linux) | the Bluetooth daemon | `apt install bluez` (or the distro's equivalent) |
| the package | a virtualenv in `.venv` with loquor and the Bluetooth libraries | `python -m venv .venv && pip install -e ".[ble]"` |
| Ollama | the local model server, started if it is not running | Homebrew on macOS; `ollama.com/install.sh` on Linux (asks first) |
| a model | one sized for the device's RAM, pulled once | 8 GB+: `qwen2.5:3b` · 4 GB: `qwen2.5:1.5b` · less: `qwen2.5:0.5b` |
| device config | the model (and the transport, if given) saved to `~/.loquor/config.json` | `loquor config set …` |
| check | `loquor doctor` | |

Flags:

```
--node                a sensor node: no Ollama, no model; sets `llm false` in the device config
--model NAME          use this model instead of the one chosen by RAM
--transport T         save the transport too: localhost | wifi | bluetooth
--no-ollama           skip Ollama and the model (you run them elsewhere, or use the template reasoner)
--no-model            install Ollama but pull nothing
--no-ble              skip the Bluetooth libraries and BlueZ
--ble-le-mode         Linux: put BlueZ in LE-only mode (see Bluetooth below); needs sudo
--service PROFILE     also install that agent as a boot service and start it
--yes                 never ask
```

Running it again is safe: every step checks before acting, and the
device config keeps keys you set by hand.

## 2. By hand

If the script cannot run (no sudo, an unusual distro, a locked-down
build), these are the same steps:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ble]"              # or `pip install -e .` without Bluetooth
# Ollama: https://ollama.com/download, then:
ollama pull qwen2.5:3b               # or 1.5b / 0.5b for small boards
loquor config set model qwen2.5:3b
loquor doctor
```

`loquor doctor` prints one line per prerequisite and the fix for each
failure. Nothing else needs to pass before `loquor run` works.

## 3. Per device

### macOS

- Homebrew makes everything automatic; without it, install Python from
  python.org and Ollama from ollama.com, then run the script.
- The first `--transport wifi` run asks "allow incoming network
  connections?" — Allow.
- The first `--transport bluetooth` run asks for Bluetooth access for
  your terminal (System Settings → Privacy & Security → Bluetooth).

### Ubuntu / Debian / Raspberry Pi OS

- The script uses `sudo apt-get` for Python, venv and BlueZ.
- Raspberry Pi 4 (4 GB) and 5: `qwen2.5:1.5b` answers in a few seconds;
  `qwen2.5:3b` works on the 8 GB models but is slow. Pi Zero / 3: make it
  a **sensor node** (`./install.sh --node`, no LLM at all) and let an
  agent elsewhere do the thinking; or run Ollama on another machine on
  the same network and point to it:
  `loquor config set ollama_url http://192.168.1.10:11434`.
- For Bluetooth between two laptops, see below.

### Fedora, Arch, other Linux

- `dnf` and `pacman` are supported for Python and BlueZ. On anything
  else, install `python3` (3.10+), `python3-venv` and `bluez` yourself
  and run the script; it continues from the virtualenv step.

### Windows

Not supported by the script. WSL2 runs the Wi-Fi and localhost
transports; Bluetooth does not reach into WSL.

## 4. Bluetooth prerequisites

Bluetooth needs the radio libraries (`pip install -e ".[ble]"`, which
the script does) and, once per device:

- **Forget any old pairing between the two devices** (macOS: System
  Settings → Bluetooth → the other device → Forget; Linux:
  `bluetoothctl remove <address>`). loquor never pairs; a stale bond
  shows up as `Peer removed pairing information`.
- **Linux: LE-only mode.** A dual-mode adapter tries a classic
  connection to a laptop and fails (`br-connection-canceled`). Put BlueZ
  in LE-only mode with `./install.sh --ble-le-mode`, or by hand:

  ```
  # /etc/bluetooth/main.conf
  [General]
  ControllerMode = le
  ```
  then `sudo systemctl restart bluetooth`.
- **Linux: the adapter must be powered**: `bluetoothctl show` says
  `Powered: yes`; if not, `bluetoothctl power on`.

`loquor doctor --transport bluetooth` checks all of this that can be
checked from software.

## 5. Run at boot

```bash
loquor service install my_agent/agent.json --now     # Linux: systemd user unit · macOS: launchd agent
loquor service uninstall my_agent/agent.json
```

Without `--now` it writes the unit and prints the commands to enable it,
so you can read them first. On Linux the unit is a *user* unit in
`~/.config/systemd/user/`; run `loginctl enable-linger $USER` once so it
starts with nobody logged in — the script prints that line. Logs:
`journalctl --user -u loquor-<name> -f` on Linux, `~/.loquor/log/` on
macOS. The service runs the same virtualenv and device config you tested
with; `loquor status` works the same way for a service as for a terminal
run.

## 6. Updating

```bash
git pull && pip install -e ".[ble]"      # in the venv
loquor doctor
```

Keys in `~/.loquor/keys/` survive updates, so agents keep their identity.
Both devices should run the same version; the wire format is versioned,
and `loquor doctor` prints the installed version.

## 7. What lands where

| path | what |
|---|---|
| `.venv/` in the repo | the Python environment and the `loquor` command |
| `~/.loquor/config.json` | this device's transport, model (or `llm false` for a node), beacon interval, logging |
| `~/.loquor/keys/<name>.key` | an agent's identity, created on its first run |
| `~/.loquor/pinned_keys.json` | peers you have pinned (trust level 1) |
| `~/.loquor/run/<name>.json` | a running agent's status, read by `loquor status` |
| `~/.loquor/run/<name>.trace.jsonl` | every LLM call of its last run, read by `loquor trace` |
| `~/.loquor/log/` | service logs on macOS |

`LOQUOR_HOME` moves `~/.loquor` somewhere else, for instance onto
persistent storage on a device with a read-only root.
