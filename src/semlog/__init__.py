"""SEMLOG: a stdlib-only structured logging library (CP-015, 8 public
names once every phase lands); ``__all__`` never lists a name before it
exists."""

from __future__ import annotations

from ._config import configure
from ._context import bind, inject, operation
from ._identity import _dist_version
from ._middleware import ASGIMiddleware, WSGIMiddleware
from ._transport import flush

# The only code copy of the semconv version pinned in STANDARDS.md §4 (design
# #170 §17.3); `llm()`'s generated header cites it alongside the installed
# semlog version, so the stored guide itself carries no version literal.
_OTEL_SEMCONV_VERSION = "1.44.0"


def llm() -> str:
    """Return the version-matched agent guide from package data, preceded
    by a header generated at read time; no cache (DOC-011, CP-018)."""
    import importlib.resources

    guide = (
        importlib.resources.files(__package__)
        .joinpath("agent_guide.md")
        .read_text(encoding="utf-8")
    )
    header = (
        f"---\nsemlog_version: {_dist_version('semlog')}\n"
        f"otel_semconv_version: {_OTEL_SEMCONV_VERSION}\n---\n\n"
    )
    return header + guide


__all__: tuple[str, ...] = (  # noqa: RUF022
    "configure",
    "WSGIMiddleware",
    "ASGIMiddleware",
    "operation",
    "bind",
    "inject",
    "flush",
    "llm",
)
