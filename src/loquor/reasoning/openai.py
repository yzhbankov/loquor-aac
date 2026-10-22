"""A model behind an OpenAI-compatible API, served on this device or the LAN.

``llama.cpp``'s ``llama-server``, LM Studio, vLLM, LocalAI and Ollama's own
``/v1`` endpoint all speak it, so this one backend covers the servers
people run on small boards and workstations. Nothing leaves the local
network unless ``base_url`` points elsewhere.

JSON schemas are passed as ``response_format`` (``json_schema``); a server
that rejects that gets the request again without it, and the caller
validates the reply as it always does.
"""

from __future__ import annotations

import logging
import os

import aiohttp

from loquor.reasoning.base import Reasoner

log = logging.getLogger("loquor")

DEFAULT_OPENAI_URL = "http://localhost:8080/v1"


class OpenAIReasoner(Reasoner):
    name = "openai"

    def __init__(self, model: str = "local", base_url: str = DEFAULT_OPENAI_URL, api_key: str | None = None,
                 temperature: float = 0.2, timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("LOQUOR_API_KEY") or None
        self.temperature = temperature
        self.timeout = timeout
        self._schema_ok = True  # until the server says it cannot do response_format

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": self.temperature if temperature is None else temperature,
            "stream": False,
        }
        if schema is not None and self._schema_ok:
            request["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "reply", "schema": schema, "strict": False}}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            async with session.post(f"{self.base_url}/chat/completions", json=request) as response:
                if response.status == 400 and "response_format" in request:
                    log.info("%s does not take response_format; asking for JSON in the prompt only", self.base_url)
                    self._schema_ok = False
                    return await self.complete(system, prompt, temperature=temperature, schema=schema)
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status} from {self.base_url}: {(await response.text())[:200]}")
                body = await response.json()
        try:
            return (body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected answer from {self.base_url}: {str(body)[:200]}") from exc


async def openai_models(base_url: str = DEFAULT_OPENAI_URL, api_key: str | None = None) -> list[str] | None:
    """The model ids the server lists, or None if it is not reachable."""
    api_key = api_key or os.environ.get("LOQUOR_API_KEY")
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(f"{base_url.rstrip('/')}/models") as response:
                if response.status != 200:
                    return None
                body = await response.json()
    except Exception:
        return None
    return [str(m.get("id", "")) for m in body.get("data", []) if isinstance(m, dict)]
