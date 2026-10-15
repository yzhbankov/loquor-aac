"""The dependency rule: only ``agent`` (and config/cli) may import the other layers.

``protocol``, ``transports`` and ``reasoning`` know nothing about each
other or about the agent, so any of them can be swapped alone.
"""

import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "loquor"
LAYERS = ("protocol", "transports", "reasoning")
IMPORT = re.compile(r"^\s*(?:from|import)\s+loquor\.(\w+)", re.MULTILINE)


def imports_of(package: str) -> set[str]:
    found = set()
    for path in (SRC / package).rglob("*.py"):
        found |= set(IMPORT.findall(path.read_text()))
    return found


def test_lower_layers_are_independent():
    for layer in LAYERS:
        assert imports_of(layer) <= {layer}, f"{layer} imports other layers: {imports_of(layer)}"


def test_nothing_below_the_agent_imports_it():
    for layer in LAYERS:
        assert "agent" not in imports_of(layer)
