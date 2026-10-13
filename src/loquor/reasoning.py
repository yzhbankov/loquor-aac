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

``best_available_reasoner()`` probes Ollama and falls back to templates.
"""

from __future__ import annotations

import aiohttp

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:1.5b"


class Reasoner:
    name = "base"

    async def complete(self, system: str, prompt: str) -> str:
        """Return a short natural-language completion for the prompt."""
        raise NotImplementedError


class TemplateReasoner(Reasoner):
    """Deterministic fallback: echoes the evidence as a readable summary."""

    name = "template"

    async def complete(self, system: str, prompt: str) -> str:
        return (
            "Summary (deterministic, no local LLM attached): "
            + " ".join(prompt.split())[:600]
        )


class OllamaReasoner(Reasoner):
    name = "ollama"

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_OLLAMA_URL):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete(self, system: str, prompt: str) -> str:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "system": system,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
            ) as response:
                response.raise_for_status()
                body = await response.json()
        return (body.get("response") or "").strip()


async def ollama_available(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url.rstrip('/')}/api/tags") as response:
                return response.status == 200
    except Exception:
        return False


async def best_available_reasoner(
    model: str = DEFAULT_MODEL, base_url: str = DEFAULT_OLLAMA_URL
) -> Reasoner:
    if await ollama_available(base_url):
        return OllamaReasoner(model=model, base_url=base_url)
    return TemplateReasoner()
