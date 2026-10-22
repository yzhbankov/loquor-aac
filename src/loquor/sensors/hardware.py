"""Sensors on a GPIO pin or a serial port.

Both need a library that only makes sense on such a device, so they are an
optional install: ``pip install gpiozero pyserial`` (or ``pip install -e ".[hw]"``
in the repository).
The device is opened on the first read, not when the profile loads, so a
profile with a GPIO sensor still checks on a laptop — the sensor just
reads as unavailable there, with the reason.

- :class:`Gpio` — a digital input (button, reed switch, PIR, rain sensor):
  ``true`` when active.
- :class:`Serial` — one line from a serial device (an Arduino, a scale, a
  GPS); optionally write a request first.
"""

from __future__ import annotations

import asyncio
import importlib
import re
from typing import Any

from loquor.sensors.base import Reading, Sensor
from loquor.sensors.builtin import parse_output, scaled


def need(module: str, package: str) -> Any:
    """Import an optional hardware library, or fail with the command that installs it."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(f"{package} is not installed: pip install {package}") from exc


class Gpio(Sensor):
    """A digital input on BCM ``pin``: ``true`` when active (high, or low with ``pull_up``)."""

    def __init__(self, name: str, pin: int, pull_up: bool = False, interval: float | None = None,
                 description: str = ""):
        super().__init__(name=name, interval=interval, description=description or f"digital input on GPIO {pin}")
        self.pin, self.pull_up = pin, pull_up
        self._device = None

    async def read(self) -> Reading:
        try:
            if self._device is None:
                gpiozero = need("gpiozero", "gpiozero")
                self._device = gpiozero.DigitalInputDevice(self.pin, pull_up=self.pull_up)
            return self.reading(bool(self._device.is_active))
        except Exception as exc:  # not a Pi, pin busy, library missing: no data, not a crash
            self._device = None
            return self.failed(f"GPIO {self.pin}: {exc}")


class Serial(Sensor):
    """One line from ``port``. ``request`` is written first (``"R\\n"``) for devices that answer
    on demand; ``pattern``, ``scale`` and ``offset`` work as for the ``file`` sensor."""

    def __init__(self, name: str, port: str, baudrate: int = 9600, unit: str = "", interval: float | None = None,
                 description: str = "", request: str | None = None, pattern: str | None = None,
                 scale: float = 1.0, offset: float = 0.0, timeout: float = 2.0):
        super().__init__(name=name, unit=unit, interval=interval, description=description or f"line from {port}")
        self.port, self.baudrate, self.request, self.timeout = port, baudrate, request, timeout
        self.pattern = re.compile(pattern) if pattern else None
        self.scale, self.offset = scale, offset
        self._serial = None

    def _read_line(self) -> str:
        if self._serial is None:
            serial = need("serial", "pyserial")
            self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        if self.request:
            self._serial.reset_input_buffer()
            self._serial.write(self.request.encode())
        return self._serial.readline().decode(errors="replace").strip()

    async def read(self) -> Reading:
        try:
            line = await asyncio.to_thread(self._read_line)
        except Exception as exc:
            self._serial = None  # reopen next time: the device may have been replugged
            return self.failed(f"{self.port}: {exc}")
        if not line:
            return self.failed(f"no line from {self.port} within {self.timeout:g} s")
        if self.pattern:
            match = self.pattern.search(line)
            if not match:
                return self.failed(f"pattern {self.pattern.pattern!r} not found in {line[:60]!r}")
            line = match.group(1) if match.groups() else match.group(0)
        return self.reading(scaled(parse_output(line), self.scale, self.offset))
