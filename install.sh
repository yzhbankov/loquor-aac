#!/usr/bin/env bash
# loquor installer — macOS, Debian/Ubuntu/Raspberry Pi OS, Fedora, Arch, other Linux.
#
#   ./install.sh                       everything: Python, the package, Ollama, a model for this hardware
#   ./install.sh --transport wifi      ...and set this device's transport
#   ./install.sh --model qwen2.5:1.5b  ...with a specific model
#   ./install.sh --ble-le-mode         ...and put BlueZ in LE-only mode (Linux, needs sudo)
#   ./install.sh --service agent.json  ...and start that agent at boot
#
# Safe to run again: every step checks before it acts. Run it from the repo root.
#
# Flags: --model NAME  --transport localhost|wifi|bluetooth  --no-ollama  --no-model
#        --no-ble  --ble-le-mode  --service PROFILE  --yes (never ask)  --help

set -euo pipefail

MODEL=""
TRANSPORT=""
WANT_OLLAMA=1
WANT_MODEL=1
WANT_BLE=1
LE_MODE=0
SERVICE=""
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --transport) TRANSPORT="$2"; shift 2 ;;
    --no-ollama) WANT_OLLAMA=0; WANT_MODEL=0; shift ;;
    --no-model) WANT_MODEL=0; shift ;;
    --no-ble) WANT_BLE=0; shift ;;
    --ble-le-mode) LE_MODE=1; shift ;;
    --service) SERVICE="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --help|-h) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1 (see --help)"; exit 2 ;;
  esac
done

cd "$(dirname "$0")"
OS="$(uname -s)"
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    ok   %s\n' "$*"; }
note() { printf '    note %s\n' "$*"; }
die()  { printf '\n\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask "question" -> 0 for yes
  [ "$ASSUME_YES" -eq 1 ] && return 0
  read -r -p "    $1 [Y/n] " answer </dev/tty || return 0
  case "$answer" in n|N|no|NO) return 1 ;; *) return 0 ;; esac
}

pkg_install() { # pkg_install <apt names> | <dnf names> | <pacman names> | <brew names>
  local apt="$1" dnf="$2" pacman="$3" brew="$4"
  if [ "$OS" = "Darwin" ]; then
    [ -n "$brew" ] || return 0
    command -v brew >/dev/null 2>&1 || die "Homebrew is needed to install $brew: https://brew.sh"
    brew install $brew
  elif command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq $apt
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y $dnf
  elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -Sy --noconfirm $pacman
  else
    die "no known package manager; install $apt yourself, then run this again"
  fi
}

python_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}

ram_gb() {
  if [ "$OS" = "Darwin" ]; then
    echo $(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
  elif [ -r /proc/meminfo ]; then
    echo $(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
  else
    echo 0
  fi
}

# ---------------------------------------------------------------- 1. Python
say "Python 3.10+"
if ! PYTHON="$(find_python)"; then
  note "not found; installing"
  pkg_install "python3 python3-venv python3-pip" "python3" "python" "python@3.12"
  PYTHON="$(find_python)" || die "Python 3.10+ still not found; install it and run this again"
fi
if ! "$PYTHON" -c 'import venv, ensurepip' 2>/dev/null; then
  note "venv module missing; installing"
  pkg_install "python3-venv python3-pip" "python3" "python" ""
fi
ok "$("$PYTHON" --version) at $PYTHON"

# ----------------------------------------------------------- 2. BLE system deps
if [ "$WANT_BLE" -eq 1 ] && [ "$OS" = "Linux" ]; then
  say "Bluetooth stack (BlueZ)"
  if command -v bluetoothctl >/dev/null 2>&1; then
    ok "bluez installed"
  else
    pkg_install "bluez" "bluez" "bluez bluez-utils" ""
    ok "bluez installed"
  fi
fi

# ---------------------------------------------------------------- 3. venv
say "Virtual environment and the loquor package"
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
  ok "created .venv"
else
  ok ".venv exists"
fi
.venv/bin/python -m pip install --quiet --upgrade pip wheel
if [ "$WANT_BLE" -eq 1 ]; then
  if .venv/bin/python -m pip install --quiet -e ".[ble]"; then
    ok "installed loquor with Bluetooth support"
  else
    note "Bluetooth libraries failed to install; installing without them (Wi-Fi and localhost still work)"
    .venv/bin/python -m pip install --quiet -e "."
    ok "installed loquor"
  fi
else
  .venv/bin/python -m pip install --quiet -e "."
  ok "installed loquor (no Bluetooth libraries; add them later with: .venv/bin/pip install -e '.[ble]')"
fi
LOQUOR=".venv/bin/loquor"

# --------------------------------------------------------------- 4. Ollama
if [ "$WANT_OLLAMA" -eq 1 ]; then
  say "Ollama (serves the local model)"
  if ! command -v ollama >/dev/null 2>&1; then
    if [ "$OS" = "Darwin" ]; then
      if command -v brew >/dev/null 2>&1; then
        brew install ollama
      else
        die "install Ollama from https://ollama.com/download, open it once, then run this again (or: ./install.sh --no-ollama)"
      fi
    else
      ask "install Ollama with its official script (curl https://ollama.com/install.sh | sh)?" || die "Ollama is needed; rerun with --no-ollama to skip"
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  fi
  ok "ollama $(ollama --version 2>/dev/null | head -1 | sed 's/.*version is //')"
  if ! curl -fsS -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    note "starting the Ollama server"
    if [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then
      brew services start ollama >/dev/null
    elif [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ollama.service >/dev/null 2>&1; then
      $SUDO systemctl enable --now ollama >/dev/null 2>&1 || true
    else
      nohup ollama serve >/dev/null 2>&1 &
    fi
    for _ in $(seq 1 30); do
      curl -fsS -m 2 http://localhost:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS -m 2 http://localhost:11434/api/tags >/dev/null 2>&1 || die "Ollama did not start; run 'ollama serve' in another terminal and run this again"
  fi
  ok "server reachable at http://localhost:11434"
fi

# ---------------------------------------------------------------- 5. Model
if [ "$WANT_MODEL" -eq 1 ]; then
  say "Model"
  if [ -z "$MODEL" ]; then
    RAM="$(ram_gb)"
    if   [ "$RAM" -ge 7 ]; then MODEL="qwen2.5:3b"
    elif [ "$RAM" -ge 3 ]; then MODEL="qwen2.5:1.5b"
    else MODEL="qwen2.5:0.5b"; fi
    note "this device has ${RAM} GB RAM; choosing $MODEL (override with --model)"
  fi
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    ok "$MODEL already pulled"
  else
    ollama pull "$MODEL"
    ok "$MODEL pulled"
  fi
fi

# ---------------------------------------------------------- 6. Device config
say "Device config"
ARGS=()
[ -n "$MODEL" ] && ARGS+=(model "$MODEL")
[ -n "$TRANSPORT" ] && ARGS+=(transport "$TRANSPORT")
if [ ${#ARGS[@]} -gt 0 ]; then
  "$LOQUOR" config set "${ARGS[@]}" | tail -1
else
  ok "unchanged ($("$LOQUOR" config path))"
fi

# ---------------------------------------------------------- 7. BlueZ LE mode
if [ "$LE_MODE" -eq 1 ]; then
  say "BlueZ LE-only mode"
  if [ "$OS" != "Linux" ]; then
    note "only applies to Linux; skipped"
  else
    CONF=/etc/bluetooth/main.conf
    if [ -f "$CONF" ] && grep -Eq '^\s*ControllerMode\s*=\s*le\s*$' "$CONF"; then
      ok "already set in $CONF"
    else
      $SUDO touch "$CONF"
      if grep -Eq '^\s*#?\s*ControllerMode\s*=' "$CONF"; then
        $SUDO sed -i -E 's/^\s*#?\s*ControllerMode\s*=.*/ControllerMode = le/' "$CONF"
      elif grep -q '^\[General\]' "$CONF"; then
        $SUDO sed -i 's/^\[General\]/[General]\nControllerMode = le/' "$CONF"
      else
        printf '\n[General]\nControllerMode = le\n' | $SUDO tee -a "$CONF" >/dev/null
      fi
      $SUDO systemctl restart bluetooth 2>/dev/null || true
      ok "ControllerMode = le written to $CONF; bluetooth restarted"
    fi
  fi
fi

# -------------------------------------------------------------- 8. Service
if [ -n "$SERVICE" ]; then
  say "Service"
  SVC_ARGS=()
  [ -n "$TRANSPORT" ] && SVC_ARGS+=(--transport "$TRANSPORT")
  "$LOQUOR" service install "$SERVICE" ${SVC_ARGS[@]+"${SVC_ARGS[@]}"} --now
fi

# --------------------------------------------------------------- 9. Doctor
say "Check"
DOCTOR_ARGS=()
[ "$WANT_OLLAMA" -eq 0 ] && DOCTOR_ARGS+=(--no-llm)
[ -n "$TRANSPORT" ] && DOCTOR_ARGS+=(--transport "$TRANSPORT")
"$LOQUOR" doctor ${DOCTOR_ARGS[@]+"${DOCTOR_ARGS[@]}"} || true

cat <<EOF

Done. Next:
  source .venv/bin/activate
  loquor init my_agent               # a working agent to start from
  loquor check my_agent/agent.json   # build it, read its sensors, check the model
  loquor run   my_agent/agent.json   # on the air
  loquor run demo/weather_and_clothes/weather_agent.json    # or a demo (see README)
EOF
