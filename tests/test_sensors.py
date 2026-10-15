"""Sensors: the contract, the built-ins, and the hub skills see."""

import asyncio

import pytest

from loquor.agent.sensors import SensorHub
from loquor.sensors import Command, FunctionSensor, Reading, Sensor, Simulated


class Thermometer(Sensor):
    name = "thermometer"
    unit = "°C"

    def __init__(self, values):
        self.values = iter(values)

    async def read(self):
        return self.reading(next(self.values))


class Broken(Sensor):
    name = "broken"

    async def read(self):
        raise OSError("serial port gone")


def test_reading_text_and_dict():
    assert Reading("t", 12.345, "°C").text().startswith("t: 12.3 °C (")
    assert Reading("w", {"temp": 12, "cond": "rain"}).text().startswith("w: temp=12, cond=rain (")
    assert Reading("t", None, error="timeout").text() == "t: unavailable (timeout)"
    d = Reading("t", 1, "x", error="e").to_dict()
    assert d["sensor"] == "t" and d["error"] == "e" and "at" in d


async def test_safe_read_never_raises_and_polling_publishes():
    assert (await Broken().safe_read()).error == "OSError: serial port gone"

    t = Thermometer([1, 2, 3, 4, 5])
    t.interval = 0.01
    got = []
    task = asyncio.create_task(t.run(got.append))
    await asyncio.sleep(0.05)
    task.cancel()
    assert [r.value for r in got[:3]] == [1, 2, 3] and got[0].unit == "°C"

    on_demand = Thermometer([1])
    await asyncio.wait_for(on_demand.run(got.append), 0.1)  # no interval: returns at once


async def test_function_sensor_sync_async_and_reading():
    def humidity():
        """Relative humidity."""
        return 55

    async def pressure():
        return Reading("pressure", 1013, "hPa")

    h = FunctionSensor(humidity, name="humidity", unit="%")
    assert h.description == "Relative humidity."
    assert (await h.read()).value == 55 and (await h.read()).unit == "%"
    p = FunctionSensor(pressure, name="p")
    assert (await p.read()).unit == "hPa"


async def test_simulated_stays_in_range():
    s = Simulated("t", min=0, max=10, unit="u", step=100)
    for _ in range(50):
        r = await s.read()
        assert 0 <= r.value <= 10 and r.unit == "u"


async def test_command_sensor_parses_json_number_text_and_errors():
    assert (await Command("c", "echo '{\"t\": 21}'").read()).value == {"t": 21}
    assert (await Command("c", "echo 3.5").read()).value == 3.5
    assert (await Command("c", "echo hello").read()).value == "hello"
    assert "exit 2" in (await Command("c", "exit 2").read()).error
    assert "timed out" in (await Command("c", "sleep 5", timeout=0.05).read()).error


async def test_hub_latest_read_readings_and_describe():
    polled = Thermometer([20, 21, 22])
    polled.interval = 10
    on_demand = FunctionSensor(lambda: 7, name="count")
    hub = SensorHub([polled, on_demand])
    assert hub.names() == ["thermometer", "count"] and "count" in hub and len(hub) == 2
    assert hub.latest("thermometer") is None

    readings = await hub.readings()  # nothing stored yet: both read fresh
    assert [r.value for r in readings] == [20, 7]
    assert hub.latest("thermometer").value == 20

    readings = await hub.readings(["thermometer", "count"])
    assert [r.value for r in readings] == [20, 7]  # polled: stored; on-demand: fresh (same value)
    assert (await hub.read("thermometer")).value == 21 and hub.latest("thermometer").value == 21
    assert hub.describe(readings).splitlines()[0].startswith("thermometer: 20 °C")
    assert hub.describe([]) == "(no sensors)"

    with pytest.raises(ValueError, match="two sensors named"):
        SensorHub([Thermometer([1]), Thermometer([2])])
    with pytest.raises(ValueError, match="has no name"):
        SensorHub([Sensor()])
