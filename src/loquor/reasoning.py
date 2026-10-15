"""Local reasoning backends.

The LLM's role in a loquor agent is to interpret evidence and articulate
hypotheses; it never controls protocol state. Deterministic application
logic owns task states, retries, permissions, and the final structured
output, so the collaboration works (and is testable) with or without a
model attached.

- :class:`OllamaReasoner` — talks to a local Ollama instance
  (``http://localhost:11434``) running a small quantized model
  (e.g. ``qwen2.5:1.5b`` or ``llama3.2:3b``). Fully offline.
- :class:`TemplateReasoner` — deterministic text generation used when no
  local model is available (CI, tests, quick demos).

``best_available_reasoner()`` probes Ollama for the requested model and falls
back to templates. Set ``LOQUOR_MODEL`` to use a different local model.
"""

from __future__ import annotations

import logging
import os

import aiohttp

log = logging.getLogger("loquor")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = os.environ.get("LOQUOR_MODEL", "qwen2.5:1.5b")


class Reasoner:
    name = "base"

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float | None = None,
        schema: dict | None = None,
    ) -> str:
        """Return a short completion for the prompt.

        ``schema`` asks for JSON matching a JSON schema (backends that cannot
        enforce it return plain text; callers must validate either way).
        """
        raise NotImplementedError


class TemplateReasoner(Reasoner):
    """Deterministic fallback: echoes the evidence as a readable summary."""

    name = "template"

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        return (
            "Summary (deterministic, no local LLM attached): "
            + " ".join(prompt.split())[:600]
        )


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
            "options": {
                "temperature": self.temperature if temperature is None else temperature
            },
        }
        if schema is not None:
            request["format"] = schema
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/api/generate", json=request
            ) as response:
                response.raise_for_status()
                body = await response.json()
        return (body.get("response") or "").strip()


class FallbackReasoner(Reasoner):
    """Use ``primary``; if it fails at runtime, answer with templates instead.

    Reasoning only articulates evidence, so an LLM failure must never fail
    the task itself.
    """

    def __init__(self, primary: Reasoner):
        self.primary = primary
        self.fallback = TemplateReasoner()
        self.name = primary.name

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        try:
            return await self.primary.complete(
                system, prompt, temperature=temperature, schema=schema
            )
        except Exception as exc:
            log.warning("%s reasoner failed (%s); using template", self.primary.name, exc)
            return await self.fallback.complete(system, prompt)


def _model_matches(wanted: str, installed: str) -> bool:
    # Ollama treats "name" and "name:latest" as the same model.
    def norm(m: str) -> str:
        return m if ":" in m else f"{m}:latest"

    return norm(wanted) == norm(installed)


async def ollama_available(
    base_url: str = DEFAULT_OLLAMA_URL, model: str | None = None
) -> bool:
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
        "Ollama is running but model %r is not pulled (have: %s); "
        "run `ollama pull %s` or set LOQUOR_MODEL. Using template reasoner.",
        model,
        ", ".join(installed) or "none",
        model,
    )
    return False


async def best_available_reasoner(
    model: str = DEFAULT_MODEL, base_url: str = DEFAULT_OLLAMA_URL
) -> Reasoner:
    if await ollama_available(base_url, model=model):
        return FallbackReasoner(OllamaReasoner(model=model, base_url=base_url))
    return TemplateReasoner()
