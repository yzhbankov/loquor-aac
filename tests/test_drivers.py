"""Built-in hardware drivers: file, http, gpio, serial — sensors and triggers."""

import sys
import types

import pytest
from aiohttp import web

from loquor.sensors import File as FileSensor
from loquor.sensors import Gpio as GpioSensor
from loquor.sensors import Http as HttpSensor
from loquor.sensors import Serial as SerialSensor
from loquor.triggers import File as FileTrigger
from loquor.triggers import Gpio as GpioTrigger
from loquor.triggers import Http as HttpTrigger
from loquor.triggers import Serial as SerialTrigger
from loquor.triggers import TriggerError

# ------------------------------------------------------------------- file


async def test_file_sensor_reads_sysfs_style_values(tmp_path):
    temp = tmp_path / "temp"
    temp.write_text("48312\n")
    reading = await FileSensor("cpu", str(temp), unit="°C", scale=0.001).read()
    assert reading.value == 48.312 and reading.unit == "°C"
    w1 = tmp_path / "w1_slave"
    w1.write_text("72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=23125\n")
    assert (await FileSensor("probe", str(w1), pattern=r"t=(-?\d+)", scale=0.001).read()).value == 23.125
    assert (await FileSensor("probe", str(w1), pattern=r"t=(x)").read()).error.startswith("pattern")
    missing = await FileSensor("gone", str(tmp_path / "nope")).read()
    assert missing.error.startswith("cannot read")


async def test_file_trigger_writes_a_template(tmp_path):
    led = tmp_path / "brightness"
    lamp = FileTrigger("lamp", str(led), template="{on:d}", description="LED",
                       input_schema={"type": "object", "properties": {"on": {"type": "boolean"}}})
    assert await lamp.fire({"on": True}) == {"wrote": "1"} and led.read_text() == "1"
    await lamp.fire({"on": False})
    assert led.read_text() == "0"
    log = FileTrigger("log", str(tmp_path / "log.txt"), template="{value}", append=True)
    await log.fire({"value": "a"})
    await log.fire({"value": "b"})
    assert (tmp_path / "log.txt").read_text() == "a\nb\n"
    with pytest.raises(TriggerError, match="does not fit"):
        await lamp.fire({"value": 1})


# ------------------------------------------------------------------- http


@pytest.fixture
async def device():
    calls = []

    async def status(request):
        return web.json_response({"main": {"temp": 21.5}, "relays": [{"on": True}]})

    async def text(request):
        return web.Response(text="17.25")

    async def relay(request):
        body = await request.json() if request.can_read_body else None
        calls.append((request.method, request.path, dict(request.query), body))
        return web.json_response({"ok": True})

    async def broken(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_get("/status", status)
    app.router.add_get("/text", text)
    app.router.add_route("*", "/relay/{n}", relay)
    app.router.add_get("/broken", broken)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", calls
    await runner.cleanup()


async def test_http_sensor_picks_a_field(device):
    base, _ = device
    assert (await HttpSensor("t", f"{base}/status", field="main.temp").read()).value == 21.5
    assert (await HttpSensor("r", f"{base}/status", field="relays.0.on").read()).value is True
    assert (await HttpSensor("x", f"{base}/text", scale=2).read()).value == 34.5
    assert (await HttpSensor("t", f"{base}/status", field="main.nope").read()).error.startswith("no 'main.nope'")
    assert (await HttpSensor("b", f"{base}/broken").read()).error.startswith("HTTP 500")
    assert (await HttpSensor("d", "http://127.0.0.1:9/", timeout=1).read()).error


async def test_http_trigger_fills_the_url_and_posts_the_input(device):
    base, calls = device
    plug = HttpTrigger("plug", f"{base}/relay/0?turn={{turn}}", method="GET")
    assert await plug.fire({"turn": "on"}) == {"ok": True}
    board = HttpTrigger("board", f"{base}/relay/{{n}}")
    await board.fire({"n": 2, "on": True})
    assert calls == [("GET", "/relay/0", {"turn": "on"}, None), ("POST", "/relay/2", {}, {"n": 2, "on": True})]
    with pytest.raises(TriggerError, match="HTTP 500"):
        await HttpTrigger("b", f"{base}/broken", method="GET").fire({})
    with pytest.raises(TriggerError, match="does not fit"):
        await plug.fire({})


# ------------------------------------------------------- gpio and serial


@pytest.fixture
def fake_hw(monkeypatch):
    pins = {}

    class Input:
        def __init__(self, pin, pull_up=False):
            self.pin = pin

        @property
        def is_active(self):
            return pins.get(self.pin, False)

    class Output:
        def __init__(self, pin, active_high=True):
            self.pin = pin

        def on(self):
            pins[self.pin] = True
            pins.setdefault("log", []).append((self.pin, "on"))

        def off(self):
            pins[self.pin] = False
            pins.setdefault("log", []).append((self.pin, "off"))

    written = []

    class Port:
        def __init__(self, port, baudrate, timeout):
            self.port = port

        def write(self, data):
            written.append(data)

        def flush(self):
            pass

        def reset_input_buffer(self):
            pass

        def readline(self):
            return b"W:12.5kg\r\n"

    monkeypatch.setitem(sys.modules, "gpiozero", types.SimpleNamespace(DigitalInputDevice=Input, DigitalOutputDevice=Output))
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Port))
    return pins, written


async def test_gpio_input_and_output(fake_hw):
    pins, _ = fake_hw
    button = GpioSensor("door", pin=4)
    assert (await button.read()).value is False
    pins[4] = True
    assert (await button.read()).value is True
    relay = GpioTrigger("pump", pin=17)
    assert await relay.fire({"on": True}) == {"on": True} and pins[17] is True
    assert await relay.fire({"on": True, "seconds": 0.01}) == {"on": False, "was_on_for_s": 0.01}
    assert pins["log"][-2:] == [(17, "on"), (17, "off")]
    assert relay.input_schema["required"] == ["on"]


async def test_serial_read_and_write(fake_hw):
    _, written = fake_hw
    scale = SerialSensor("weight", "/dev/ttyUSB0", request="R\n", pattern=r"W:([\d.]+)", unit="kg")
    assert (await scale.read()).value == 12.5 and written == [b"R\n"]
    motor = SerialTrigger("motor", "/dev/ttyACM0", template="M {speed}", reply=True,
                          input_schema={"type": "object", "properties": {"speed": {"type": "integer"}}})
    assert await motor.fire({"speed": 40}) == {"sent": "M 40", "reply": "W:12.5kg"} and written[-1] == b"M 40\n"


async def test_missing_hardware_library_says_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "gpiozero", None)  # import fails
    monkeypatch.setitem(sys.modules, "serial", None)
    reading = await GpioSensor("door", pin=4).read()
    assert "pip install gpiozero" in reading.error
    assert "pip install pyserial" in (await SerialSensor("s", "/dev/x").read()).error
    with pytest.raises(TriggerError, match="pip install gpiozero"):
        await GpioTrigger("pump", pin=17).fire({"on": True})
