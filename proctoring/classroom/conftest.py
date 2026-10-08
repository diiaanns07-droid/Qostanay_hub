"""Make `classroom` importable when pytest runs from proctoring/ (pyproject pythonpath is A01's)."""

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])  # proctoring/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
