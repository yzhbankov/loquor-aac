"""The shipped demo must run end to end (Test 1 offline path, Test 2 resume)."""

import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).parent.parent / "examples" / "two_machines_one_problem"


def run_demo(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run_demo.py", "--quiet", *args],
        cwd=DEMO_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_full_collaborative_diagnosis():
    proc = run_demo()
    assert proc.returncode == 0, proc.stderr
    assert '"diagnosis": "shaft_misalignment"' in proc.stdout
    assert '"consistent_with_local_trends": true' in proc.stdout
    assert "MR-2041" in proc.stdout  # evidence reference from Device B's records


def test_interrupted_collaboration_resumes():
    proc = run_demo("--interrupt")
    assert proc.returncode == 0, proc.stderr
    assert "Device B disconnects" in proc.stdout
    assert "resumed diagnosis: shaft_misalignment" in proc.stdout
    assert "final state: completed" in proc.stdout
