"""The demo's sensor: invents plausible weather.

This is the whole contract a sensor needs: subclass ``Sensor``, set its
``name`` and ``description``, implement ``read()``, return
``self.reading(value)``. A real one would open a serial port or an I²C bus
and read the instrument here — the agent cannot tell the difference.
Listed in the profile as::

    {"type": "weather_sensor.py:RandomWeather", "interval": 5}
"""

from __future__ import annotations

import random

from loquor.sensors import Sensor

CONDITIONS = [  # (max temperature, possible conditions)
    (0, ["snowy", "freezing fog", "icy and clear"]),
    (10, ["rainy", "overcast", "windy and cold"]),
    (20, ["partly cloudy", "light showers", "mild and breezy"]),
    (99, ["sunny", "hot and clear", "humid"]),
]


class RandomWeather(Sensor):
    name = "weather"
    description = "Current outdoor weather at this device: temperature, condition, wind."

    async def read(self):
        temperature = random.randint(-10, 35)
        condition = random.choice(next(opts for limit, opts in CONDITIONS if temperature <= limit))
        return self.reading({
            "temperature_c": temperature,
            "condition": condition,
            "wind_kph": random.randint(0, 40),
        })
