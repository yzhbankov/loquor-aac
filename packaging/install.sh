#!/bin/sh
# Install the self-contained loquor agent (macOS / Linux, arm64 / x86-64).
#
#   curl -fsSL https://raw.githubusercontent.com/yzhbankov/loquor-aac/master/packaging/install.sh | sh
#   sh install.sh ./loquor-macos-arm64.tar.gz      # from a local file (offline machines)
#
# Installs to ~/.local/share/loquor and links ~/.local/bin/loquor.
# Nothing else is needed: Python, its libraries and the LLM engine are inside.
set -eu

REPO="${LOQUOR_REPO:-yzhbankov/loquor-aac}"
VERSION="${LOQUOR_VERSION:-latest}"
APP_DIR="${LOQUOR_INSTALL_DIR:-$HOME/.local/share/loquor}"
BIN_DIR="${LOQUOR_BIN_DIR:-$HOME/.local/bin}"

case "$(uname -s)" in
  Darwin) os=macos ;;
  Linux) os=linux ;;
  *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64 | aarch64) arch=arm64 ;;
  x86_64 | amd64) arch=x64 ;;
  *) echo "unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
asset="loquor-$os-$arch.tar.gz"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [ $# -ge 1 ]; then
  cp "$1" "$tmp/$asset"
  [ -f "$1.sha256" ] && cp "$1.sha256" "$tmp/$asset.sha256"
else
  if [ "$VERSION" = latest ]; then base="https://github.com/$REPO/releases/latest/download"
  else base="https://github.com/$REPO/releases/download/$VERSION"; fi
  echo "downloading $asset ..."
  curl -fSL --progress-bar -o "$tmp/$asset" "$base/$asset"
  curl -fsSL -o "$tmp/$asset.sha256" "$base/$asset.sha256"
fi

if [ -f "$tmp/$asset.sha256" ]; then
  expected="$(cut -d' ' -f1 "$tmp/$asset.sha256")"
  if [ "$(sha256 "$tmp/$asset")" != "$expected" ]; then
    echo "checksum mismatch for $asset; not installing" >&2
    exit 1
  fi
else
  echo "warning: no .sha256 file next to $asset; skipping the checksum check" >&2
fi

mkdir -p "$APP_DIR" "$BIN_DIR"
rm -rf "$APP_DIR/loquor"
tar -xzf "$tmp/$asset" -C "$APP_DIR"
if [ "$os" = macos ]; then
  # Files downloaded by a browser are quarantined and would be blocked.
  xattr -dr com.apple.quarantine "$APP_DIR/loquor" 2>/dev/null || true
fi
ln -sf "$APP_DIR/loquor/loquor" "$BIN_DIR/loquor"

echo "installed: $BIN_DIR/loquor"
"$BIN_DIR/loquor" version
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "add it to your PATH:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo "next: loquor model pull     (once, ~2 GB; or 'loquor model import <file.gguf>' offline)"
