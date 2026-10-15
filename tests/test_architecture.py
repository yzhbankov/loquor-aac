"""The dependency rule.

- ``protocol`` imports nothing but itself: no radio, no LLM, no agent.
- ``transports`` implement the protocol's transport port and import nothing else.
- ``reasoning`` imports nothing but itself.
- ``extensions`` build on ``protocol`` only.
- ``sensors`` and ``triggers`` are drivers: they import nothing but themselves.
- nothing below ``agent`` imports ``agent``.
"""

import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "loquor"
ALLOWED = {
    "protocol": {"protocol"},
    "transports": {"transports", "protocol"},
    "reasoning": {"reasoning"},
    "extensions": {"extensions", "protocol"},
    "sensors": {"sensors"},
    "triggers": {"triggers"},
}
IMPORT = re.compile(r"^\s*(?:from|import)\s+loquor\.(\w+)", re.MULTILINE)


def imports_of(package: str) -> set[str]:
    found = set()
    for path in (SRC / package).rglob("*.py"):
        found |= set(IMPORT.findall(path.read_text()))
    return found


def test_layers_import_only_what_they_may():
    for layer, allowed in ALLOWED.items():
        if not (SRC / layer).exists():
            continue
        assert imports_of(layer) <= allowed, f"{layer} imports {imports_of(layer) - allowed}"


def test_nothing_below_the_agent_imports_it():
    for layer in ALLOWED:
        if (SRC / layer).exists():
            assert "agent" not in imports_of(layer)
