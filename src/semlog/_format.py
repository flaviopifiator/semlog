"""Log record formatter: the single pre-queue stage rendering one RFC 8259
JSON line per record (design #162 §1.1/§2.1/§4.1/§4.2, CP-016, LP-002)."""

from __future__ import annotations

import base64
import datetime
import decimal
import enum
import functools
import json
import logging
import math
import pathlib
import re
import traceback
import uuid

from ._context import current

EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# The library's own internal diagnostics channel (STANDARDS SI-003), never
# the formatter's own output path (self-referential logging).
_DIAGNOSTIC_LOGGER = logging.getLogger("semlog")

# The 23 stdlib LogRecord attribute names LRC-006 declares reserved
# (hardcoded, not introspected, so the set stays stable across versions).
RESERVED_KEYS = frozenset(
    "args asctime created exc_info exc_text filename funcName levelname "  # noqa: SIM905
    "levelno lineno message module msecs msg name pathname process "
    "processName relativeCreated stack_info taskName thread threadName".split()
)


def _severity_number(levelno: int) -> int:
    """Band a Python level into the OTel severity_number range 1-24 (STANDARDS §3)."""
    return 1 + 4 * min(levelno // 10, 5) + min(levelno % 10, 3)


# One `(second, "YYYY-MM-DDThh:mm:ss.")` tuple, replaced as a whole: a racing
# thread may recompute it, but never pairs a stale second with fresh micros.
_SECOND_PREFIX: list[tuple[int | None, str]] = [(None, "")]


def _timestamp(created: float) -> str:
    # Split and round exactly as `datetime.fromtimestamp` does (half-even to the
    # microsecond, carrying into the next second), then reuse the cached prefix.
    whole = int(created)
    micros = round((created - whole) * 1e6)
    second, micros = divmod(whole * 1_000_000 + micros, 1_000_000)
    cached = _SECOND_PREFIX[0]
    if cached[0] != second:
        utc = datetime.datetime.fromtimestamp(second, tz=datetime.timezone.utc)
        cached = (second, utc.strftime("%Y-%m-%dT%H:%M:%S."))
        _SECOND_PREFIX[0] = cached
    return f"{cached[1]}{micros:06d}Z"


# Strings of at most this many characters (attribute keys, event names) go
# through a bounded memo; longer ones are never cached, so a flood of unique or
# huge inputs can neither grow a memo past its size nor pin large strings.
_MEMO_MAX_LENGTH = 256


@functools.lru_cache(maxsize=1024)
def _is_event_name(msg: str) -> bool:
    return EVENT_NAME_RE.match(msg) is not None


def _classify(record: logging.LogRecord) -> tuple[str | None, str | None]:
    msg = record.msg
    if isinstance(msg, str) and not record.args:
        short = len(msg) <= _MEMO_MAX_LENGTH
        if _is_event_name(msg) if short else EVENT_NAME_RE.match(msg):
            return msg, None
    return None, record.getMessage()


_STACKTRACE_RENDERED_MARKER = "_semlog_stacktrace_rendered"


def _render_exception(exc_info) -> dict[str, object]:
    """Capture `exc_info` into the LP-004 fields, once per exception instance (design §2.1 step 3g)."""
    if not exc_info:
        return {}
    exc_type, exc_value, exc_tb = exc_info
    type_name = (
        exc_type.__qualname__
        if exc_type.__module__ in (None, "builtins")
        else f"{exc_type.__module__}.{exc_type.__qualname__}"
    )
    fields: dict[str, object] = {
        "error.type": type_name,
        "exception.type": type_name,
        "exception.message": str(exc_value),
    }
    if not getattr(exc_value, _STACKTRACE_RENDERED_MARKER, False):
        fields["exception.stacktrace"] = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        )
        try:
            setattr(exc_value, _STACKTRACE_RENDERED_MARKER, True)
        except AttributeError:
            pass  # a __slots__-based exception may refuse a new attribute
    return fields


def _catalog_violations(
    event_name: str | None, keys, catalog: dict | None
) -> list[str]:
    """Batch every catalog violation for `event_name` and call-site keys (LRC-008)."""
    if catalog is None or event_name is None:
        return []
    event_entry = catalog.get("events", {}).get(event_name)
    violations = []
    if event_entry is None:
        violations.append(f"undeclared event: {event_name}")
        declared: dict = {}
    else:
        declared = event_entry.get("attributes", {})
    violations.extend(f"undeclared field: {key}" for key in keys if key not in declared)
    return violations


# Always-on redaction key list (LP-005); `redact_keys` at Formatter
# construction only adds to this set, it never removes from it.
_BUILTIN_REDACT_KEYS = frozenset(
    "password passwd secret token access_token refresh_token api_key apikey "  # noqa: SIM905
    "authorization cookie set_cookie session private_key client_secret".split()
)

_REDACTED = "REDACTED"

# Shared recursion-depth cap for redaction and `_sanitize` (design #162 §6.6).
_MAX_RECURSION_DEPTH = 16


@functools.lru_cache(maxsize=1024)
def _sensitive_str_key(key: str, redact_keys: frozenset[str]) -> bool:
    return key.lower() in redact_keys or key.rsplit(".", 1)[-1].lower() in redact_keys


def _is_sensitive_key(key: object, redact_keys: frozenset[str]) -> bool:
    """Case-insensitive match on the full key or its last dotted segment (LP-005, LRC-009)."""
    if not isinstance(key, str):
        return False
    if len(key) > _MEMO_MAX_LENGTH:
        return _sensitive_str_key.__wrapped__(key, redact_keys)
    return _sensitive_str_key(key, redact_keys)


# A call-site key's flattened name (LRC-006/007/013) and its sensitivity (LP-005).
@functools.lru_cache(maxsize=1024)
def _key_info(key: str, namespace: str, redact_keys: frozenset[str]) -> tuple:
    name = key if "." in key else f"{namespace}.{key}"
    return name, _is_sensitive_key(name, redact_keys)


def _redact_value(value: object, redact_keys: frozenset[str], depth: int = 0) -> object:
    """Recursively redact sensitive keys, always on; a `tuple` stays a `tuple` (LP-005)."""
    if depth > _MAX_RECURSION_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if _is_sensitive_key(key, redact_keys)
                else _redact_value(item, redact_keys, depth + 1)
                if isinstance(item, (dict, list, tuple))  # scalars need no walk
                else item
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(
            _redact_value(item, redact_keys, depth + 1) for item in value
        )
    return value


_JSON_NATIVE_TYPES = (str, int, float, bool, type(None), bytes, list, tuple, dict)
# Exact types the single pass in `Formatter.format` settles: never walked or coerced.
_JSON_SCALAR_TYPES = frozenset({str, int, float, bool, type(None)})


def _truncate(
    value: object, max_length: int | None, depth: int = 0
) -> tuple[object, bool]:
    """Truncate a string/bytes value to `max_length`, recursing into lists/tuples/maps (LRC-010)."""
    if max_length is None or depth > _MAX_RECURSION_DEPTH:
        return value, False
    if isinstance(value, (str, bytes)):
        truncated = len(value) > max_length
        return (value[:max_length], True) if truncated else (value, False)
    if isinstance(value, (list, tuple)):
        pairs = [_truncate(item, max_length, depth + 1) for item in value]
        items, flags = zip(*pairs) if pairs else ((), ())
        return type(value)(items), any(flags)
    if isinstance(value, dict):
        pairs = {
            key: _truncate(item, max_length, depth + 1) for key, item in value.items()
        }
        return {key: item for key, (item, _was) in pairs.items()}, any(
            was for _item, was in pairs.values()
        )
    return value, False


def _coerce_scalar(value: object) -> object:
    """Coerce a non-JSON-native value (LRC-009); also the encoder's `default=` hook."""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID, pathlib.Path)):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (set, frozenset)):
        return list(value)
    try:
        return str(value)
    except Exception:  # noqa: BLE001 -- str() on an arbitrary/broken object
        # can raise literally anything; this coercion must never raise.
        return f"<unrepresentable {type(value).__name__}>"


_JSON_ENCODER = json.JSONEncoder(
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
    default=_coerce_scalar,
)


def _sanitize(value: object, depth: int = 0, coerce: bool = False) -> object:
    """Slow-path fallback: non-finite floats and non-string keys become JSON-safe."""
    if depth > _MAX_RECURSION_DEPTH:
        return "<max depth exceeded>"
    if coerce and not isinstance(value, _JSON_NATIVE_TYPES):
        value = _coerce_scalar(value)  # sanitize what `default=` would return
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item, depth + 1, coerce) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1, coerce) for item in value]
    return value


def _encode(envelope: dict, identity_json: str, identity: dict) -> str:
    """Encode compact single-line JSON, falling back to `_sanitize` (LRC-001, CWE-117)."""
    # `identity_json` is the record's closing tail: "}" or the pre-rendered
    # identity members; the fallback re-merges `identity` as `dict.update` would.
    try:
        return _JSON_ENCODER.encode(envelope)[:-1] + identity_json
    except (TypeError, ValueError):
        envelope = {**envelope, **identity}
    try:
        return _JSON_ENCODER.encode(_sanitize(envelope))
    except (TypeError, ValueError):  # a nested value `default=` made non-finite
        return _JSON_ENCODER.encode(_sanitize(envelope, coerce=True))


class Formatter(logging.Formatter):
    """Renders one JSON line per record (design #162 §1.1)."""

    def __init__(
        self,
        identity: dict[str, object],
        namespace: str = "app",
        baggage_prefix: str = "baggage.",
        catalog: dict | None = None,
        catalog_mode: str = "off",
        max_attributes: int = 128,
        max_attribute_length: int | None = None,
        redact_keys: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self._identity = identity
        self._namespace = namespace
        self._baggage_prefix = baggage_prefix
        self._catalog = catalog
        self._catalog_mode = catalog_mode
        self._max_attributes = max_attributes
        self._max_attribute_length = max_attribute_length
        self._redact_keys = _BUILTIN_REDACT_KEYS | {key.lower() for key in redact_keys}
        try:  # identity pre-rendered once as a JSON tail (design #162 §6.1)
            rendered = _JSON_ENCODER.encode(identity)
            self._identity_json: str | None = f",{rendered[1:]}" if identity else "}"
        except (TypeError, ValueError):  # only the sanitizing fallback encodes it
            self._identity_json = None

    def format(self, record: logging.LogRecord) -> str:
        event_name, body = _classify(record)
        envelope: dict[str, object] = {
            "timestamp": _timestamp(record.created),
            "severity_text": record.levelname,
            "severity_number": _severity_number(record.levelno),
            "event_name": event_name,
            "body": body,
        }
        snapshot = current()
        if snapshot is not None and snapshot.trace_id is not None:
            envelope["trace_id"] = snapshot.trace_id
            envelope["span_id"] = snapshot.span_id
            envelope["trace_flags"] = snapshot.trace_flags
        if snapshot is not None and snapshot.request_id is not None:
            envelope["http.request.id"] = snapshot.request_id
        envelope["otel.scope.name"] = record.name

        envelope.update(_render_exception(record.exc_info))
        if record.stack_info:
            envelope["code.stacktrace"] = record.stack_info

        # One pass over the call-site extras: reserved keys are skipped, and each
        # key is flattened and checked for sensitivity through one bounded memo.
        # Only names holding a non-scalar value are kept for the slow path.
        namespace, redact_keys = self._namespace, self._redact_keys
        attributes, slow = {}, []
        for key, value in vars(record).items():
            if key in RESERVED_KEYS:
                continue
            memo = _key_info if len(key) <= _MEMO_MAX_LENGTH else _key_info.__wrapped__
            name, sensitive = memo(key, namespace, redact_keys)
            attributes[name] = _REDACTED if sensitive else value
            if not sensitive and type(value) not in _JSON_SCALAR_TYPES:
                slow.append(name)

        if self._catalog_mode != "off":
            violations = _catalog_violations(event_name, attributes, self._catalog)
            if violations:
                joined = "; ".join(violations)
                if self._catalog_mode == "strict":
                    raise ValueError(f"catalog violation on {event_name!r}: {joined}")
                _DIAGNOSTIC_LOGGER.warning(
                    "%s.log.catalog_violation: %s", self._namespace, joined
                )

        # Bound context never overrides a call-site attribute; baggage does.
        if snapshot is not None and (snapshot.attributes or snapshot.baggage):
            tail = {k: v for k, v in snapshot.attributes.items() if k not in attributes}
            for key, value in snapshot.baggage.items():
                tail[f"{self._baggage_prefix}{key}"] = value
            attributes.update(tail)
            slow.extend(tail)  # bound names were never checked by the pass above

        # The slow path, for those names only: redaction by key and the
        # recursive walk into containers (LP-005); LRC-010's count limit keeps
        # the first attributes (design §5), whose non-native values are coerced
        # (LRC-009) before its length limit truncates them.
        slow = dict.fromkeys(slow)  # each name once, in order
        for name in slow:
            if _is_sensitive_key(name, redact_keys):
                attributes[name] = _REDACTED
            elif isinstance(attributes[name], (dict, list, tuple)):
                attributes[name] = _redact_value(attributes[name], redact_keys, 1)
        dropped = max(0, len(attributes) - self._max_attributes)
        if dropped:
            attributes = dict(list(attributes.items())[: self._max_attributes])
            slow = [name for name in slow if name in attributes]
        for name in slow:
            if not isinstance(attributes[name], _JSON_NATIVE_TYPES):
                attributes[name] = _coerce_scalar(attributes[name])
        truncated, max_length = 0, self._max_attribute_length
        if max_length is not None:
            for name, value in attributes.items():
                attributes[name], was_truncated = _truncate(value, max_length)
                truncated += was_truncated
        envelope.update(attributes)
        if dropped:
            envelope[f"{self._namespace}.dropped_attributes_count"] = dropped
        if truncated:
            envelope[f"{self._namespace}.truncated_attributes_count"] = truncated

        # Splice the identity tail unless a key collides (identity then wins at
        # the colliding key's position, exactly as `dict.update` places it).
        identity_json = self._identity_json
        if identity_json is None or not self._identity.keys().isdisjoint(envelope):
            envelope.update(self._identity)
            identity_json = "}"
        return _encode(envelope, identity_json, self._identity)
