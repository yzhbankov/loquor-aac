"""Sensors: data in.

A sensor is anything that can produce a :class:`Reading` — a thermometer on
a UART, a GPIO pin, a serial scale, an HTTP endpoint, a random generator for
demos. The agent talks to every one of them through one contract
(:class:`Sensor`); the driver's internals are its own business.

Write one as a class or a plain function (see :mod:`base`), ship it as a
file next to the profile or as an installed module, and list it under
``"sensors"`` in the profile. This package imports nothing else in loquor.
"""

from loquor.sensors.base import FunctionSensor, Reading, Sensor
from loquor.sensors.builtin import Command, Simulated

BUILTIN_SENSORS = {
    "simulated": Simulated,
    "command": Command,
}

__all__ = ["BUILTIN_SENSORS", "Command", "FunctionSensor", "Reading", "Sensor", "Simulated"]
