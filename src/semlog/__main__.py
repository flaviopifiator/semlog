"""`python -m semlog` entry point: only the `llm` subcommand, no
console-script (design-decisions #170 §17.4; CP-018)."""

from __future__ import annotations

import sys

from . import llm


def main(argv: list[str]) -> int:
    """Return the process exit code for `argv` (`sys.argv[1:]`-shaped)."""
    if argv == ["llm"]:
        sys.stdout.buffer.write(llm().encode("utf-8"))
        return 0
    sys.stderr.write("usage: python -m semlog llm\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
