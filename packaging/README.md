# Packaging

`loquor` is distributed as one self-contained archive per platform:

| Archive | Platform |
|---|---|
| `loquor-macos-arm64.tar.gz` | macOS, Apple Silicon |
| `loquor-macos-x64.tar.gz` | macOS, Intel |
| `loquor-linux-arm64.tar.gz` | Linux, ARM64 (e.g. Raspberry Pi 5, Graviton) |
| `loquor-linux-x64.tar.gz` | Linux, x86-64 |

Each archive (about 25 MB) unpacks to:

```
loquor/
├── loquor          the agent: Python + all libraries, frozen with PyInstaller
├── _internal/      its runtime files
└── engine/         llama.cpp `llama-server` + libraries (Metal GPU on macOS)
```

Models are **not** inside. A `.gguf` model file is the same on every
platform; it is downloaded once (`loquor model pull`) or imported from a file
(`loquor model import`) into `~/.loquor/models/`.

## Build

The archive is built natively on each target platform (PyInstaller does not
cross-compile):

```bash
python3 -m venv .venv && source .venv/bin/activate     # Python 3.10+
pip install -e ".[build]"
python packaging/build.py
# -> dist/loquor-<platform>.tar.gz and .sha256
```

`build.py` downloads the llama.cpp release pinned in `engines.json`, checks
its SHA-256, keeps only `llama-server` and its libraries, freezes the agent,
packs the archive and runs `loquor version` as a smoke test.

To update the engine, change `llama_cpp_build` and the four `sha256` values in
`engines.json`. The checksums are listed on the GitHub release page, or:
`gh api repos/ggml-org/llama.cpp/releases/tags/<build> --jq '.assets[] | .name + " " + .digest'`

## Install

```bash
sh packaging/install.sh dist/loquor-macos-arm64.tar.gz   # from a local archive
curl -fsSL .../packaging/install.sh | sh                 # from the latest GitHub release
```

The installer picks the archive for the current OS and CPU, verifies the
`.sha256`, installs to `~/.local/share/loquor` and links `~/.local/bin/loquor`.
On macOS it clears the download quarantine flag. The binaries are not signed
or notarized, so a package downloaded with a browser would otherwise be
blocked by Gatekeeper.

## Status

- `macos-arm64`: built and tested. Runs with no Python or Ollama on PATH;
  model pull, resume and import work; the engine stops with the agent
  (Ctrl-C, `kill`, `kill -9`).
- `macos-x64`, `linux-arm64`, `linux-x64`: same scripts, not built yet; next
  step is a CI release workflow on native runners.
