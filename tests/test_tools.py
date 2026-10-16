"""The operator tools: tracing, status, doctor, check, init, service."""

import asyncio
import json
import plistlib
from pathlib import Path

import pytest

from loquor import Agent, Identity, Reasoner, Sensor, Trigger
from loquor.agent.skills import Goal, LlmAnswer
from loquor.reasoning import TemplateReasoner, TracingReasoner, read_trace
from loquor.settings import Settings
from loquor.tools import scaffold, service
from loquor.tools.check import check_profile
from loquor.tools.doctor import FAIL, OK, WARN, Check, check_transport, format_checks, model_ram_gb, run_checks
from loquor.tools.status import StatusWriter, format_status, read_status, running, snapshot, status_path
from loquor.transports import MemoryHub


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_MODEL", raising=False)
    return tmp_path / "home"


class Thermo(Sensor):
    name, unit, interval, description = "thermo", "°C", 0.05, "Temperature."

    async def read(self):
        return self.reading(21.5)


class Beep(Trigger):
    name, description = "beep", "Beep once."
    input_schema = {"type": "object", "properties": {"times": {"type": "integer"}}, "required": ["times"]}

    async def fire(self, input):
        if input["times"] < 0:
            raise RuntimeError("speaker unplugged")
        return {"beeped": input["times"]}


class Failing(Reasoner):
    name = "failing"

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        raise RuntimeError("model on fire")


# ------------------------------------------------------------------ trace


async def test_tracing_reasoner_records_every_call_to_file_and_memory(tmp_path):
    path = tmp_path / "t.trace.jsonl"
    r = TracingReasoner(TemplateReasoner(), path, keep=2)
    assert r.name == "template" and r.model is None
    for i in range(3):
        out = await r.complete("sys", f"prompt {i}", temperature=0.1, schema={"type": "object"})
        assert out.startswith("Summary")
    assert r.count == 3 and [c["n"] for c in r.calls] == [2, 3]  # ring keeps the last two
    calls = read_trace(path)
    assert [c["n"] for c in calls] == [1, 2, 3]
    assert calls[0]["prompt"] == "prompt 0" and calls[0]["schema"] == {"type": "object"} and calls[0]["seconds"] >= 0
    assert read_trace(path, last=1)[0]["n"] == 3
    assert read_trace(tmp_path / "missing.jsonl") == []

    failing = TracingReasoner(Failing(), path)  # a new run truncates the file
    with pytest.raises(RuntimeError):
        await failing.complete("s", "p")
    (only,) = read_trace(path)
    assert only["error"] == "RuntimeError: model on fire" and "response" not in only


# ----------------------------------------------------------------- status


async def test_snapshot_and_status_file_of_a_running_agent(home):
    hub = MemoryHub()
    a = Agent(name="Station A", description="d", skills=[LlmAnswer("temp", "You report.", sensors=["thermo"])],
              reasoner=TracingReasoner(TemplateReasoner()), transport=hub.transport(), identity=Identity.generate(),
              beacon_interval=0.05, sensors=[Thermo()], triggers=[Beep()])
    b = Agent(name="Asker B", description="d", skills=[Goal("know the temperature", "You ask.")],
              reasoner=TemplateReasoner(), transport=hub.transport(), identity=Identity.generate(), beacon_interval=0.05)
    await a.start()
    await b.start()
    writer = StatusWriter(a, interval=0.05, extra={"profile": "a.json"})
    writer.start()
    try:
        await asyncio.sleep(0.4)
        await a.triggers.fire("beep", {"times": 2})
        with pytest.raises(Exception, match="speaker unplugged"):
            await a.triggers.fire("beep", {"times": -1})
        data = snapshot(a)
        assert data["name"] == "Station A" and data["capabilities"] == ["temp"] and data["model"] is None
        (peer,) = data["peers"]
        assert peer["name"] == "Asker B" and peer["handshaken"] and peer["last_seen_s"] < 5
        (thermo,) = data["sensors"]
        assert thermo["sensor"] == "thermo" and thermo["value"] == 21.5 and thermo["unit"] == "°C"
        assert data["triggers"]["names"] == ["beep"]
        fired, = [h for h in data["triggers"]["history"] if "result" in h]
        assert fired["result"] == {"beeped": 2} and data["triggers"]["history"][-1]["error"]
        assert data["llm"]["calls"] == 0 and data["goals"] == []

        await asyncio.sleep(0.1)
        saved = read_status("Station A")
        assert saved and saved["alive"] and saved["profile"] == "a.json" and saved["status_file"] == str(status_path("Station A"))
        assert [r["name"] for r in running()] == ["Station A"]
        page = format_status(saved)
        assert "running" in page and "asker-b-" in page and "thermo: 21.5 °C" in page and "beep" in page

        # the goal shows up on the asker's snapshot
        gdata = snapshot(b)
        assert gdata["goals"][0]["goal"] == "know the temperature" and gdata["goals"][0]["done"] is False
    finally:
        await writer.stop()
        await a.stop()
        await b.stop()
    assert not status_path("Station A").exists()  # removed on stop
    assert read_status("Station A") is None and read_status("nobody") is None


def test_stale_status_file_is_reported_as_not_running(home):
    path = status_path("Old")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "Old", "uid": "old-1", "pid": 2**22 - 1, "updated_at": 0,
                                "peers": [], "sensors": [], "triggers": {}, "llm": {}, "goals": []}))
    data = read_status("Old")
    assert data["alive"] is False and "not running" in format_status(data)


# ----------------------------------------------------------------- doctor


async def test_doctor_without_llm_and_with_a_broken_ollama(home, monkeypatch):
    checks = await run_checks(Settings(), llm=False)
    names = [c.name for c in checks]
    assert names[:4] == ["python", "loquor", "dependencies", "state directory"]
    assert all(c.state == OK for c in checks if c.name != "device config")
    assert next(c for c in checks if c.name == "device config").state == WARN  # no file yet
    assert "transport localhost" in names and all(c.ok for c in checks)

    Settings(ollama_url="http://127.0.0.1:1").save()  # nothing listens there
    checks = await run_checks(Settings.load())
    ollama = next(c for c in checks if c.name == "ollama")
    assert ollama.state == FAIL and "not reachable" in ollama.detail and ollama.fix
    assert "problem" in format_checks(checks)


def test_transport_checks_and_model_sizes(monkeypatch):
    assert check_transport("localhost")[0].state == OK
    monkeypatch.setattr("loquor.transports.multicast.primary_ip", lambda: "127.0.0.1")
    wifi = check_transport("wifi")[0]
    assert wifi.state == FAIL and "join a Wi-Fi network" in wifi.fix
    monkeypatch.setattr("loquor.transports.multicast.primary_ip", lambda: "10.1.2.3")
    assert "10.1.2.3" in check_transport("wifi")[0].detail
    assert check_transport("my.pkg:Link")[0].state == WARN
    assert model_ram_gb("qwen2.5:3b") == 6 and model_ram_gb("llama3.1:8b") == 12 and model_ram_gb("mystery") is None
    page = format_checks([Check("a", OK, "fine"), Check("b", WARN, "hm", "do x")])
    assert "fix: do x" in page and "Ready, with 1 thing to know" in page


# ------------------------------------------------------------------ check


async def test_check_profile_reads_sensors_and_validates_triggers(tmp_path):
    (tmp_path / "parts.py").write_text(
        "from loquor.sensors import Sensor\nfrom loquor.triggers import Trigger\n"
        "class Good(Sensor):\n    name='good'\n    async def read(self): return self.reading(1)\n"
        "class Broken(Sensor):\n    name='broken'\n    async def read(self): return self.failed('no device')\n"
        "class Weird(Sensor):\n    name='weird'\n    async def read(self): return self.reading(object())\n"
        "class NoDesc(Trigger):\n    name='mute'\n    input_schema={'type':'object'}\n    async def fire(self, i): return {}\n"
        "class BadSchema(Trigger):\n    name='bad'\n    description='x'\n    input_schema={'type':'nonsense'}\n    async def fire(self, i): return {}\n"
    )
    profile = tmp_path / "a.json"
    profile.write_text(json.dumps({
        "name": "Checked", "description": "d", "reasoner": {"type": "template"},
        "sensors": [{"type": "parts.py:Good"}, {"type": "parts.py:Broken"}, {"type": "parts.py:Weird"}],
        "triggers": [{"type": "parts.py:NoDesc"}, {"type": "parts.py:BadSchema"}, {"type": "console"}],
        "skills": [{"type": "sensor_report"}],
    }))
    lines, checks = await check_profile(profile, Settings(), transport="localhost")
    assert lines[0].startswith("Checked — d") and any("SensorReport" in line for line in lines)
    by_name = {c.name: c for c in checks}
    assert by_name["profile"].state == OK
    assert by_name["sensor good"].state == OK and by_name["sensor broken"].state == WARN
    assert by_name["sensor weird"].state == FAIL and "JSON-safe" in by_name["sensor weird"].detail
    assert by_name["trigger mute"].state == FAIL and by_name["trigger bad"].state == FAIL and by_name["trigger print"].state == OK
    assert by_name["reasoner"].state == OK and by_name["transport localhost"].state == OK

    profile.write_text(json.dumps({"name": "X", "description": "d", "skills": [{"type": "llm_answer", "nope": 1}]}))
    _, checks = await check_profile(profile, Settings(), llm=False)
    assert checks[-1].name == "parts" and checks[-1].state == FAIL and "invalid skill" in checks[-1].detail

    profile.write_text("{oops")
    _, checks = await check_profile(profile, Settings(), llm=False)
    assert checks == [checks[0]] and checks[0].state == FAIL and "not valid JSON" in checks[0].detail


# ------------------------------------------------------------------- init


async def test_init_writes_a_runnable_agent(tmp_path):
    written = scaffold.init(tmp_path / "farm_bot")
    assert [p.name for p in written] == ["agent.json", "prompt.txt", "sensors.py", "triggers.py", "README.md"]
    profile = tmp_path / "farm_bot" / "agent.json"
    data = json.loads(profile.read_text())
    assert data["name"] == "Farm Bot" and data["skills"][0]["capability"] == "farm_bot_status"
    lines, checks = await check_profile(profile, Settings(), llm=False, transport="localhost")
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert any("load: " in c.detail for c in checks) and any("temperature: " in c.detail for c in checks)
    assert "Farm Bot" in (tmp_path / "farm_bot" / "prompt.txt").read_text()
    with pytest.raises(FileExistsError, match="use --force"):
        scaffold.init(tmp_path / "farm_bot")
    scaffold.init(tmp_path / "farm_bot", name="Barn", force=True)
    assert json.loads(profile.read_text())["name"] == "Barn"


# ---------------------------------------------------------------- service


def test_service_units_for_linux_and_macos(tmp_path, monkeypatch, home):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    profile = tmp_path / "agent.json"
    profile.write_text("{}")
    linux = service.install(profile, "Soil Station", ["--transport", "wifi"], system="Linux", python="/venv/bin/python")
    assert linux.name == "loquor-soil-station" and linux.unit_path == tmp_path / ".config/systemd/user/loquor-soil-station.service"
    unit = linux.unit_path.read_text()
    assert f"ExecStart=/venv/bin/python -m loquor run {profile} --transport wifi" in unit
    assert f"Environment=LOQUOR_HOME={home}" in unit and "Restart=on-failure" in unit and "WantedBy=default.target" in unit
    assert linux.commands[1] == "systemctl --user enable --now loquor-soil-station"
    assert service.uninstall("Soil Station", system="Linux").commands[0].startswith("systemctl --user disable --now")

    mac = service.install(profile, "Soil Station", system="Darwin", python="/venv/bin/python")
    assert mac.name == "com.loquor.soil-station" and mac.unit_path.name == "com.loquor.soil-station.plist"
    plist = plistlib.loads(mac.unit_path.read_bytes())
    assert plist["ProgramArguments"] == ["/venv/bin/python", "-m", "loquor", "run", str(profile)]
    assert plist["KeepAlive"] is True and plist["StandardOutPath"].endswith("log/loquor-soil-station.log")
    assert mac.commands[0].startswith("launchctl bootstrap gui/")
    assert service.uninstall("Soil Station", system="Darwin").commands[0].startswith("launchctl bootout")

    with pytest.raises(OSError, match="no service manager"):
        service.install(profile, "x", system="Windows")
