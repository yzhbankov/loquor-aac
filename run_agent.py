"""Run one loquor agent straight from the source tree, no install needed.

    pip install -r requirements.txt
    python run_agent.py demo/weather_and_clothes/weather_agent.json --transport wifi
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from loquor.cli import main  # noqa: E402

main()
