"""Local reasoning backends.

The LLM articulates answers and decisions inside skills; it never controls
protocol state. Message format, addressing, retries and deduplication are
fixed code, which is why the whole system is testable without a model.

- :class:`OllamaReasoner` — a model served by a local Ollama
- :class:`TemplateReasoner` — deterministic text, for runs without an LLM
- :class:`FallbackReasoner` — use one reasoner; on failure, templates
- :class:`TracingReasoner` — wrap any reasoner; record every call for debugging
"""

from loquor.reasoning.base import Reasoner
from loquor.reasoning.ollama import DEFAULT_MODEL, OllamaReasoner, ollama_available
from loquor.reasoning.template import FallbackReasoner, TemplateReasoner
from loquor.reasoning.trace import TracingReasoner, read_trace

__all__ = [
    "DEFAULT_MODEL",
    "FallbackReasoner",
    "OllamaReasoner",
    "Reasoner",
    "TemplateReasoner",
    "TracingReasoner",
    "ollama_available",
    "read_trace",
]
