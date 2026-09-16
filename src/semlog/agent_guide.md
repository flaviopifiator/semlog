# semlog: Agent Guide

> If you are reading this file directly from the repository source tree, this is repository text with no version header. Run `python -m semlog llm`, or call `semlog.llm()` from the installed package, to get this exact guide preceded by a header naming the installed semlog version and the pinned OpenTelemetry Semantic Conventions version.

This guide is the single, self-sufficient source for writing correct call sites against semlog, offline, with no network access. It is shipped inside the installed package as package data, so it always matches the installed library version. `llms.txt` and `AGENTS.md` link to it instead of duplicating its content; if you already have this file, you do not need either of those.

The name is `semantic` plus `log`: semlog applies the field names of OpenTelemetry's Semantic Conventions to log records, so every field carries a stable meaning instead of free-form text. That is what the call-site rules below protect: a static event name and typed fields in `extra` keep a record queryable, while an interpolated message does not.

## Public API

semlog exposes exactly nine public names. Nothing else is part of the public surface; everything else in application code stays plain stdlib `logging.getLogger(__name__)` and standard `Logger` methods. The package ships a PEP 561 `py.typed` marker, so a type checker reads the annotations it carries instead of treating the package as untyped; the signatures below are the authoritative ones.

### configure

```python
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
) -> None: ...
```

The single entry point. Call it exactly once, at process start, before the application starts logging. All parameters are keyword-only; there is no settings object and no settings dict. Calling it twice is safe: the last call wins, and the previous writer is drained and stopped. An invalid value or combination raises `ValueError` immediately, never later at runtime (for example, an `overflow` outside `"block"`/`"drop"`, or a non-positive `queue_size`). `search_dir` overrides the directory `pyproject.toml` detection searches upward from (instead of the current working directory); most callers never need it. `mode` selects `"full"` (default), `"hybrid"` or `"off"`; precedence is the `mode` parameter, then `SEMLOG_MODE`, then `[tool.semlog].mode` (3.11+), then `"full"`.

`capture_loggers` removes a third-party logger's own handlers (for example a server's error/access logger) and turns propagation on, so its records flow through the same pipeline instead of a separate one. `baggage_allow`/`baggage_prefix`/`accept_inbound_baggage` set the process-wide default: `operation()`, `WSGIMiddleware`, and `ASGIMiddleware` each still accept their own `baggage_allow` (see below) to override it per instance, but a plain `WSGIMiddleware(app)` with no `baggage_allow` uses the value configured here. `accept_inbound_baggage=False` makes every middleware/`operation()` ignore an inbound `baggage` header entirely, regardless of any allowlist. `queue=False` writes every record synchronously, on the caller's own thread, with no internal queue and no writer thread, like a plain stdlib `StreamHandler`.

### WSGIMiddleware

```python
class WSGIMiddleware:
    def __init__(self, app, *, baggage_allow=None, log_requests=False): ...
```

Wraps a PEP 3333 WSGI application: parses inbound `traceparent`/`tracestate`/baggage headers, binds a fresh context for the request, generates `http.request.id`, and resets the context only after the response iterable is fully consumed (so a streaming/generator body stays correlated through every chunk). `baggage_allow` lists which inbound baggage keys this middleware copies into log attributes; its default, `None`, means "use `configure(baggage_allow=...)`'s process-wide default". Pass an explicit tuple (including `()` for none) to override it just for this instance. `log_requests=True` additionally emits one `http.server.request` completion event per request.

### ASGIMiddleware

```python
class ASGIMiddleware:
    def __init__(self, app, *, baggage_allow=None, log_requests=False): ...
```

The same contract as `WSGIMiddleware`, including `baggage_allow`'s configured-default fallback, for a pure ASGI 3.0 application operating at the `http` scope; it does not depend on `BaseHTTPMiddleware`. The `lifespan` scope passes through untouched, with no header parsing and no context binding.

### DjangoMiddleware

```python
class DjangoMiddleware:
    def __init__(self, get_response, *, baggage_allow=None, log_requests=None): ...
```

A `settings.MIDDLEWARE` entry serving both synchronous and asynchronous Django deployments through exactly one class: parses inbound `traceparent`/`tracestate`/baggage headers, binds a fresh context for the request, and stays correctly bound throughout a `StreamingHttpResponse` body, including on Django's oldest supported release. `baggage_allow` follows the same configured-default fallback as `WSGIMiddleware`/`ASGIMiddleware`. `log_requests=True` additionally emits one `http.server.request` completion event per request; left as `None` (the default), it resolves instead from the Django setting `SEMLOG_LOG_REQUESTS` (default off), because a dotted `MIDDLEWARE` entry cannot receive a keyword argument. `process_exception` stashes an unhandled view exception without swallowing it, so the completion event still carries the real final status and a rendered traceback; see the Django recipe below for placement guidance and this hook's permanent limitations.

### operation

```python
def operation(headers=None, *, baggage_allow=None): ...
```

The correlation primitive for non-HTTP work (background jobs, queue consumers, CLI commands); the three middlewares use the same primitive internally. Used as a context manager: `with operation():`. Called with `headers`, it parses them like an inbound HTTP request; `baggage_allow` lists which inbound baggage keys are copied into log attributes, falling back to `configure(baggage_allow=...)`'s default when left as `None`. Called without `headers` while already inside an operation, it starts a child span on the same trace. Called without `headers` outside any operation, it starts a brand new trace.

### bind

```python
def bind(attributes: Mapping[str, object]) -> None: ...
```

Adds fields to the scope of the current operation. Fields merge into whatever is already bound; a later `bind()` call never replaces earlier fields, it only adds to them. Takes a mapping of dotted keys to values (this is event data, not configuration).

Calling `bind()` outside any `operation()` or middleware scope does not raise and does not modify context. The first such call in a process emits one warning through the library's internal diagnostics channel, naming the call site; every later out-of-scope call in that process is a silent no-op.

### inject

```python
def inject(headers: dict, *, trusted: bool = True) -> None: ...
```

Injects `traceparent` (and `tracestate`/`baggage` when present) into any caller-supplied, dict-like header mapping, with no dependency on any specific HTTP client. If `headers` already contains a `traceparent` (any case), it is left unchanged: another propagator already owns that call. Set `trusted=False` at a trust boundary so `baggage` is stripped from that specific outbound call while the current process's own log records keep their allowlisted baggage attributes.

### flush

```python
def flush(timeout=None): ...
```

Drains the queue deterministically: enqueues a marker and returns only once everything queued before it has been written. Use it right before `os._exit()` (pre-fork workers, `multiprocessing` children) and in tests, to make sure records are written before assertions run.

### llm

```python
def llm() -> str: ...
```

Returns this exact guide as a `str`, preceded by a header generated at read time (installed semlog version, pinned OTel Semantic Conventions version). Reads only package data via stdlib `importlib.resources`; performs no network access and raises no error on a correctly installed package. `python -m semlog llm` prints the identical text to stdout and exits with status code 0.

## Call-site rules

- **MUST** use a static `event_name` with no interpolated variables, matching the grammar `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. `logger.info("order.item.purchased", ...)` is conformant; `logger.info(f"order {order_id} purchased")` is not.
- **MUST** pass every call-site variable via `extra`, under the configured custom namespace (default `app.`) or a declared OTel semantic-convention key (for example `server.address`, `url.path`). **MUST NOT** interpolate a variable into the message text itself.
- **MUST** call `logger.exception(...)` exactly once, at the boundary that actually handles the exception; it is never re-logged by an intermediate layer that only re-raises.
- **MUST NOT** build the log message with an f-string, `%`-formatting, `.format()`, dict interpolation, or `json.dumps(...)`. A message built that way carries no structured fields and cannot be aggregated or type-checked.
- **MUST NOT** log secrets, tokens, passwords, or full request/response bodies. Log only the specific fields a case actually needs. Always-on redaction (below) is a safety net, never the primary control.
- **MUST NOT** use `print()` anywhere in application code; it never reaches the pipeline, carries no severity, and has no trace context.
- **MUST NOT** pass an `extra` key that collides with a reserved stdlib `LogRecord` attribute name (for example `message`, `module`, `args`, `name`): this raises `KeyError` synchronously at the call site, before the record reaches the queue.
- **MUST NOT** construct a dynamically named attribute key at runtime (using a runtime value as the JSON key itself). New keys are declared through the event catalog, not invented at the call site.

Example, variables as fields instead of interpolated text:

```python
# Before (non-conformant)
logger.info(f"User {user_id} purchased {quantity} units of {product}")

# After (conformant)
logger.info(
    "order.item.purchased",
    extra={
        "app.user.id": user_id,
        "app.order.quantity": quantity,
        "app.product.sku": product,
    },
)
```

Example, an exception logged exactly once at the handling boundary:

```python
# Before (non-conformant: logged again by every layer that re-raises)
def reserve_inventory(order):
    try:
        inventory_client.reserve(order.items)
    except InventoryUnavailableError:
        logger.error("could not reserve inventory")
        raise


# After (conformant: logged once, where it is actually handled)
def reserve_inventory(order):
    inventory_client.reserve(order.items)  # not the handler, does not catch


def process_order(order):
    try:
        reserve_inventory(order)
    except InventoryUnavailableError:
        logger.exception("order.checkout.failed", extra={"app.order.id": order.id})
```

### Redaction

A fixed built-in list of sensitive keys is always redacted, recursively into nested values, before serialization. This cannot be disabled at runtime. `configure(redact_keys=(...))` adds project-specific keys on top of the built-in list; it can only add, never remove or replace, a built-in entry. It exists as a last-resort safety net, so call sites still must not deliberately log secrets or full bodies.

## Levels and severity

| Python level | `severity_text` | `severity_number` | OTel short name | RFC 5424 (informative only) |
|---|---|---|---|---|
| `DEBUG` | `DEBUG` | 5 | DEBUG | 7 Debug |
| `INFO` | `INFO` | 9 | INFO | 6 Informational |
| `WARNING` | `WARNING` | 13 | WARN | 4 Warning |
| `ERROR` | `ERROR` | 17 | ERROR | 3 Error |
| `CRITICAL` | `CRITICAL` | 21 | FATAL | 2 Critical |

Use `INFO` for an expected business outcome, even a "declined" or "rejected" one, because the system worked as designed. Use `WARNING` for something degraded but handled (a retry, a fallback, temporary slowness). Reserve `ERROR` for something that actually needs attention, and `CRITICAL` for a service that cannot keep working. A routine, already-handled retry logged at `ERROR` is a common anti-pattern; it belongs at `WARNING`.

## Output contract

Every record is exactly one JSON object per line, UTF-8, no pretty-printing. Keys are flat, lowercase, dotted strings, never nested objects. An optional field whose value is legitimately unknown is `null`, never an empty string. Trace correlation fields (`trace_id`, `span_id`, `trace_flags`) appear only when a trace context is bound; otherwise they are omitted entirely, never emitted as `null`. Attribute counts default to 128 and value lengths are unbounded by default, both configurable; overflow discards excess attributes and truncates oversized values rather than raising.

A real, single-line record (the actual wire format; never pretty-printed in production):

```json
{"timestamp":"2026-09-10T17:52:14.460123Z","severity_text":"INFO","severity_number":9,"event_name":"payment.authorization.completed","body":null,"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"53995c3f42cd8ad8","trace_flags":"01","http.request.id":"0199358a-7c2e-7b1d-8f3a-2c9e4b6d1a0f","otel.scope.name":"payments.service","app.payment.amount":15000,"app.identity":"api.payments","service.name":"payments","service.namespace":null,"service.version":"1.4.0","service.instance.id":"5f1c2d9e-3b4a-4c8e-9f10-2a6b7c8d9e0f","deployment.environment.name":"production","telemetry.sdk.name":"semlog","telemetry.sdk.version":"<installed-semlog-version>","telemetry.sdk.language":"python"}
```

`telemetry.sdk.version` is shown as a placeholder, not a literal: it always equals the exact installed semlog version that also appears in the header `llm()` and the CLI generate at read time (see `llm` above), so this stored guide never carries a hardcoded semlog version that could drift from the package it ships in. `service.version` here is unrelated: it is the illustrative user service's own version (`payments`, in this example), resolved independently per its own precedence chain, not semlog's version.

The full normative field order, presence rules, and the published JSON Schema live in STANDARDS.md and `schemas/log-record.schema.json`.

## Configuration and precedence

All `configure()` parameters are keyword-only; there is no settings object or dict. For the identity and limit parameters below, precedence, highest to lowest, is always: explicit `configure()` parameter > `OTEL_*` environment variable > `pyproject.toml` (`[project]`, then `[tool.poetry]` as a fallback) > built-in default. A forced value (parameter or environment variable) always wins over a detected one. `mode` follows its own precedence chain instead, described in "Execution modes and the semlog=True keyword" below.

| Group | Parameter | Default | Notes |
|---|---|---|---|
| Identity | `service_name` | detected | parameter > `OTEL_SERVICE_NAME` > `OTEL_RESOURCE_ATTRIBUTES` > `pyproject.toml` |
| Identity | `service_version` | detected | parameter > `OTEL_RESOURCE_ATTRIBUTES` > installed package version > `pyproject.toml` |
| Identity | `service_namespace` | `None` | parameter > `OTEL_RESOURCE_ATTRIBUTES` |
| Identity | `service_instance_id` | one UUIDv4 per process | parameter > `OTEL_RESOURCE_ATTRIBUTES` |
| Identity | `environment` | `None` | parameter > `OTEL_RESOURCE_ATTRIBUTES` |
| Identity | `identity` | `None` | one value per level named in `identity_levels` |
| Identity | `identity_levels` | `("role", "component")` | level names composed into one `{namespace}.identity` field |
| Output | `level` | `"INFO"` | effective root logger level |
| Output | `namespace` | `"app"` | root of the custom attribute namespace |
| Output | `capture_loggers` | `()` | third-party loggers whose own handlers are removed, with propagation turned on |
| Output | `search_dir` | `None` (current working directory) | directory `pyproject.toml` detection searches upward from |
| Privacy | `redact_keys` | `()` | added on top of the built-in redaction list; never removes a built-in key |
| Distributed context | `baggage_allow` | `()` | process-wide default; `operation()`/`WSGIMiddleware`/`ASGIMiddleware` fall back to this when their own `baggage_allow` is left as `None` |
| Distributed context | `baggage_prefix` | `"baggage."` | attribute-key prefix for copied baggage members |
| Distributed context | `accept_inbound_baggage` | `True` | `False` makes every middleware/`operation()` ignore an inbound `baggage` header entirely |
| Limits | `max_attributes` | 128 (or environment variable) | see STANDARDS.md |
| Limits | `max_attribute_length` | no limit (or environment variable) | see STANDARDS.md |
| Transport | `queue` | `True` | `False` = synchronous write, no internal queue, no writer thread |
| Transport | `queue_size` | `10000` | maximum queued lines; `configure()` raises `ValueError` for a non-positive value |
| Transport | `overflow` | `"block"` | `"block"` waits for room (default, nothing lost); `"drop"` never waits and drops low-severity records first; any other value raises `ValueError` |
| Mode | `mode` | `None` (resolves to `"full"`) | `"full"`, `"hybrid"` or `"off"`; parameter > `SEMLOG_MODE` > `[tool.semlog].mode` (3.11+) > `"full"`; any other value raises `ValueError` naming the value and its source |
| Catalog | `catalog` | `None` | event catalog document (JSON) |
| Catalog | `catalog_mode` | `"off"` without a catalog, `"warn"` with one | `"off"`, `"warn"`, `"strict"` |

Recognized environment variables: `OTEL_SERVICE_NAME`; `OTEL_RESOURCE_ATTRIBUTES` (for `service.namespace`, `service.version`, `service.instance.id`, `deployment.environment.name`, and as a fallback for `service.name`); the pairs `OTEL_ATTRIBUTE_COUNT_LIMIT`/`OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT` and `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`/`OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`; `SEMLOG_MODE` (for `mode`).

On Python 3.11 and later, `service_name` and `service_version` are detected by reading `pyproject.toml` with stdlib `tomllib`. Python 3.10 has no `tomllib`, and semlog does not bundle a third-party TOML reader: on 3.10, `pyproject.toml` is not read at all, resolution falls back to the explicit parameter, the `OTEL_*` variables, or installed package metadata, and a distinct startup diagnostic notes that detection was skipped for this reason (not because the file was missing).

### Execution modes and the semlog=True keyword

`mode` selects how much of semlog is active in a process: `"full"` (the default, and the right choice for a new service), `"hybrid"` (the adoption path for a service already running in production, with its own existing log lines), and `"off"` (behaves as if semlog were never installed).

Resolution precedence, highest to lowest:

1. the `mode` keyword argument to `configure()`;
2. the `SEMLOG_MODE` environment variable;
3. the `mode` key under `[tool.semlog]` in `pyproject.toml`:

```toml
[tool.semlog]
mode = "hybrid"
```

4. the default, `"full"`.

Reading `pyproject.toml` needs `tomllib`, available on Python 3.11 and later; on 3.10 this source is skipped entirely. The file is searched from the current working directory (or `configure(search_dir=...)`) upward through its parents; a container image built without the project's source tree, or without the working directory set to it, often has none to find, and this source is silently skipped the same as on 3.10. An empty `SEMLOG_MODE` is treated as absent, the same as unset, and falls through to the next source; this does not apply to `[tool.semlog].mode`, where an empty string is a real declared value. A value that is present but outside `"full"`, `"hybrid"`, `"off"`, from any of the three sources, raises `ValueError` naming both the invalid value and its source.

Once `semlog` is imported, every `logging.Logger` call accepts the keyword-only `semlog=True`, in every mode, including before `configure()` runs; this keyword never raises. Its effect depends on `mode`:

- **`full`**: `semlog=True` has no additional effect: every call already becomes one JSON record regardless of the keyword.
- **`hybrid`**: `configure()` never touches the root logger's existing handlers or level. A call made with `semlog=True`, for example `logger.info("event.name", extra={...}, semlog=True)`, is hidden from every `StreamHandler` and instead emitted as one semlog JSON record; every other call keeps printing exactly as it did before `semlog` was installed. This suppression is a patch on `StreamHandler.handle` itself: a handler outside that synchronous dispatch, such as a `logging.handlers.QueueHandler` paired with a `QueueListener`, or a `logging.handlers.MemoryHandler`, may still render a marked record as text, and so may a `StreamHandler` subclass that overrides `handle()` without calling `super().handle()`; none of this is a defect.
- **`off`**: `semlog=True` produces no JSON output and no other side effect of its own; `configure()` installs nothing, `operation()`, `bind()` and both middlewares keep working as inert pass-throughs. A process that starts in `off` mode behaves as if semlog were never installed. Reconfiguring an already-running process from `full`/`hybrid` to `off` leaves an already-installed JSON pipeline attached; `off` is a process-start switch, not a live toggle. Removing `semlog` from a service while `semlog=True` call sites remain raises `TypeError` at an enabled call site rather than failing silently; strip the keyword from call sites before uninstalling.

## FastAPI recipe

```python
import logging

import semlog
from fastapi import FastAPI
from semlog import ASGIMiddleware

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
app = ASGIMiddleware(app, log_requests=True)
# log_requests=True emits one http.server.request event per request (INFO on
# success, ERROR when the application raises an unhandled exception), carrying
# http.request.method, url.path, http.response.status_code and event.duration
# (nanoseconds). It never includes url.query. Default is False: no extra event.

logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    ...


@app.get("/reports/{report_id}")
def generate_report_sync(report_id: str):
    # A sync `def` endpoint also keeps trace context: Starlette runs it in a
    # threadpool worker with an explicit contextvars.copy_context().
    logger.info("report.generation.started", extra={"app.report.id": report_id})
    ...
```

Equivalent, using FastAPI's own middleware stack instead of wrapping `app`:

```python
app = FastAPI()
app.add_middleware(semlog.ASGIMiddleware, log_requests=True)
# A global @app.exception_handler(Exception) runs in Starlette's outermost
# ServerErrorMiddleware, outside semlog's own bound context: its logs carry
# no trace_id, and the ERROR completion event has no status code. Wrap
# `app` instead (above) to keep exception handling inside semlog's context.
```

## Django recipe

Add the class to `settings.MIDDLEWARE`, and call `configure()` from `AppConfig.ready()`, since Django applies its own logging configuration during startup, after `settings.py` is read but before `ready()` runs.

```python
# settings.py
MIDDLEWARE = [
    # ... other middleware ...
    "semlog.DjangoMiddleware",
]
```

```python
# apps.py
import semlog
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        semlog.configure(service_name="my-django-service")
```

Placed outermost (first in `MIDDLEWARE`, the default recommendation), it covers every other middleware's own logging, but a competing `process_exception` closer to the view can pre-empt semlog's own hook; placed closer to the view instead, it maximizes exception-capture priority at the cost of narrower context coverage.

`process_exception` stashes an unhandled view exception without swallowing it, so the completion event still carries the real final status and a rendered traceback; Django's own exception handling is never modified. Two permanent limitations, neither a defect: it cannot observe an exception raised by another middleware's own code, since only a view exception ever reaches it, and a competing `process_exception` registered closer to the view can return a response first, pre-empting semlog's own hook for that request entirely.

Using the class together with `WSGIMiddleware` or `ASGIMiddleware` wrapping the same application is unsupported: each layer parses the inbound `traceparent` independently and mints its own `span_id`, so with the completion event enabled on both, telemetry is duplicated.

The completion event is enabled through the Django setting `SEMLOG_LOG_REQUESTS` (default `False`, read once, when Django constructs the middleware) instead of a constructor keyword, since Django instantiates a `MIDDLEWARE` entry with a single positional argument:

```python
# settings.py
SEMLOG_LOG_REQUESTS = True
```

A non-boolean value raises `ValueError` naming the value and its source. Passing `log_requests=` directly still overrides the setting.

## When to use each API

| Situation | API | Where |
|---|---|---|
| Starting the process, once | `configure(...)` | At the service entry point, before the application is built |
| Serving HTTP (WSGI or ASGI) | `WSGIMiddleware` / `ASGIMiddleware` | Wrapping the application once; every request is traced automatically |
| Serving HTTP with Django | `DjangoMiddleware` | Added once to `settings.MIDDLEWARE`; every request is traced automatically |
| Logging any event | `logger.info` / `.warning` / `.error` / `.exception` / ... | Any call site, always |
| Adding a field to every later record in the same request or operation | `bind(attributes)` | At the start of handling, or as soon as the value is known |
| Calling another service | `inject(headers, trusted=...)` | Immediately before the outbound call |
| Work with no HTTP request (jobs, scheduled tasks, queue consumers, scripts) | `operation(headers=None)` | Wrapping the body of the work |
| Before the process exits, or in tests | `flush(timeout=None)` | Immediately before `os._exit()`, or at the end of a test |
| Reading the complete agent-facing reference | `llm()` / `python -m semlog llm` | Once per agent session, or whenever this guide is needed offline |

In a typical HTTP service, `configure()` and the middleware are used exactly once, `logger.*` is used constantly, and `bind()` is used occasionally. `operation()`, `inject()`, and `flush()` are for specific cases: work with no HTTP request, outbound calls, and process shutdown.

## Migration checklist

- [ ] No f-string, `%`-formatting, or `.format()` interpolated into the log message; pass variables via `extra`.
- [ ] No exception rendered as `f"Error: {e}"` or `logger.error(e)`; use `logger.exception(...)` exactly once, at the boundary that handles it.
- [ ] No duplicate `logger.exception`/`logger.error` for the same exception across multiple call layers.
- [ ] No expected business outcome logged at `ERROR`; use `INFO` with an outcome field.
- [ ] No routine, already-handled retry logged at `ERROR`; use `WARNING`.
- [ ] No `dict` or `json.dumps(...)` used as the log message; pass the fields via `extra` instead.
- [ ] No free-text prefix used to carry context (for example `f"[fraud] ..."`); use an event name plus `extra` fields.
- [ ] No secrets or full request/response bodies logged; log only the specific fields a case needs.
- [ ] No full URL with its query string logged; use `server.address`/`url.path`, never `url.query`.
- [ ] No per-item log line inside a loop over a large collection; log one summary event with a count.
- [ ] No "entering function" breadcrumb at `INFO`: trace context already reconstructs the call flow; use `DEBUG` if a local trace is still needed.
- [ ] No `print()` call in application code.
- [ ] No hand-built request id: the middlewares already generate `http.request.id`.
- [ ] No identical field repeated on every call within the same scope; call `bind()` once instead.
- [ ] No outbound call missing `inject(headers)`: downstream services need `traceparent`/`baggage` to correlate.
- [ ] No background job or scheduled task logging outside `operation()`: it has no trace context otherwise.
- [ ] No per-service `logging.basicConfig()`/`dictConfig()`; call `configure()` once instead.

## Review checklist

A coding agent can apply these mechanically while reviewing or writing call sites.

| When you see | Change it to |
|---|---|
| `logger.info(f"...{var}...")` | `logger.info("static.event_name", extra={"app.field": var})` |
| `logger.error(f"Error: {e}")` or `logger.error(e)` | `logger.exception("static.event_name", extra={...})`, called exactly once |
| `logger.error(...)` for an outcome the application already handled | `logger.info(...)` with an outcome field |
| `logger.error(...)` for a routine, handled retry | `logger.warning(...)` |
| `logger.info(json.dumps({...}))` | `logger.info("static.event_name", extra={...})` |
| `print(...)` anywhere in application code | `logging.getLogger(__name__).info(...)` |
| A log call carrying a raw secret, token, or full request/response body | Remove it, or pass only the specific safe fields via `extra` |
| A log line containing the full URL with its query string | `extra={"server.address": ..., "url.path": ...}`, never `url.query` |
| An outbound HTTP call with no `inject(headers)` before it | Add `inject(headers)` (or `inject(headers, trusted=False)` at a trust boundary) |
| A background job or scheduled task with no `operation()` wrapper | Wrap its body in `with operation():` |
| An `extra` key that collides with a reserved `LogRecord` attribute name | Rename it under the configured namespace (for example `app.module` instead of `module`) |
| `bind()` called at module level or in a thread that does not inherit context | Move it inside `operation()`, or route process-wide fields through `configure(identity=...)` |
