"""The ``loquor`` command: every subcommand short of a real radio or model."""

import json
import re
from pathlib import Path

import pytest

from loquor import __version__
from loquor.cli import main
from loquor.settings import Settings
from loquor.tools.status import status_path, trace_path

DEMO = Path(__file__).parent.parent / "demo" / "weather_and_clothes"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_MODEL", raising=False)
    return tmp_path / "home"


def run_cli(argv, capsys):
    try:
        main(argv)
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if isinstance(exc.code, str):
            print(exc.code)
    out = capsys.readouterr()
    return code, out.out + out.err


def test_version_and_help(capsys):
    assert run_cli(["version"], capsys) == (0, f"loquor {__version__}\n")
    code, out = run_cli([], capsys)
    assert code == 0 and "doctor" in out and "init" in out and "service" in out


def test_config_show_set_reset_and_path(capsys, home):
    code, out = run_cli(["config", "show"], capsys)
    assert code == 0 and "defaults; nothing saved yet" in out
    code, out = run_cli(["config", "set", "transport", "wifi", "beacon_interval", "3"], capsys)
    assert code == 0 and "saved to" in out and Settings.load().transport == "wifi"
    code, out = run_cli(["config", "set", "wire", "loud"], capsys)
    assert code == 2 and "wire must be one of" in out
    code, out = run_cli(["config", "set", "transport"], capsys)
    assert code == 2 and "KEY VALUE" in out
    code, out = run_cli(["config", "path"], capsys)
    assert out.strip() == str(home / "config.json")
    code, out = run_cli(["config", "reset"], capsys)
    assert code == 0 and not (home / "config.json").exists()


def test_doctor_without_llm(capsys):
    code, out = run_cli(["doctor", "--no-llm", "--transport", "localhost"], capsys)
    assert code == 0 and "[  ok  ] python" in out and "transport localhost" in out and "Ready" in out


def test_check_a_demo_profile_and_a_bad_one(capsys, tmp_path):
    code, out = run_cli(["check", str(DEMO / "weather_agent.json"), "--no-llm", "--transport", "localhost"], capsys)
    assert code == 0 and "Weather Forecaster" in out and "sensor weather" in out and "Ready." in out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "B", "description": "d", "skills": [{"type": "nope"}]}))
    code, out = run_cli(["check", str(bad), "--no-llm"], capsys)
    assert code == 1 and "unknown skill type 'nope'" in out
    code, out = run_cli(["check", str(tmp_path / "missing.json"), "--no-llm"], capsys)
    assert code == 1 and "does not exist" in out


def test_init_then_check(capsys, tmp_path):
    target = tmp_path / "greenhouse"
    code, out = run_cli(["init", str(target)], capsys)
    assert code == 0 and "wrote" in out and "loquor check" in out
    code, out = run_cli(["check", str(target / "agent.json"), "--no-llm", "--transport", "localhost"], capsys)
    assert code == 0 and "Greenhouse" in out and "Ready." in out
    code, out = run_cli(["init", str(target)], capsys)
    assert code == 1 and "--force" in out
    node = tmp_path / "shed"
    code, out = run_cli(["init", str(node), "--node"], capsys)
    assert code == 0 and "triggers.py" not in out and "prompt.txt" not in out
    code, out = run_cli(["check", str(node / "agent.json"), "--transport", "localhost"], capsys)  # no --no-llm needed
    assert code == 0 and "node (no LLM)" in out and "Ready." in out


def test_status_and_trace_without_running_agents(capsys, home):
    code, out = run_cli(["status"], capsys)
    assert code == 0 and "no agents running" in out
    code, out = run_cli(["status", "Ghost"], capsys)
    assert code == 1 and "no status for 'Ghost'" in out
    code, out = run_cli(["trace", "Ghost"], capsys)
    assert code == 1 and "no trace" in out

    # a status file and a trace file left by a run
    status_path("Ghost").parent.mkdir(parents=True)
    status_path("Ghost").write_text(json.dumps({
        "name": "Ghost", "uid": "ghost-1", "pid": 1, "updated_at": 0, "model": "m", "capabilities": ["c"],
        "transport": {"type": "MulticastTransport", "where": "127.0.0.1"}, "visibility": "visible",
        "peers": [{"label": "other-2", "handshaken": True, "trust": 0, "last_seen_s": 1.0, "capabilities": ["x"]}],
        "sensors": [{"sensor": "t", "value": 3, "unit": "°C", "age_s": 1.0}],
        "triggers": {"names": ["beep"], "history": []}, "llm": {"calls": 1, "last": []},
        "goals": [{"goal": "g", "done": True, "answers": 1, "outcome": "fine"}],
    }))
    trace_path("Ghost").write_text(json.dumps({"n": 1, "seconds": 0.5, "model": "m", "temperature": 0,
                                               "system": "s", "prompt": "why?", "response": "because"}) + "\n")
    code, out = run_cli(["status", "Ghost"], capsys)
    assert code == 0 and "not running" in out and "other-2" in out and "t: 3 °C" in out and "outcome  : fine" in out
    code, out = run_cli(["status", "--json"], capsys)
    assert code == 0 and json.loads(out)[0]["name"] == "Ghost"
    code, out = run_cli(["trace", "Ghost"], capsys)
    assert code == 0 and "why?" in out and "because" in out and "1 call from" in out
    code, out = run_cli(["trace", "Ghost", "--full", "--last", "1"], capsys)
    assert "system : s" in out
    code, out = run_cli(["trace", str(trace_path("Ghost")), "--json"], capsys)
    assert json.loads(out)[0]["n"] == 1


def test_service_install_writes_a_unit_and_prints_the_next_commands(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    code, out = run_cli(["service", "install", str(DEMO / "weather_agent.json"), "--transport", "wifi"], capsys)
    assert code == 0 and "wrote" in out and "Then:" in out
    unit = re.search(r"wrote (\S+)", out).group(1)
    text = Path(unit).read_text(errors="replace")
    assert Path(unit).exists() and "--transport" in text and "wifi" in text  # plist lists arguments one per element
    code, out = run_cli(["service", "uninstall", str(DEMO / "weather_agent.json")], capsys)
    assert code == 0 and "removed" in out and not Path(unit).exists()


def test_run_with_a_missing_model_fails_with_advice(capsys, tmp_path):
    Settings(ollama_url="http://127.0.0.1:1").save()  # nothing listens there
    code, out = run_cli(["run", str(DEMO / "weather_agent.json"), "--transport", "localhost"], capsys)
    assert code == 1 and "not available in Ollama" in out and "loquor doctor" in out


def test_bare_profile_argument_means_run(capsys, tmp_path):
    Settings(ollama_url="http://127.0.0.1:1").save()
    code, out = run_cli([str(DEMO / "weather_agent.json"), "--transport", "localhost"], capsys)
    assert code == 1 and "not available in Ollama" in out  # it reached `run`
