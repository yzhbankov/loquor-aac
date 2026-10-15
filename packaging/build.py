"""Build the self-contained loquor package for the current platform.

    pip install -e ".[build]"
    python packaging/build.py

Steps:
1. download the pinned llama.cpp release for this platform (engines.json),
   verify its SHA-256, and keep only llama-server and its libraries;
2. freeze the agent (Python + all dependencies) with PyInstaller;
3. place the engine in ``engine/`` next to the executable;
4. pack ``dist/loquor-<platform>.tar.gz`` (+ ``.sha256``) and smoke-test it.

Run it on each target (macOS arm64/x64, Linux arm64/x64); CI does the same.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def platform_key() -> str:
    system = {"Darwin": "macos", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(
        platform.machine()
    )
    if not system or not machine:
        sys.exit(f"unsupported platform: {platform.system()} {platform.machine()}")
    return f"{system}-{machine}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def fetch_engine(key: str) -> Path:
    """Download, verify and trim the llama.cpp release; returns the engine dir."""
    pins = json.loads((ROOT / "packaging" / "engines.json").read_text())
    pin = pins["platforms"][key]
    build = pins["llama_cpp_build"]
    url = pins["url"].format(build=build, asset=pin["asset"])

    archive = BUILD / f"llama-{build}-{key}.tar.gz"
    BUILD.mkdir(exist_ok=True)
    if not archive.exists() or sha256_of(archive) != pin["sha256"]:
        print(f"downloading {url}")
        import certifi

        tls = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(url, context=tls) as response, open(archive, "wb") as f:
            shutil.copyfileobj(response, f)
    if sha256_of(archive) != pin["sha256"]:
        archive.unlink()
        sys.exit(f"checksum mismatch for {url}")

    unpacked = BUILD / f"llama-{build}-{key}"
    shutil.rmtree(unpacked, ignore_errors=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (unpacked / member.name).resolve()
            if not str(target).startswith(str(unpacked.resolve())) or member.isdev():
                sys.exit(f"unsafe path in engine archive: {member.name}")
        tar.extractall(unpacked)

    server = next(unpacked.rglob("llama-server"), None)
    if server is None:
        sys.exit("llama-server not found in the engine archive")
    source = server.parent

    # Keep llama-server and the shared libraries it loads; drop the other
    # tools and their private libraries (libllama-<tool>-impl).
    engine = BUILD / f"engine-{key}"
    shutil.rmtree(engine, ignore_errors=True)
    engine.mkdir()
    for item in source.iterdir():
        name = item.name
        is_lib = name.startswith("lib") and (".dylib" in name or ".so" in name)
        other_tool = "-impl" in name and "server-impl" not in name
        if name in ("llama-server", "LICENSE") or (is_lib and not other_tool):
            if item.is_symlink():
                (engine / name).symlink_to(item.readlink())
            else:
                shutil.copy2(item, engine / name)
    if (engine / "LICENSE").exists():
        (engine / "LICENSE").rename(engine / "LICENSE-llama.cpp")
    return engine


def freeze() -> Path:
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onedir",
            "--name", "loquor",
            "--distpath", str(BUILD / "dist"),
            "--workpath", str(BUILD / "work"),
            "--specpath", str(BUILD),
            "--paths", str(ROOT / "src"),
            "--collect-submodules", "zeroconf",
            str(ROOT / "packaging" / "entry.py"),
        ],
        check=True,
    )
    return BUILD / "dist" / "loquor"


def main() -> None:
    key = platform_key()
    engine = fetch_engine(key)
    app = freeze()
    shutil.copytree(engine, app / "engine", symlinks=True)

    DIST.mkdir(exist_ok=True)
    package = DIST / f"loquor-{key}.tar.gz"
    with tarfile.open(package, "w:gz") as tar:
        tar.add(app, arcname="loquor")
    (DIST / f"{package.name}.sha256").write_text(f"{sha256_of(package)}  {package.name}\n")

    print("\nsmoke test:")
    subprocess.run([str(app / "loquor"), "version"], check=True)
    size = package.stat().st_size / 1e6
    print(f"\nbuilt {package} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
