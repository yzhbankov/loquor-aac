"""A model served by a local Ollama (``http://localhost:11434``)."""

from __future__ import annotations

import logging
import os

import aiohttp

from loquor.reasoning.base import Reasoner

log = logging.getLogger("loquor")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = os.environ.get("LOQUOR_MODEL", "qwen2.5:3b")


class OllamaReasoner(Reasoner):
    name = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        temperature: float = 0.2,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        request = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temperature if temperature is None else temperature},
        }
        if schema is not None:
            request["format"] = schema
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/api/generate", json=request) as response:
                response.raise_for_status()
                body = await response.json()
        return (body.get("response") or "").strip()


def _model_matches(wanted: str, installed: str) -> bool:
    # Ollama treats "name" and "name:latest" as the same model.
    def norm(m: str) -> str:
        return m if ":" in m else f"{m}:latest"

    return norm(wanted) == norm(installed)


async def ollama_available(base_url: str = DEFAULT_OLLAMA_URL, model: str | None = None) -> bool:
    """True if Ollama is reachable (and, if ``model`` is given, has it pulled)."""
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url.rstrip('/')}/api/tags") as response:
                if response.status != 200:
                    return False
                if model is None:
                    return True
                body = await response.json()
    except Exception:
        return False
    installed = [m.get("name", "") for m in body.get("models", [])]
    if any(_model_matches(model, name) for name in installed):
        return True
    log.warning(
        "Ollama is running but model %r is not pulled (have: %s)",
        model,
        ", ".join(installed) or "none",
    )
    return False
