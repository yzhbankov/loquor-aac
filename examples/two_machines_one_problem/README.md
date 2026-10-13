# Two Machines, One Problem

The first loquor PoC: a **Pump Agent** (Device A — live sensor data) and an **Inspection Agent** (Device B — maintenance history) discover each other on a local offline network, negotiate over evidence, and jointly diagnose an abnormal vibration. Full plan: [docs/POC_PLAN.md](../../docs/POC_PLAN.md).

## One machine (quick demo / CI)

```bash
pip install -e ../..            # from this directory, or run from the repo root
python run_demo.py              # full collaborative diagnosis on loopback
python run_demo.py --interrupt  # Device B drops mid-task, reconnects, task resumes
```

## Two machines

Both laptops on the same Wi-Fi (one can host a hotspot for the other — no internet needed), with the same pairing secret:

```bash
# Laptop B
export LOQUOR_PAIRING_SECRET=pick-something-shared
python inspection_agent.py --mdns

# Laptop A
export LOQUOR_PAIRING_SECRET=pick-something-shared
python pump_agent.py --mdns                     # mDNS discovery
python pump_agent.py --peer 192.168.1.20:8771   # or direct, if multicast is blocked
```

Optional: run [Ollama](https://ollama.com) locally (`ollama pull qwen2.5:1.5b`) and both agents will use it to write their explanations. Without it, deterministic templates are used — the diagnosis itself is always driven by deterministic logic.

## What to watch for

1. A detects vibration 8.2 mm/s against a 4.5 mm/s alarm threshold and decides it needs help.
2. A discovers B advertising `maintenance_diagnostics` (mDNS TXT records).
3. B finds record MR-2041 (recent bearing replacement, coupling re-fitted, alignment not verified) and asks A for motor current and the vibration spectrum.
4. A measures locally and answers; B concludes **shaft misalignment** with evidence and repair instructions; A cross-checks the hypothesis against its own trend data.
5. The final structured recommendation names both agents, the evidence records, and the cross-check results.

Every message is schema-validated and HMAC-signed; task state is persisted in SQLite on both sides, which is what makes the `--interrupt` scenario resume with no duplicate results.
