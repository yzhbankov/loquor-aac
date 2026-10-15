"""Model files (pull/import/resolve) and the llama.cpp reasoner, without a real model."""

import hashlib
import json

import pytest
from aiohttp import web

from loquor import models
from loquor.engine import LlamaCppReasoner

CONTENT = b"GGUF" + bytes(range(256)) * 400


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    return tmp_path


async def serve(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.fixture
def fake_catalog(monkeypatch):
    def install(url, sha256=hashlib.sha256(CONTENT).hexdigest()):
        spec = models.ModelSpec("tiny", "test model", url, sha256, len(CONTENT))
        monkeypatch.setitem(models.CATALOG, "tiny", spec)
        return spec

    return install


def test_resolve_explains_what_to_do(home):
    with pytest.raises(models.ModelError, match="loquor model pull qwen2.5-3b"):
        models.resolve("qwen2.5-3b")
    with pytest.raises(models.ModelError, match="unknown model"):
        models.resolve("no-such-model")
    gguf = home / "my.gguf"
    gguf.write_bytes(CONTENT)
    assert models.resolve(str(gguf)) == gguf


async def test_pull_resumes_and_verifies(home, fake_catalog):
    ranges = []

    async def handler(request):
        start = int(request.headers.get("Range", "bytes=0-")[6:].rstrip("-") or 0)
        ranges.append(start)
        status = 206 if start else 200
        return web.Response(body=CONTENT[start:], status=status)

    runner, base = await serve(handler)
    try:
        fake_catalog(f"{base}/tiny.gguf")
        partial = models.installed_path("tiny").with_suffix(".gguf.part")
        partial.parent.mkdir(parents=True)
        partial.write_bytes(CONTENT[:1000])  # an interrupted earlier download

        path = await models.pull("tiny", progress=False)
    finally:
        await runner.cleanup()
    assert ranges == [1000]
    assert path.read_bytes() == CONTENT
    assert models.resolve("tiny") == path


async def test_pull_rejects_corrupt_download(home, fake_catalog):
    async def handler(request):
        return web.Response(body=b"not the model")

    runner, base = await serve(handler)
    try:
        fake_catalog(f"{base}/tiny.gguf")
        with pytest.raises(models.ModelError, match="checksum mismatch"):
            await models.pull("tiny", progress=False)
    finally:
        await runner.cleanup()
    assert not models.installed_path("tiny").exists()
    assert not models.installed_path("tiny").with_suffix(".gguf.part").exists()


def test_import_recognises_catalog_file_by_checksum(home, fake_catalog):
    fake_catalog("http://unused")
    source = home / "downloaded-by-hand.gguf"
    source.write_bytes(CONTENT)
    name, path = models.import_file(source)
    assert name == "tiny" and path.read_bytes() == CONTENT

    other = home / "custom.gguf"
    other.write_bytes(b"other model")
    assert models.import_file(other)[0] == "custom"
    with pytest.raises(models.ModelError, match="checksum differs"):
        models.import_file(other, name="tiny")


async def test_llama_cpp_reasoner_sends_schema_and_temperature():
    seen = {}

    async def handler(request):
        seen.update(await request.json())
        return web.json_response({"choices": [{"message": {"content": ' {"useful": true} '}}]})

    runner, base = await serve(handler)
    try:
        reasoner = LlamaCppReasoner(base)
        schema = {"type": "object", "properties": {"useful": {"type": "boolean"}}}
        text = await reasoner.complete("sys", "prompt", temperature=0, schema=schema)
    finally:
        await runner.cleanup()
    assert json.loads(text) == {"useful": True}
    assert seen["temperature"] == 0
    assert seen["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "prompt"},
    ]
    assert seen["response_format"]["json_schema"]["schema"] == schema
