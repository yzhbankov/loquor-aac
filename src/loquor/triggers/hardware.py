"""Triggers on a GPIO pin or a serial port.

Optional install, like the sensors: ``pip install gpiozero pyserial`` (or
``pip install -e ".[hw]"`` in the repository). The device is opened on the first fire, so a profile
still checks on a laptop; firing there fails with the reason.

- :class:`Gpio` — a digital output (relay, LED, buzzer, valve): on, off,
  or on for some seconds.
- :class:`Serial` — writes a line to a serial device (an Arduino, a motor
  controller), optionally reads one line back.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

from loquor.triggers.base import Trigger, TriggerError


def need(module: str, package: str) -> Any:
    """Import an optional hardware library, or fail with the command that installs it."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise TriggerError(f"{package} is not installed: pip install {package}") from exc


class Gpio(Trigger):
    """A digital output on BCM ``pin``. Input ``{"on": bool, "seconds"?: number}``: with
    ``seconds`` it switches on, waits, and switches off again."""

    def __init__(self, name: str, pin: int, description: str = "", active_high: bool = True,
                 max_seconds: float = 600):
        super().__init__(name=name, description=description or f"switch GPIO {pin} on or off", input_schema={
            "type": "object",
            "properties": {"on": {"type": "boolean"},
                           "seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": max_seconds,
                                       "description": "switch off again after this long"}},
            "required": ["on"],
        })
        self.pin, self.active_high = pin, active_high
        self._device = None

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        if self._device is None:
            gpiozero = need("gpiozero", "gpiozero")
            try:
                self._device = gpiozero.DigitalOutputDevice(self.pin, active_high=self.active_high)
            except Exception as exc:
                raise TriggerError(f"GPIO {self.pin}: {exc}") from exc
        if input["on"] and input.get("seconds"):
            self._device.on()
            await asyncio.sleep(float(input["seconds"]))
            self._device.off()
            return {"on": False, "was_on_for_s": input["seconds"]}
        self._device.on() if input["on"] else self._device.off()
        return {"on": bool(input["on"])}


class Serial(Trigger):
    """Writes ``template`` filled from the input (default: the input's ``text``) and a newline
    to ``port``; with ``reply`` it reads one line back as the result."""

    def __init__(self, name: str, port: str, baudrate: int = 9600, description: str = "",
                 input_schema: dict[str, Any] | None = None, template: str = "{text}", reply: bool = False,
                 timeout: float = 2.0):
        super().__init__(name=name, description=description or f"send a line to {port}",
                         input_schema=input_schema or {"type": "object", "properties": {"text": {"type": "string"}},
                                                       "required": ["text"]})
        self.port, self.baudrate, self.template, self.reply, self.timeout = port, baudrate, template, reply, timeout
        self._serial = None

    def _write(self, line: str) -> str:
        if self._serial is None:
            serial = need("serial", "pyserial")
            self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        self._serial.write((line + "\n").encode())
        self._serial.flush()
        return self._serial.readline().decode(errors="replace").strip() if self.reply else ""

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            line = self.template.format(**input)
        except (KeyError, IndexError, ValueError) as exc:
            raise TriggerError(f"template {self.template!r} does not fit the input: {exc}") from exc
        try:
            answer = await asyncio.to_thread(self._write, line)
        except TriggerError:
            raise
        except Exception as exc:
            self._serial = None
            raise TriggerError(f"{self.port}: {exc}") from exc
        return {"sent": line, **({"reply": answer} if self.reply else {})}
