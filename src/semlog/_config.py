"""`configure()`, the library's single entry point (design #162 §3.3/3.4).
No configuration object (engram #238; CP-015): state lives in one
module-level `types.SimpleNamespace`, not a class -- a stdlib type never
counts against the class budget. `current()` returns it directly."""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

from . import _modes
from ._baggage import defaults as _baggage_defaults
from ._format import Formatter
from ._identity import resolve_identity
from ._transport import attach_root as _attach_root
from ._transport import capture_loggers as _capture_loggers
from ._transport import install as _install_pipeline

_CATALOG_MODES = ("off", "warn", "strict")
_OVERFLOW_MODES = ("block", "drop")
# LP-010: stdlib leaves the root logger at WARNING when unset; this
# library always sets an explicit, configurable level instead (default
# INFO), so INFO records are never silently dropped by omission.
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_state = SimpleNamespace(
    identity=None,
    namespace="app",
    catalog=None,
    catalog_mode=None,
    max_attributes=128,
    max_attribute_length=None,
    queue=True,
    queue_size=10000,
    overflow="block",
)


def current() -> SimpleNamespace:
    """Return the state of the last `configure()` call (test/internal use)."""
    if _state.identity is None:
        raise RuntimeError("configure() has not been called yet")
    return _state


def _resolve_limit(explicit: int | None, kind: str, default: int | None) -> int | None:
    """LRC-010's precedence: parameter > log-record `OTEL_*` var > generic var > default."""
    if explicit is not None:
        return explicit
    for env_name in (
        f"OTEL_LOGRECORD_ATTRIBUTE_{kind}_LIMIT",
        f"OTEL_ATTRIBUTE_{kind}_LIMIT",
    ):
        raw = os.environ.get(env_name)
        if raw:
            try:
                return int(raw)
            except ValueError:
                continue
    return default


def configure(
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    service_namespace: str | None = None,
    service_instance_id: str | None = None,
    environment: str | None = None,
    identity: tuple[object, ...] | None = None,
    identity_levels: tuple[str, ...] = ("role", "component"),
    level: str = "INFO",
    namespace: str = "app",
    capture_loggers: tuple[str, ...] = (),
    redact_keys: tuple[str, ...] = (),
    baggage_allow: tuple[str, ...] = (),
    baggage_prefix: str = "baggage.",
    accept_inbound_baggage: bool = True,
    catalog: dict | None = None,
    catalog_mode: str | None = None,
    max_attributes: int | None = None,
    max_attribute_length: int | None = None,
    queue: bool = True,
    queue_size: int = 10000,
    overflow: str = "block",
    mode: str | None = None,
    search_dir: str | None = None,
) -> None:
    """Resolve identity and validate configuration; last call wins. `mode`
    (LM-001) resolves before any other work, so an invalid value raises
    before anything else in this process is touched."""
    resolved_mode = _modes.resolve_mode(mode, search_dir=search_dir)
    if level not in _LEVELS:
        raise ValueError(f"level must be one of {sorted(_LEVELS)}, got {level!r}")
    if catalog is not None and not isinstance(catalog, dict):
        raise TypeError(
            f"catalog must be a JSON-shaped dict document, not {type(catalog).__name__}"
        )
    if overflow not in _OVERFLOW_MODES:
        raise ValueError(f"overflow must be one of {_OVERFLOW_MODES}, got {overflow!r}")
    if not (isinstance(queue_size, int) and queue_size > 0):
        raise ValueError(f"queue_size must be a positive int, got {queue_size!r}")

    default_catalog_mode = "off" if catalog is None else "warn"
    resolved_catalog_mode = (
        default_catalog_mode if catalog_mode is None else catalog_mode
    )
    if resolved_catalog_mode not in _CATALOG_MODES:
        raise ValueError(
            f"catalog_mode must be one of {_CATALOG_MODES}, got "
            f"{resolved_catalog_mode!r}"
        )
    if resolved_mode == "hybrid" and capture_loggers:
        # LM-003: hybrid never takes root, so there is no pipeline handler
        # a captured logger's records could propagate to instead.
        raise ValueError("capture_loggers is not supported in hybrid mode")

    if resolved_mode == "off":
        # LM-005: as if semlog were not installed -- nothing else runs.
        _modes.state.mode = "off"
        _modes.state.marking = False
        return

    # Hybrid defers its pyproject diagnostic (SI-005 erratum 8): no
    # pipeline exists yet to carry it as JSON, so it is collected here and
    # emitted after install(), marked `semlog=True`.
    pending_diagnostics = [] if resolved_mode == "hybrid" else None
    _state.identity = resolve_identity(
        service_name=service_name,
        service_version=service_version,
        service_namespace=service_namespace,
        service_instance_id=service_instance_id,
        environment=environment,
        identity=identity,
        identity_levels=identity_levels,
        namespace=namespace,
        search_dir=search_dir,
        diagnostics=pending_diagnostics,
    )
    _state.namespace, _state.catalog = namespace, catalog
    _state.catalog_mode = resolved_catalog_mode
    _state.max_attributes = _resolve_limit(max_attributes, "COUNT", 128)
    _state.max_attribute_length = _resolve_limit(
        max_attribute_length, "VALUE_LENGTH", None
    )
    _state.level = level
    _state.queue, _state.queue_size, _state.overflow = queue, queue_size, overflow
    _baggage_defaults.accept_inbound_baggage = accept_inbound_baggage
    _baggage_defaults.baggage_allow = baggage_allow

    formatter = Formatter(
        identity=_state.identity,
        namespace=namespace,
        baggage_prefix=baggage_prefix,
        catalog=catalog,
        catalog_mode=resolved_catalog_mode,
        max_attributes=_state.max_attributes,
        max_attribute_length=_state.max_attribute_length,
        redact_keys=redact_keys,
    )
    handler = _install_pipeline(
        formatter,
        namespace=namespace,
        queue=queue,
        queue_size=queue_size,
        overflow=overflow,
    )

    if resolved_mode == "full":
        # LP-010: full always sets an explicit root level; hybrid leaves
        # root's handlers and level exactly as found.
        _attach_root(handler, level=_LEVELS[level], replace_all=True)
        _capture_loggers(capture_loggers)
    else:
        # Hybrid never takes root: only detach a handler THIS library left
        # there from an earlier full configure(), never a foreign one.
        root = logging.getLogger()
        for existing in list(root.handlers):
            if getattr(existing, "_semlog_root", False):
                root.removeHandler(existing)
        _modes.arm_routing()

    # `marking`/`mode` are the very last things this call touches: a second
    # call that raises during validation above must never reach this point,
    # so it leaves both exactly as the last successful call left them
    # (validation-4 minor).
    _modes.state.mode = resolved_mode
    _modes.state.marking = resolved_mode == "hybrid"

    if pending_diagnostics:
        logging.getLogger("semlog").info(pending_diagnostics[0], semlog=True)
