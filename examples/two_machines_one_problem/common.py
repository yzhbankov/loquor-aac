"""Shared helpers for the Two Machines, One Problem demo."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# Pre-shared pairing secret standing in for a real pairing ceremony.
# Set the same value on both laptops: export LOQUOR_PAIRING_SECRET=...
def pairing_secret() -> str:
    return os.environ.get("LOQUOR_PAIRING_SECRET", "demo-pairing-secret")


def load_knowledge(name: str) -> dict:
    return json.loads((KNOWLEDGE_DIR / name).read_text())


def setup_logging(verbose: bool = True) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)


def banner(text: str) -> None:
    print(f"\n=== {text} ===")
