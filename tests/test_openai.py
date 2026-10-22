"""The OpenAI-compatible reasoner (llama.cpp, LM Studio, vLLM) and its device settings."""

import pytest
from aiohttp import web

from loquor import Identity
from loquor.config import build_agent
from loquor.reasoning import OpenAIReasoner, openai_models, reasoner_problem
from loquor.settings import Settings
from loquor.tools.doctor import run_checks


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_API_KEY", raising=False)


@pytest.fixture
async def server():
    seen = []
    state = {"schema": True}

    async def chat(request):
        body = await request.json()
        seen.append((dict(request.headers), body))
        if "response_format" in body and not state["schema"]:
            return web.Response(status=400, text="response_format not supported")
        text = '{"ok": true}' if "response_format" in body else "plain answer"
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": f" {text} "}}]})

    async def models(request):
        return web.json_response({"data": [{"id": "qwen2.5-1.5b-instruct"}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/v1", seen, state
    await runner.cleanup()


async def test_chat_completion_with_and_without_a_schema(server):
    url, seen, state = server
    r = OpenAIReasoner(model="m", base_url=url, api_key="k")
    assert await r.complete("sys", "hi") == "plain answer"
    headers, body = seen[-1]
    assert headers["Authorization"] == "Bearer k" and body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["model"] == "m" and "response_format" not in body
    assert await r.complete("sys", "json please", schema={"type": "object"}) == '{"ok": true}'
    assert seen[-1][1]["response_format"]["json_schema"]["schema"] == {"type": "object"}
    # a server without response_format: asked again without it, and not asked with it again
    state["schema"] = False
    r2 = OpenAIReasoner(base_url=url)
    assert await r2.complete("s", "p", schema={"type": "object"}) == "plain answer" and r2._schema_ok is False
    assert "Authorization" not in seen[-1][0]


async def test_models_and_problems(server):
    url, _, _ = server
    assert await openai_models(url) == ["qwen2.5-1.5b-instruct"]
    assert await openai_models("http://127.0.0.1:1/v1") is None
    assert await reasoner_problem(OpenAIReasoner(base_url=url)) is None
    what, detail, fix = await reasoner_problem(OpenAIReasoner(base_url="http://127.0.0.1:1/v1"))
    assert what == "llm server" and "llama-server" in fix


async def test_device_config_picks_the_backend(server):
    url, _, _ = server
    s = Settings()
    for key, value in (("reasoner", "openai"), ("openai_url", url), ("model", "qwen2.5-1.5b-instruct"), ("api_key", "secret")):
        s.set(key, value)
    s.save()
    loaded = Settings.load()
    assert loaded.reasoner == "openai" and loaded.shown()["api_key"] == "***" and loaded.to_dict()["api_key"] == "secret"
    with pytest.raises(ValueError, match="reasoner must be one of ollama, openai"):
        s.set("reasoner", "gpt")
    agent = build_agent({"name": "A", "description": "d", "brain": {"prompt": "p"}}, settings=loaded,
                        identity=Identity.generate())
    assert isinstance(agent.reasoner, OpenAIReasoner) and agent.reasoner.base_url == url
    assert agent.reasoner.api_key == "secret" and agent.reasoner.model == "qwen2.5-1.5b-instruct"
    checks = {c.name: c for c in await run_checks(loaded, transport="localhost")}
    assert checks["llm server"].ok and "qwen2.5-1.5b-instruct" in checks["llm server"].detail
    loaded.model = "other"
    checks = {c.name: c for c in await run_checks(loaded, transport="localhost")}
    assert checks["model other"].state == "warn"
