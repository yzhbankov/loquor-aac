"""Local reasoning backends.

The LLM articulates answers and decisions inside skills; it never controls
protocol state. Message format, addressing, retries and deduplication are
fixed code, which is why the whole system is testable without a model.

- :class:`OllamaReasoner` — a model served by a local Ollama
- :class:`OpenAIReasoner` — a model behind an OpenAI-compatible API
  (llama.cpp ``llama-server``, LM Studio, vLLM, LocalAI)
- :class:`TemplateReasoner` — deterministic text, for runs without an LLM
- :class:`FallbackReasoner` — use one reasoner; on failure, templates
- :class:`TracingReasoner` — wrap any reasoner; record every call for debugging
- :class:`NoReasoner` — no LLM: sensor nodes that only measure and report
"""

from loquor.reasoning.base import Reasoner
from loquor.reasoning.none import NoReasoner
from loquor.reasoning.ollama import DEFAULT_MODEL, OllamaReasoner, ollama_available
from loquor.reasoning.openai import OpenAIReasoner, openai_models
from loquor.reasoning.template import FallbackReasoner, TemplateReasoner
from loquor.reasoning.trace import TracingReasoner, read_trace

__all__ = [
    "reasoner_problem",
    "DEFAULT_MODEL",
    "FallbackReasoner",
    "NoReasoner",
    "OllamaReasoner",
    "OpenAIReasoner",
    "Reasoner",
    "TemplateReasoner",
    "TracingReasoner",
    "ollama_available",
    "openai_models",
    "read_trace",
]


async def reasoner_problem(reasoner: Reasoner) -> tuple[str, str, str] | None:
    """Why this reasoner cannot run here, as (check name, problem, fix); None if it can.

    A model that an OpenAI-compatible server does not list is not a problem:
    llama.cpp serves whatever it loaded under any name.
    """
    reasoner = getattr(reasoner, "inner", reasoner)
    if isinstance(reasoner, OllamaReasoner):
        if not await ollama_available(reasoner.base_url):
            return "ollama", f"not reachable at {reasoner.base_url}", "start it: ollama serve (or brew services start ollama)"
        if not await ollama_available(reasoner.base_url, model=reasoner.model):
            return f"model {reasoner.model}", "not pulled", f"ollama pull {reasoner.model}"
    elif isinstance(reasoner, OpenAIReasoner):
        if await openai_models(reasoner.base_url, reasoner.api_key) is None:
            return ("llm server", f"no OpenAI-compatible server at {reasoner.base_url}",
                    "start it (llama-server -m model.gguf --port 8080), or: loquor config set openai_url http://host:port/v1")
    return None
