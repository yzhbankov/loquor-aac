# Demo: Emergency Triage — LLM to LLM

A waiting area with **automated triage chairs**. A patient sits down; the
chair measures temperature, heart rate, blood pressure and oxygen, asks
"how do you feel?", and its LLM can assess how serious the case is. A
**nurse** arrives with a laptop; the nurse's agent asks every chair about
its patient, its LLM decides whom to see first, and the triage board
appears on the screen.

This is the case for a brain on *both* sides: each chair knows its own
patient and judges it; the nurse's agent only sees what the chairs say.
Unlike the [greenhouse](../greenhouse/README.md), the nurse asks the
chairs questions instead of reading their sensors — agents are asked,
nodes are read.

**Real:** the agents, the protocol (keys, handshake, encryption), the
LLM decisions on both sides. **Emulated:** the chair's monitor invents
vitals instead of reading a cuff and an oximeter; the board prints to the
terminal instead of a ward display.

| file | what it is |
|---|---|
| `patient_simulator.py` | **sensor** — the chair's monitor: vitals plus what the patient said; a `scenario` per chair |
| `triage_board.py` | **trigger** — the board on the nurse's screen; its `input_schema` is the board's shape |
| `chair_1.json` … `chair_3.json` | a chair: the sensor and a `brain` that answers questions (no goal) |
| `chair_prompt.txt` | how a chair's LLM assesses its patient; `{sensors}` becomes the monitor's reading |
| `nurse_agent.json` | the nurse: a `brain` with a goal, `"min_peers": 3`, `"settle": 15`, `"act": ["triage_board"]` |
| `nurse_prompt.txt` | how the nurse's LLM orders the patients |

```mermaid
sequenceDiagram
    participant C as Triage Chair 1…3 (brain)
    participant N as Triage Nurse (brain, goal)
    participant B as triage board (trigger)
    C-->>N: beacons · HELLO/HELLO_ACK (keys exchanged, encrypted from here)
    Note over N: LLM, once per chair: "can it help my goal?" → yes; writes the question
    N->>C: ask {question: "How do you feel and what is your current vital sign status?"}
    Note over C: LLM assesses from the monitor's reading
    C->>N: {answer}
    Note over N: 3 answers in (or 15 s after the last) → LLM concludes the order
    Note over N: LLM proposes triage_board {patients, recommendations}; schema checked
    N->>B: fire → the board is printed
```

## 1. Setup (once per device)

In the repo root, `./install.sh`, then `source .venv/bin/activate`
(details in [docs/INSTALL.md](../../docs/INSTALL.md)). Chairs and nurse
all need a model.

## 2. Run on one computer

Four terminals in the repo root with the venv active. Chairs first (any
order), then the nurse:

```bash
loquor run demo/triage/chair_1.json        # chest pain
loquor run demo/triage/chair_2.json        # flu
loquor run demo/triage/chair_3.json        # breathing difficulty
loquor run demo/triage/nurse_agent.json
```

Two chairs are enough: the nurse concludes 15 s after the last answer if
fewer than three turn up (`"settle"` in `nurse_agent.json`).

### What you will see

The nurse's terminal — a real run with `qwen2.5:3b` on one laptop, about
20 s from the first chair to the board (the `met` and `shares` lines left
out):

```
02:26:15  triage-nurse-7897d9e2: reasoning about triage-chair-1-925b9870: useful=True — The Triage Chair can provide the necessary information to assess the urgency of each patient's case.
02:26:16  LLM  0.4 s  "You will ask Triage Chair 1 (I am an automated emergency triage chair…" -> "How do you feel and what is your current vital sign status?"
02:26:16  triage-nurse-7897d9e2: asking triage-chair-1-925b9870 for ask: How do you feel and what is your current vital sign status?
02:26:18  triage-nurse-7897d9e2: triage-chair-1-925b9870 answered: The patient reports a temperature of 37.3°C, a heart rate of 115 bpm, a blood pressure of 154/95 mmHg, and a SpO2 of 95%. They describe a 20-minute episode of …
  … the same for Triage Chair 2 and Triage Chair 3 …
02:26:30  triage-nurse-7897d9e2: concluded: Based on the urgency and severity of symptoms, the nurse should see the patient from Triage Chair 3 first, followed by the patient from Triage Chair 1, and then the patient from Triage Chair 2. …
02:26:35  triage-nurse-7897d9e2: round 1: firing triage_board — Patient from Triage Chair 3 requires immediate attention due to severe hypoxemia and shortness of breath.

╭────────────────────────────────────────────────────────────────────╮
│ 🏥  TRIAGE BOARD — see patients in this order                       │
│                                                                    │
│ 1. Triage Chair 3                                                  │
│       Severe hypoxemia and shortness of breath                     │
│                                                                    │
│ 2. Triage Chair 1                                                  │
│       Potential acute coronary syndrome with chest pain and        │
│       profuse sweating                                             │
│                                                                    │
│ 3. Triage Chair 2                                                  │
│       Severe viral infection with high fever and exhaustion        │
│                                                                    │
│ Recommendations                                                    │
│   • See Triage Chair 3 immediately                                 │
│   • See Triage Chair 1 within 10 minutes                           │
│   • See Triage Chair 2 within 15 minutes                           │
╰────────────────────────────────────────────────────────────────────╯
```

With a 3B model the order changes between runs — chest pain first one
time, breathing first the next; the board always follows the nurse's
conclusion. A larger model on the nurse's laptop is steadier. This is a
demo of the mechanics, not a clinical tool.

Each chair's terminal shows the question arriving (`triage-nurse-… asks
for ask`), its `LLM` call and its assessment leaving. `loquor status`
from a fifth terminal shows every chair's current reading and the
nurse's goal; `loquor trace "Triage Chair 1" --full` the exact vitals the
chair's LLM saw. `loquor ask "Triage Chair 2" "How is your patient?"` asks
a chair yourself.

Where the decisions are made:

| decision | by | how |
|---|---|---|
| how serious is this patient | the chair's LLM | its brain answers with the monitor's reading at `{sensors}` in its prompt |
| which chairs to ask, and what | the nurse's LLM | once per chair it hears: useful or not, and the question |
| whom to see first | the nurse's LLM | concludes over all three answers |
| show the board | the nurse's LLM proposes, code decides | the proposal must match `triage_board`'s input schema or nothing is shown |

Things to try:

- Change a chair's `"scenario"` (`sprain`, `headache`, `random`) and restart the nurse.
- Ask a chair for numbers, not prose: `loquor ask "Triage Chair 1"` — the
  chairs share their monitor's readings too. The nurse could read them
  without asking: `"sensors": ["triage-chair-1:patient", …]` in its brain.
- Replace `patient_simulator.py` with a driver for a real cuff: same contract, nothing else changes.

## 3. Run on several devices

Chairs and the nurse's device on one Wi-Fi or hotspot, each set up as in
step 1 with `loquor config set transport wifi` (or `--transport wifi` on
every command). A chair can be a Raspberry Pi with a cuff on its UART —
`loquor service install chair_1.json --now` keeps it running after a
reboot; the nurse's device anything with Python and a local model.

**Over Bluetooth**, with no network at all: `loquor doctor --transport
bluetooth` on every device, then `--transport bluetooth` instead. The
nurse's device connects to each chair over GATT as it hears its beacon;
with three chairs allow ~30 s before the round starts, and raise
`"settle"` in `nurse_agent.json` if a chair is slow to connect. See the
weather demo's README for Bluetooth prerequisites and troubleshooting.

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| nurse concludes with fewer chairs than expected | they took longer than 15 s to answer (`LLM  … s` lines) — raise `"settle"`, or a smaller model on the chairs (`loquor config set model qwen2.5:1.5b`) |
| nurse never starts | it needs to hear at least one chair; same transport on all (`loquor config show`); `loquor status` on the nurse lists the chairs it sees |
| a chair answers "I can't provide the status of other patients" | the question was about all patients; `loquor trace "Triage Nurse"` shows the question it wrote — a larger model writes better ones |
| Bluetooth: chairs never `met` | `loquor doctor --transport bluetooth` on each device |
| `no action — invalid input …` | the model's board did not match the schema; `loquor trace "Triage Nurse"` shows the proposal — rerun or use a larger model |
| `model ... is not available in Ollama` | `ollama pull <model>`, or `loquor doctor` |
