"""The chair's sensor: vitals plus the patient's answer to "how do you feel?"

Stand-in for the chair's thermometer, pulse oximeter and cuff, and for the
microphone/keypad the patient answers on. One reading is one dict so the
LLM sees the whole picture at once. A ``scenario`` keeps a chair's patient
consistent between readings (vitals drift a little; the complaint stays).

    {"type": "patient_simulator.py:PatientMonitor", "scenario": "chest_pain"}
"""

from __future__ import annotations

import random

from loquor.sensors import Sensor

# ranges: temperature °C, heart rate bpm, systolic, diastolic, SpO2 %
SCENARIOS = {
    "chest_pain": {
        "temperature_c": (36.8, 37.4), "heart_rate_bpm": (105, 125), "systolic": (150, 170),
        "diastolic": (90, 100), "spo2": (93, 96),
        "complaints": [
            "Crushing pain in the middle of my chest for twenty minutes, it goes into my left arm and I feel sweaty.",
            "Heavy pressure on my chest since I climbed the stairs, I feel sick and dizzy.",
        ],
    },
    "breathing": {
        "temperature_c": (37.6, 38.3), "heart_rate_bpm": (110, 130), "systolic": (100, 115),
        "diastolic": (60, 70), "spo2": (86, 91),
        "complaints": [
            "I can't catch my breath, it has been getting worse all morning.",
            "Wheezing and short of breath, my inhaler did not help.",
        ],
    },
    "flu": {
        "temperature_c": (38.4, 39.5), "heart_rate_bpm": (90, 105), "systolic": (110, 125),
        "diastolic": (70, 80), "spo2": (96, 98),
        "complaints": [
            "Fever and body aches since yesterday, I feel exhausted but I can walk.",
            "Sore throat, cough and fever, I just feel awful.",
        ],
    },
    "sprain": {
        "temperature_c": (36.5, 37.0), "heart_rate_bpm": (70, 85), "systolic": (115, 130),
        "diastolic": (75, 85), "spo2": (97, 99),
        "complaints": [
            "I twisted my ankle playing football, it is swollen and hurts to walk on.",
            "Fell off my bike and my wrist hurts, I can still move my fingers.",
        ],
    },
    "headache": {
        "temperature_c": (36.6, 37.1), "heart_rate_bpm": (75, 90), "systolic": (125, 140),
        "diastolic": (80, 90), "spo2": (97, 99),
        "complaints": [
            "A headache since this morning, bright light bothers me, no vomiting.",
            "Dull headache for two days, paracetamol helps a little.",
        ],
    },
}


class PatientMonitor(Sensor):
    name = "patient"
    description = (
        "The chair's vitals monitor (temperature, heart rate, blood pressure, SpO2) "
        "plus the seated patient's own answer to 'how do you feel?'"
    )

    def __init__(self, scenario: str = "random", **fields):
        super().__init__(**fields)  # name, interval… from the profile
        if scenario == "random":
            scenario = random.choice(list(SCENARIOS))
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)} or random")
        self.scenario = scenario
        self.complaint = random.choice(SCENARIOS[scenario]["complaints"])

    async def read(self):
        s = SCENARIOS[self.scenario]
        systolic = random.randint(*s["systolic"])
        return self.reading({
            "temperature_c": round(random.uniform(*s["temperature_c"]), 1),
            "heart_rate_bpm": random.randint(*s["heart_rate_bpm"]),
            "blood_pressure": f"{systolic}/{random.randint(*s['diastolic'])}",
            "spo2_percent": random.randint(*s["spo2"]),
            "complaint": self.complaint,
        })
