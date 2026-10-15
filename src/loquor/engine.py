"""Embedded LLM engine: a private llama.cpp ``llama-server`` per agent.

The packaged ``loquor`` ships ``llama-server`` (and its libraries) in an
``engine/`` folder next to the executable, so no separate LLM runtime has to
be installed. The agent starts the server on a free localhost port, talks to
its OpenAI-compatible API, and stops it on exit.

The binary is looked up in this order:

1. ``$LOQUOR_LLAMA_SERVER``
2. ``engine/llama-server`` next to the packaged executable
3. ``llama-server`` on ``PATH`` (e.g. ``brew install llama.cpp``) for development
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
from pathlib import Path

import aiohttp

from loquor.models import loquor_home
from loquor.reasoning import Reasoner


class EngineError(Exception):
    pass


def find_llama_server() -> Path:
    candidates = []
    if os.environ.get("LOQUOR_LLAMA_SERVER"):
        candidates.append(Path(os.environ["LOQUOR_LLAMA_SERVER"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "engine" / "llama-server")
    on_path = shutil.which("llama-server")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise EngineError(
        "llama-server not found. Use the packaged loquor build, set LOQUOR_LLAMA_SERVER, "
        "or install llama.cpp (e.g. `brew install llama.cpp`)"
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    """One llama-server process serving one model on 127.0.0.1."""

    def __init__(
        self,
        model_path: Path,
        *,
        binary: Path | None = None,
        context_size: int = 4096,
        startup_timeout: float = 180.0,
    ):
        self.model_path = model_path
        self.binary = binary or find_llama_server()
        self.context_size = context_size
        self.startup_timeout = startup_timeout
        self.port = _free_port()
        self.log_path = loquor_home() / "logs" / f"engine-{self.port}.log"
        self._proc: asyncio.subprocess.Process | None = None
        self._watchdog: asyncio.subprocess.Process | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self.log_path, "wb")
        self._proc = await asyncio.create_subprocess_exec(
            str(self.binary),
            "--model", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--ctx-size", str(self.context_size),
            "--parallel", "1",
            "--n-gpu-layers", "999",  # use the GPU (Metal on Macs) when there is one
            "--no-webui",
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        log_file.close()
        # If loquor is killed without a chance to clean up (kill -9, crash),
        # this tiny watchdog stops the engine so it doesn't run on orphaned.
        self._watchdog = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c",
            'while kill -0 "$0" 2>/dev/null; do sleep 2; done; kill "$1" 2>/dev/null',
            str(os.getpid()), str(self._proc.pid),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            while True:
                if self._proc.returncode is not None:
                    raise EngineError(f"llama-server exited at startup:\n{self._log_tail()}")
                try:
                    async with session.get(f"{self.base_url}/health") as response:
                        if response.status == 200:
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    await self.stop()
                    raise EngineError(f"llama-server did not become ready:\n{self._log_tail()}")
                await asyncio.sleep(0.25)

    async def stop(self) -> None:
        if self._watchdog is not None and self._watchdog.returncode is None:
            self._watchdog.kill()
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), 5)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()

    def _log_tail(self, lines: int = 15) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return "(no engine log)"


class LlamaCppReasoner(Reasoner):
    """Reasoner backed by a llama-server's OpenAI-compatible chat API."""

    name = "llama.cpp"

    def __init__(self, base_url: str, model: str = "local", temperature: float = 0.2):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        request = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": 512,
        }
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema},
            }
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/v1/chat/completions", json=request
            ) as response:
                response.raise_for_status()
                body = await response.json()
        return (body["choices"][0]["message"].get("content") or "").strip()
