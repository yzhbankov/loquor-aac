"""Device config: defaults, file, validation, precedence over the profile."""

import json

import pytest

from loquor.config import build_agent
from loquor.protocol import Identity
from loquor.settings import Settings, config_path, loquor_home


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOQUOR_MODEL", raising=False)
    return tmp_path / "home"


PROFILE = {"name": "P", "description": "d", "skills": [{"type": "llm_answer", "capability": "c", "system_prompt": "s"}]}


def test_defaults_when_there_is_no_file(home):
    s = Settings.load()
    assert (s.transport, s.model, s.beacon_interval, s.wire, s.trace) == ("localhost", "qwen2.5:3b", 10.0, "off", True)
    assert config_path() == home / "config.json" and loquor_home() == home


def test_set_save_load_round_trip(home):
    s = Settings.load()
    s.set("transport", "wifi")
    s.set("beacon_interval", "3")
    s.set("trace", "no")
    s.set("interface", "")
    path = s.save()
    assert path == home / "config.json" and json.loads(path.read_text())["beacon_interval"] == 3.0
    again = Settings.load()
    assert (again.transport, again.beacon_interval, again.trace, again.interface) == ("wifi", 3.0, False, None)


@pytest.mark.parametrize("key, value, message", [
    ("transport", "carrier-pigeon", "transport must be one of"),
    ("wire", "loud", "wire must be one of"),
    ("beacon_interval", "soon", "number of seconds"),
    ("beacon_interval", "-1", "must be positive"),
    ("trace", "maybe", "true or false"),
    ("model", "", "non-empty"),
    ("colour", "blue", "unknown key"),
])
def test_invalid_values_are_refused(key, value, message):
    with pytest.raises(ValueError, match=message):
        Settings.load().set(key, value)


def test_broken_file_is_reported_with_its_path(home):
    home.mkdir()
    (home / "config.json").write_text("{not json")
    with pytest.raises(ValueError, match="invalid device config .*config.json"):
        Settings.load()
    (home / "config.json").write_text(json.dumps({"wire": "loud"}))
    with pytest.raises(ValueError, match="wire must be one of"):
        Settings.load()


def test_environment_model_overrides_the_file(home, monkeypatch):
    Settings(model="qwen2.5:3b").save()
    monkeypatch.setenv("LOQUOR_MODEL", "gemma3:1b")
    assert Settings.load().model == "gemma3:1b"


def test_precedence_command_line_over_profile_over_device_config():
    device = Settings(transport="wifi", interface="10.0.0.9", model="qwen2.5:1.5b", beacon_interval=4)
    ident = Identity.generate()

    # device config alone
    a = build_agent(PROFILE, settings=device, identity=ident, transport="localhost")  # (wifi needs a LAN; keep the test offline)
    assert a.reasoner.model == "qwen2.5:1.5b" and a.node.beacon_interval == 4

    # the profile pins model and beacon interval
    pinned = {**PROFILE, "reasoner": {"type": "ollama", "model": "llama3.1:8b"}, "beacon_interval": 2}
    b = build_agent(pinned, settings=device, identity=ident, transport="localhost")
    assert b.reasoner.model == "llama3.1:8b" and b.node.beacon_interval == 2

    # the command line wins over both
    c = build_agent(pinned, settings=device, identity=ident, transport="localhost", model="gemma3:1b", beacon_interval=1)
    assert c.reasoner.model == "gemma3:1b" and c.node.beacon_interval == 1

    # the device's Ollama URL reaches the reasoner unless the profile pins one
    assert a.reasoner.base_url == "http://localhost:11434"
    d = build_agent(PROFILE, settings=Settings(ollama_url="http://10.0.0.2:11434/"), identity=ident)
    assert d.reasoner.base_url == "http://10.0.0.2:11434"
