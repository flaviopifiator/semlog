"""Test package for semlog.

Makes the src-layout package importable with zero setup: ``python -m
unittest discover`` runs against the ``src/`` tree directly, by inserting
it at the front of ``sys.path`` once, here -- no editable install
required. `pyproject.toml` (task 4.4/4.15) exists for building and
installing the package (`uv build`, `uv sync`); the anti-drift checks that
specifically need an *installed* package (tasks 4.21-4.23, e.g. reading
`importlib.metadata` for a real version instead of the ``"unknown"``
fallback) run against a built wheel installed elsewhere, never against
this sys.path shortcut.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
