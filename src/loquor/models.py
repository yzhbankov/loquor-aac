"""Local model files (GGUF) for the embedded llama.cpp engine.

A model is a single ``.gguf`` file. The same file works on every platform,
so it is distributed separately from the ``loquor`` program:

- ``loquor model pull qwen2.5-3b`` downloads a catalog model once (resumable,
  verified against a pinned SHA-256); afterwards everything runs offline.
- ``loquor model import file.gguf`` installs a file copied by hand (USB
  stick, shared drive) on a machine that is never online.

Installed models live in ``$LOQUOR_HOME/models/<name>.gguf``
(``LOQUOR_HOME`` defaults to ``~/.loquor``). A profile's ``model`` is either a
model name or a path to a ``.gguf`` file.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import certifi


@dataclass(frozen=True)
class ModelSpec:
    name: str
    description: str
    url: str
    sha256: str
    size: int  # bytes


CATALOG: dict[str, ModelSpec] = {
    spec.name: spec
    for spec in [
        ModelSpec(
            name="qwen2.5-3b",
            description="Qwen2.5 3B Instruct, Q4_K_M — default; good balance of speed and quality",
            url=(
                "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/"
                "qwen2.5-3b-instruct-q4_k_m.gguf"
            ),
            sha256="626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d",
            size=2_104_932_768,
        ),
    ]
}
DEFAULT_MODEL_NAME = "qwen2.5-3b"


class ModelError(Exception):
    """A model is missing, unknown, or failed verification."""


def loquor_home() -> Path:
    return Path(os.environ.get("LOQUOR_HOME", Path.home() / ".loquor"))


def models_dir() -> Path:
    return loquor_home() / "models"


def installed_path(name: str) -> Path:
    return models_dir() / f"{name}.gguf"


def installed_models() -> list[Path]:
    directory = models_dir()
    return sorted(directory.glob("*.gguf")) if directory.is_dir() else []


def resolve(model: str) -> Path:
    """The model file for a name or path; raises ModelError if not installed."""
    path = Path(model).expanduser()
    if model.endswith(".gguf"):
        if path.is_file():
            return path
        raise ModelError(f"model file not found: {path}")
    if installed_path(model).is_file():
        return installed_path(model)
    if model in CATALOG:
        raise ModelError(
            f"model {model!r} is not installed yet. Run `loquor model pull {model}` once "
            "(needs internet), or copy the .gguf file here and run `loquor model import <file>`"
        )
    known = sorted(set(CATALOG) | {p.stem for p in installed_models()})
    raise ModelError(
        f"unknown model {model!r}. Use one of: {', '.join(known)}, or a path to a .gguf file"
    )


def sha256_of(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            digest.update(block)
    return digest.hexdigest()


async def pull(name: str, *, progress: bool = True) -> Path:
    """Download a catalog model (resuming a partial download) and verify it."""
    if name not in CATALOG:
        raise ModelError(f"{name!r} is not in the catalog: {', '.join(sorted(CATALOG))}")
    spec = CATALOG[name]
    target = installed_path(name)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".gguf.part")

    digest = hashlib.sha256()
    have = 0
    if partial.exists():
        with open(partial, "rb") as f:
            while block := f.read(8 * 1024 * 1024):
                digest.update(block)
                have += len(block)

    headers = {"Range": f"bytes={have}-"} if have else {}
    timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
    # Bundled CA certificates: frozen apps and python.org builds may have none.
    tls = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=tls)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        async with session.get(spec.url, headers=headers) as response:
            if response.status == 200 and have:
                digest, have = hashlib.sha256(), 0  # server ignored Range: restart
            elif response.status not in (200, 206):
                raise ModelError(f"download failed: HTTP {response.status} for {spec.url}")
            mode = "ab" if have else "wb"
            shown = -1
            with open(partial, mode) as f:
                async for block in response.content.iter_chunked(1024 * 1024):
                    f.write(block)
                    digest.update(block)
                    have += len(block)
                    percent = have * 100 // spec.size
                    if progress and percent != shown:
                        shown = percent
                        print(
                            f"\rdownloading {name}: {percent:3d}% "
                            f"({have / 1e9:.2f} / {spec.size / 1e9:.2f} GB)",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
    if progress:
        print(file=sys.stderr)

    if digest.hexdigest() != spec.sha256:
        partial.unlink(missing_ok=True)
        raise ModelError(f"checksum mismatch for {name}; the download was discarded, try again")
    partial.rename(target)
    return target


def import_file(source: str | Path, name: str | None = None) -> tuple[str, Path]:
    """Install a .gguf file copied by hand. Catalog files are recognised by checksum."""
    source = Path(source).expanduser()
    if not source.is_file():
        raise ModelError(f"file not found: {source}")
    checksum = sha256_of(source)
    match = next((s.name for s in CATALOG.values() if s.sha256 == checksum), None)
    if name is None:
        name = match or source.stem
    elif name in CATALOG and match != name:
        raise ModelError(f"{source} is not the catalog file for {name!r} (checksum differs)")
    target = installed_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".gguf.part")
    shutil.copyfile(source, tmp)
    tmp.rename(target)
    return name, target
