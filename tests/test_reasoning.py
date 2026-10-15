from loquor.reasoning import FallbackReasoner, Reasoner, TemplateReasoner
from loquor.reasoning.ollama import _model_matches


class _Broken(Reasoner):
    name = "broken"

    async def complete(self, system, prompt, **kwargs) -> str:
        raise RuntimeError("model not found")


def test_model_name_matching():
    assert _model_matches("qwen2.5:1.5b", "qwen2.5:1.5b")
    assert _model_matches("llama3", "llama3:latest")
    assert not _model_matches("qwen2.5:1.5b", "gemma3:1b")


async def test_fallback_on_runtime_failure():
    text = await FallbackReasoner(_Broken()).complete("sys", "evidence")
    assert text == await TemplateReasoner().complete("sys", "evidence")
