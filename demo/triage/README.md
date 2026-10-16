# Demo: Emergency Triage

A waiting area with **automated triage chairs**. A patient sits down; the
chair measures temperature, heart rate, blood pressure and oxygen, asks
"how do you feel?", and its LLM assesses how serious the case is. A
**nurse** arrives with a laptop; her agent asks every chair for its
patient's status, her LLM decides whom to see first, and the triage board
appears on her screen.

**Real:** the agents, the protocol (keys, handshake, encryption), the LLM
decisions on both sides. **Emulated:** the hardware — the chair's monitor
invents vitals instead of reading a cuff and an oximeter; the board prints
to the terminal instead of a ward display.

Everything specific to this demo is one sensor, one trigger and the
profiles. The skills are the built-in ones.

| file | what it is |
|---|---|
| `patient_simulator.py` | **sensor** — the chair's monitor: vitals plus what the patient said; a `scenario` per chair (stand-in for a real cuff and oximeter) |
| `triage_board.py` | **trigger** — the board on the nurse's screen (stand-in for a ward display or pager) |
| `chair_1.json`, `chair_2.json`, `chair_3.json` | a chair: the sensor + `llm_answer` grounded in it, answering `patient_status` |
| `nurse_agent.json` | the nurse: `goal` that gathers answers from 3 chairs + the board trigger |

```mermaid
sequenceDiagram
    participant S as patient monitor (sensor)
    participant C as Triage Chair 1…3
    participant N as Triage Nurse
    participant B as triage board (trigger)
    C-->>N: beacons · HELLO/HELLO_ACK (keys exchanged, encrypted from here)
    Note over N: LLM: "can this chair help my goal?" → yes, ask patient_status
    N->>C: MSG request patient_status {question}
    S-->>C: vitals + "Crushing pain in my chest…"
    Note over C: LLM assesses from the reading: severity, urgency, concern
    C->>N: MSG response {answer}
    Note over N: 3 answers in (or 15 s after the last) → LLM concludes the order
    Note over N: LLM proposes triage_board {patients, recommendations}; schema validated
    N->>B: fire → the board is printed
```

## 1. Setup (once per device)

Needs Python 3.10+ and [Ollama](https://ollama.com). In the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b
```

## 2. Run on one computer

Four terminals in the repo root with the venv active. Chairs first (any
order), then the nurse:

```bash
python run_agent.py demo/triage/chair_1.json --wire off    # chest pain
python run_agent.py demo/triage/chair_2.json --wire off    # flu
python run_agent.py demo/triage/chair_3.json --wire off    # breathing difficulty
python run_agent.py demo/triage/nurse_agent.json --wire off
```

Two chairs are enough: the nurse concludes 15 s after the last answer if
fewer than three turn up (`"settle"` in `nurse_agent.json`).

### What you will see

The nurse's terminal, under a minute with a 3B model:

```
triage-nurse-…: reasoning about triage-chair-1-…: useful=True — it has a patient's vitals and complaint
triage-nurse-…: reasoning about triage-chair-3-…: useful=True — …
triage-nurse-…: reasoning about triage-chair-2-…: useful=True — …

=== Goal complete ===
goal      : Find out the status of every patient in the triage chairs and decide the order…
asked     : Triage Chair 1, Triage Chair 3, Triage Chair 2
outcome   : See Chair 1 first (possible cardiac event, severity 1), then Chair 3 (respiratory distress, SpO2 88 %), then Chair 2 (influenza-like illness, stable)…

triage-nurse-…: firing triage_board — the order is decided and must reach the nurse

╭────────────────────────────────────────────────────────────────────╮
│ 🏥  TRIAGE BOARD — see patients in this order                      │
│                                                                    │
│ 1. Triage Chair 1                                                  │
│       Chest pain radiating to the arm, tachycardia, hypertension:  │
│       possible acute coronary syndrome — immediate                 │
│                                                                    │
│ 2. Triage Chair 3                                                  │
│       Wheezing, SpO2 88 %, heart rate 119: respiratory distress —  │
│       within 15 minutes                                            │
│                                                                    │
│ 3. Triage Chair 2                                                  │
│       Fever 38.5 °C, stable vitals: influenza-like illness — within │
│       an hour                                                      │
│                                                                    │
│ Recommendations                                                    │
│   • ECG and cardiac pathway for Chair 1 now.                       │
│   • Supplemental oxygen for Chair 3, reassess SpO2 in 5 minutes.   │
╰────────────────────────────────────────────────────────────────────╯
```

Each chair's terminal shows the question arriving and its own assessment
leaving — grounded in its sensor reading, which `-v` prints every time it
is read.

Where the decisions are made:

| decision | by | how |
|---|---|---|
| how serious is this patient | the chair's LLM | `llm_answer` with the sensor reading at `{sensors}` in its prompt |
| which peers to ask | the nurse's LLM | `goal` decides per chair it hears (useful / capability / question) |
| whom to see first | the nurse's LLM | `goal` concludes over all three answers |
| show the board | the nurse's LLM proposes, code decides | the proposal must match `triage_board`'s input schema or nothing is shown |

Things to try:
- Change a chair's `"scenario"` (`sprain`, `headache`, `random`) and restart the nurse.
- Ask a chair for numbers, not prose: `sensor_report` is on every chair.
- Replace `patient_simulator.py` with a driver for a real cuff: same contract, nothing else changes.

## 3. Run on several devices

Chairs and the nurse's device on one Wi-Fi or hotspot, each set up as in
step 1, `--transport wifi` on every command. A chair can be a Raspberry Pi
with a cuff on its UART; the nurse's device anything with Python and a local
model.

**Over Bluetooth**, with no network at all: `pip install -e ".[ble]"` on
every device, then `--transport bluetooth` on every command instead. The
nurse's device connects to each chair over GATT as it hears its beacon;
with three chairs allow ~30 s before the round starts, and raise
`"settle"` in `nurse_agent.json` if a chair is slow to connect. See the
weather demo's README for Bluetooth prerequisites and troubleshooting.

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| nurse concludes with fewer chairs than expected | they took longer than 15 s to answer — raise `"settle"`, or use a smaller model on the chairs (`--model qwen2.5:1.5b`) |
| nurse never starts | it needs to hear at least one chair; same `--transport` on all |
| Bluetooth: chairs never `met` | Bluetooth off or not allowed for the terminal; `pip install -e ".[ble]"` missing (the error says so) |
| `no trigger fired: invalid input…` | the model's board did not match the schema; it is logged, nothing wrong is shown — rerun or use a larger model |
| `model ... is not available in Ollama` | `ollama pull qwen2.5:3b` |
