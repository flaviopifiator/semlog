"""Fairness check (task 5.1): runs before any timing (STANDARDS.md CP-005
-- "todos los sujetos se configuran para emitir las mismas claves y
valores ante la misma llamada, verificado campo por campo antes de medir
tiempos"). Every subject keeps its own natural field names (semlog's
dotted OTel-style attributes, structlog/loguru's flat or nested shapes);
this module normalizes each subject's raw JSON record to one canonical,
comparable shape and reports any discrepancy, instead of silently
assuming the subjects are doing equivalent work.
"""

from __future__ import annotations

CANONICAL_FIELDS = ("event", "user_id", "request_id", "duration_ms")


def normalize_semlog(record: dict) -> dict:
    return {
        "event": record.get("event_name"),
        "user_id": record.get("app.user_id"),
        "request_id": record.get("app.request_id"),
        "duration_ms": record.get("app.duration_ms"),
    }


def normalize_stdlib_baseline(record: dict) -> dict:
    return {
        "event": record.get("event"),
        "user_id": record.get("user_id"),
        "request_id": record.get("request_id"),
        "duration_ms": record.get("duration_ms"),
    }


def normalize_loguru(record: dict) -> dict:
    inner = record.get("record", {})
    extra = inner.get("extra", {})
    return {
        "event": inner.get("message"),
        "user_id": extra.get("user_id"),
        "request_id": extra.get("request_id"),
        "duration_ms": extra.get("duration_ms"),
    }


def normalize_structlog(record: dict) -> dict:
    return {
        "event": record.get("event"),
        "user_id": record.get("user_id"),
        "request_id": record.get("request_id"),
        "duration_ms": record.get("duration_ms"),
    }


NORMALIZERS = {
    "semlog": normalize_semlog,
    "stdlib_baseline": normalize_stdlib_baseline,
    "loguru": normalize_loguru,
    "structlog": normalize_structlog,
}


def check_fairness(raw_records: dict) -> list:
    """Return a list of human-readable discrepancies (empty when every
    subject's normalized record carries the same canonical content).
    `raw_records` maps subject name (a key of `NORMALIZERS`) to the
    parsed JSON object it emitted for the SAME call."""
    normalized = {name: NORMALIZERS[name](raw) for name, raw in raw_records.items()}
    problems = []
    reference_name = next(iter(normalized))
    reference = normalized[reference_name]
    for name, value in normalized.items():
        for field in CANONICAL_FIELDS:
            if value.get(field) is None:
                problems.append(f"{name} is missing a value for {field!r}: {value}")
        if name != reference_name and value != reference:
            problems.append(
                f"{name} differs from {reference_name}: {value} != {reference}"
            )
    return problems
