# Connecting hardware

How to get a real sensor's value into a profile, and a real actuator
under a trigger — one recipe per interface, from no code to a small
class. Every recipe is a sensor or trigger entry for `agent.json`; try it
before going on the air:

```bash
loquor read agent.json                 # reads every sensor once and prints it
loquor fire agent.json pump '{"on": true}'    # fires one trigger, schema-checked
```

| interface | sensor | trigger | Python? |
|---|---|---|---|
| a file: sysfs, procfs, 1-wire | `file` | `file` | no |
| a shell command or script | `command` | `command` | no |
| a device with a web API | `http` | `http` | no |
| a GPIO pin (button, PIR, relay, LED) | `gpio` | `gpio` | no — needs `pip install gpiozero` |
| a serial port (Arduino, scale, GPS) | `serial` | `serial` | no — needs `pip install pyserial` |
| I²C, SPI, a vendor SDK, anything else | a function or a class | a function or a class | a few lines |

Common to all sensors: `name` (unique on the device), `unit`,
`description` (the LLM reads it: say what the number means and its
limits), `interval` (seconds between readings; leave it out to read only
when needed). Common to all triggers: `name`, `description` (what it does
and *when to use it*), `input_schema` (what the LLM must provide;
validated before anything runs).

## Files: sysfs, procfs, 1-wire

Linux exposes many devices as files. `scale` and `offset` turn the raw
number into units; `pattern` picks a value out of longer text (its first
group).

```json
{ "type": "file", "name": "cpu_temp", "path": "/sys/class/thermal/thermal_zone0/temp",
  "scale": 0.001, "unit": "°C", "interval": 10, "description": "CPU temperature of this board." }
```

A DS18B20 thermometer on 1-wire (`dtoverlay=w1-gpio` in `/boot/config.txt`):

```json
{ "type": "file", "name": "water_temp", "path": "/sys/bus/w1/devices/28-0316a2794bff/w1_slave",
  "pattern": "t=(-?\\d+)", "scale": 0.001, "unit": "°C", "interval": 30,
  "description": "Water temperature in the tank." }
```

An LED or any sysfs control, as a trigger. `{on:d}` writes a boolean as
`1` or `0`:

```json
{ "type": "file", "name": "status_led", "path": "/sys/class/leds/ACT/brightness", "template": "{on:d}",
  "description": "Switch the status LED on or off.",
  "input_schema": { "type": "object", "properties": { "on": { "type": "boolean" } }, "required": ["on"] } }
```

## Commands and scripts

The universal adapter: any tool that prints a value is a sensor; any tool
that acts is a trigger. Output that parses as JSON or a number is used as
such.

```json
{ "type": "command", "name": "cpu_temp", "command": "vcgencmd measure_temp | tr -dc '0-9.'", "unit": "°C", "interval": 10 }
{ "type": "command", "name": "humidity", "command": "python3 read_dht22.py --pin 4", "unit": "%", "interval": 60 }
```

A trigger gets its input as JSON on stdin:

```json
{ "type": "command", "name": "pump", "command": "./pump.sh",
  "description": "Run the irrigation pump for some seconds.",
  "input_schema": { "type": "object", "properties": { "seconds": { "type": "integer", "minimum": 1, "maximum": 300 } }, "required": ["seconds"] } }
```

```bash
#!/bin/sh   # pump.sh
seconds=$(jq -r .seconds)
gpioset gpiochip0 17=1; sleep "$seconds"; gpioset gpiochip0 17=0
```

## HTTP: ESP32 boards, smart plugs, weather stations

`field` picks a value out of a JSON answer by a dotted path
(`"sensors.0.value"` indexes a list).

```json
{ "type": "http", "name": "outdoor_temp", "url": "http://192.168.1.40/status", "field": "main.temp",
  "unit": "°C", "interval": 60, "description": "Outdoor temperature from the weather station." }
```

Triggers fill `{placeholders}` in the URL from the input; POST, PUT and
PATCH also send the input as a JSON body. A Shelly plug:

```json
{ "type": "http", "name": "heater", "url": "http://192.168.1.41/relay/0?turn={turn}", "method": "GET",
  "description": "Switch the greenhouse heater.",
  "input_schema": { "type": "object", "properties": { "turn": { "enum": ["on", "off"] } }, "required": ["turn"] } }
```

## GPIO

Needs `gpiozero` (`./install.sh --hw`, or `pip install gpiozero` in the
venv). Pins are BCM numbers. A digital input reads `true` when
active:

```json
{ "type": "gpio", "name": "door_open", "pin": 4, "pull_up": true, "interval": 1,
  "description": "True while the barn door is open." }
```

A digital output takes `{"on": bool}`, and `"seconds"` to switch off again
by itself — which makes a safe pump or valve:

```json
{ "type": "gpio", "name": "valve", "pin": 17, "max_seconds": 120,
  "description": "Open the irrigation valve. Give seconds; it closes by itself." }
```

On a laptop the same profile still loads: the sensor reads as
unavailable, with the reason, and the trigger fails when fired. So one
profile serves the bench and the field.

## Serial

Needs `pyserial` (`./install.sh --hw`, or `pip install pyserial` in the
venv). A sensor reads one line; `request` is written first for devices that answer on demand.

```json
{ "type": "serial", "name": "weight", "port": "/dev/ttyUSB0", "baudrate": 9600, "request": "W\n",
  "pattern": "([\\d.]+) ?kg", "unit": "kg", "interval": 30, "description": "Weight on the hive scale." }
```

A trigger writes one line (`template`, filled from the input); with
`"reply": true` it returns the device's answer:

```json
{ "type": "serial", "name": "motor", "port": "/dev/ttyACM0", "template": "M {speed}", "reply": true,
  "description": "Set the conveyor speed, 0 to stop.",
  "input_schema": { "type": "object", "properties": { "speed": { "type": "integer", "minimum": 0, "maximum": 100 } }, "required": ["speed"] } }
```

## Anything else: a function or a class

When there is a Python library for the device (I²C, SPI, a vendor SDK),
write a function in a file next to the profile. Its return value is the
reading; its docstring is the description.

```python
# sensors.py
import board, adafruit_sht31d

_sht = adafruit_sht31d.SHT31D(board.I2C())

def air_humidity():
    """Relative humidity under the greenhouse roof."""
    return round(_sht.relative_humidity, 1)
```

```json
{ "type": "sensors.py:air_humidity", "name": "humidity", "unit": "%", "interval": 30 }
```

A class when the device needs state, setup or a push loop. Set the
contract as class attributes; the profile can override `name`,
`description`, `unit` and `interval` without an `__init__`:

```python
from loquor import Sensor, Trigger

class Thermometer(Sensor):
    name, unit, interval = "thermometer", "°C", 10
    description = "Air temperature at the device."

    async def read(self):
        value = await asyncio.to_thread(read_my_device)     # blocking I/O off the event loop
        return self.reading(value)                          # or self.failed("why") — never raise

class Pump(Trigger):
    name = "pump"
    description = "Switch the irrigation pump on or off."
    input_schema = {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}

    async def fire(self, input):                            # input already matches input_schema
        await asyncio.to_thread(set_relay, input["on"])
        return {"on": input["on"]}
```

A class that needs its own arguments (a port, a pin) defines `__init__`
and passes the rest on:

```python
class Probe(Sensor):
    unit = "pH"

    def __init__(self, channel: int, **fields):
        super().__init__(**fields)          # name, description, unit, interval from the profile
        self.channel = channel
```

```json
{ "type": "sensors.py:Probe", "name": "ph", "channel": 2, "interval": 60 }
```

The full contracts and the rules (JSON-safe values, never raise from
`read()`, blocking I/O through `asyncio.to_thread`) are in
[DEVELOPMENT.md](DEVELOPMENT.md#add-a-sensor).

## Safety

The LLM can only fire a trigger with an input that matches its schema, so
**the schema is the limit**: give every duration a `maximum`, every
setpoint a range, every mode an `enum`. On a node with real actuators,
let only your controller fire them:

```json
"share": { "min_trust": 1 }
```

and pin the controller's key on the node: `loquor check controller.json`
on the controller prints a `pin entry` line; put it in
`~/.loquor/pinned_keys.json` on the node (`{ <that line> }`) and restart
the node.
