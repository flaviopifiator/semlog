**English** | [Español](README.es.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/semlog-logo-dark.svg">
    <img alt="semlog" src="docs/assets/semlog-logo-light.svg" width="360">
  </picture>
</p>

# semlog

**Structured JSON logging for Python's standard `logging`, with OpenTelemetry field names and W3C Trace Context.**

[![CI](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml/badge.svg)](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml)
[![Python 3.10-3.14](https://img.shields.io/badge/python-3.10--3.14-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI 0.71+](https://img.shields.io/badge/FastAPI-%E2%89%A5%200.71-009688?logo=fastapi&logoColor=white)](.github/workflows/ci.yml)
[![Django 3.2.9+](https://img.shields.io/badge/Django-%E2%89%A5%203.2.9-092E20?logo=django&logoColor=white)](.github/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Requirements proven: 114/114](https://img.shields.io/badge/requirements%20proven-114%2F114-brightgreen)](STANDARDS.md)
[![PyPI](https://img.shields.io/pypi/v/semlog)](https://pypi.org/project/semlog/)

## What semlog is

semlog is a logging library for Python services. Application code keeps calling `logging.getLogger(__name__)` and the standard `Logger` methods; semlog decides what every resulting record looks like, and writes it as one JSON object per line on `stdout`.

The name is `semantic` plus `log`, after OpenTelemetry's Semantic Conventions: the specification that fixes what each telemetry field is called and what it means. semlog applies those names to log records, so `service.name`, `trace_id` and `url.path` mean the same thing in every service that emits them.

That is the difference from free-form message text. `f"User {user_id} purchased {quantity} units"` has to be read back by a person, or parsed by a rule written once per service. A field with a stable name and a stable meaning can be queried, aggregated and alerted on across services instead, with no parsing rule at all.

The WSGI, ASGI and Django middlewares read `traceparent`, `tracestate` and allowlisted `baggage` from the inbound request, so every record of a request carries the same `trace_id`, and `inject()` propagates that context to outbound calls; no tracing SDK is needed. Third-party loggers that propagate to the root logger go through the same pipeline, and `capture_loggers` covers the ones that install their own handlers.

## Why semlog

Two properties decide whether semlog fits a service you already have:

- **It goes into a service that is already running.** In [`hybrid` mode](#modes), every line the service prints today keeps printing byte-identically. A call marked `semlog=True` is hidden from that printed output and becomes one JSON record instead. No existing log line has to be rewritten first. `SEMLOG_MODE=off` returns the process to its previous behavior at its next start, with no code change.
- **Zero runtime dependencies.** semlog is built on the standard library alone, and its wheel declares no `Requires-Dist` entries. That matters where every new dependency has to be reviewed before it ships.

## Philosophy

semlog takes a few positions about what a log record is, and about what a logging library may ask of the code around it.

- **A log record is a contract, not free text.** The field names, their order and the `null` rules are specified in [STANDARDS.md](STANDARDS.md) and published as a JSON Schema ([`schemas/log-record.schema.json`](schemas/log-record.schema.json)). Removing or renaming a field is a major version change, and so is adopting a later Semantic Conventions rename.
- **The standard library is enough.** semlog is built from `logging`, `json`, `contextvars` and `queue`, and the built wheel declares no `Requires-Dist` entries, so adopting it adds nothing to a dependency review.
- **Application code should not have to learn a second logging API.** Call sites stay on `logging.getLogger(__name__)` and the standard `Logger` methods, and the public surface is ten names. semlog decides what a record looks like instead of asking every call site to be rewritten against a logger object of its own.
- **Adoption should be reversible.** A library that can only be adopted by rewriting every existing log line does not get adopted in a service that is already running, so `hybrid` mode rewrites none of them, and `SEMLOG_MODE=off` takes semlog back out from the environment, with no code change. See [Modes](#modes).
- **A rule no test cites is not a rule.** Every normative requirement in [STANDARDS.md](STANDARDS.md) carries an identifier and at least one test that cites it by that identifier. The suite fails when a requirement has no proving test, and when a test cites an identifier that does not exist. STANDARDS.md declares 114 requirements today.
- **A field name should mean the same thing to a person, to a tool and to an agent.** The names are not invented here: `severity_text`, `severity_number`, `trace_id`, `span_id`, `service.*` and `telemetry.sdk.*` follow the OpenTelemetry Logs Data Model and its Semantic Conventions, pinned to v1.44.0, and trace propagation follows W3C Trace Context. The agent-facing guide ships inside the package, so a coding agent applies the same rules offline (`python -m semlog llm`).

## Installation

semlog requires Python 3.10 or later.

```bash
pip install semlog
```

With uv, add semlog to a project:

```bash
uv add semlog
```

Or install it into an environment:

```bash
uv pip install semlog
```

## Quick start

Save this as `app.py`:

```python
import logging

import semlog

semlog.configure(service_name="checkout")

logger = logging.getLogger(__name__)
logger.info("order.created", extra={"app.order.id": "ord_42", "app.order.total": 1500})
```

Run `python app.py`. It prints one JSON line:

```json
{"timestamp":"2026-09-14T18:49:31.659966Z","severity_text":"INFO","severity_number":9,"event_name":"order.created","body":null,"otel.scope.name":"__main__","app.order.id":"ord_42","app.order.total":1500,"service.name":"checkout","service.namespace":null,"service.version":null,"service.instance.id":"b821b60b-7f5e-44a9-88f8-7cf734286abe","deployment.environment.name":null,"telemetry.sdk.name":"semlog","telemetry.sdk.version":"0.4.0","telemetry.sdk.language":"python"}
```

Three rules keep records useful:

- The message is a static event name, such as `order.created`, never an f-string.
- Variables go in `extra`, under `app.` or an OpenTelemetry key such as `url.path`.
- `logger.exception(...)` is called once, where the exception is handled.

### Public API

semlog exposes exactly ten names. Everything else stays standard `logging`.

| Name | Use it to |
|---|---|
| `configure(...)` | Configure the process once, at startup |
| `WSGIMiddleware(app)` | Wrap a WSGI application: trace context and `http.request.id` on every record of a request |
| `ASGIMiddleware(app)` | The same, for an ASGI application |
| `DjangoMiddleware` | A `settings.MIDDLEWARE` entry serving both sync and async Django views |
| `AiohttpMiddleware()` | An `aiohttp.web` middleware entry, registered on the application |
| `operation(headers=None)` | Correlate work outside HTTP, such as jobs, queue consumers and CLI commands |
| `bind(attributes)` | Add fields to every later record of the current request or operation |
| `inject(headers, *, trusted=True)` | Propagate trace context to an outbound call |
| `flush(timeout=None)` | Wait until every queued record is written, before `os._exit()` or in tests |
| `llm()` | Return the agent guide bundled with the installed version (also `python -m semlog llm`) |

Full signatures and semantics are in the [agent guide](src/semlog/agent_guide.md).

## Framework recipes

Each framework has more than one supported way in, and the routes are not equivalent. Every example below runs as written; a `# settings.py` or `# apps.py` marker names the file the block belongs in.

Every route can add the same completion event: one `http.server.request` record per request, INFO on success, ERROR when the application raises an unhandled exception, carrying `http.request.method`, `url.path`, `http.response.status_code` and `event.duration` in nanoseconds, and never `url.query`. A wrapper and the aiohttp middleware enable it with `log_requests=True`, the Django middleware class with the `SEMLOG_LOG_REQUESTS` setting; all default to off. In `hybrid` mode the event needs one more line in the application; see [Modes](#modes).

### FastAPI

`ASGIMiddleware` is a pure ASGI 3.0 middleware with no FastAPI import of its own, so both routes below apply unchanged to a plain Starlette application and to any other ASGI 3.0 application.

**Route 1, the application's own middleware stack.**

```python
import logging

import semlog
from fastapi import FastAPI

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
app.add_middleware(semlog.ASGIMiddleware, log_requests=True)

logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    return {"id": order_id}
```

A `GET /orders/ord_42` carrying an inbound `traceparent` prints the endpoint's own record and then the completion event, both with that request's `trace_id`, `span_id` and `http.request.id`.

One caveat belongs to this route alone. A global `@app.exception_handler(Exception)` runs inside Starlette's outermost `ServerErrorMiddleware`, above the layer `add_middleware` installs and therefore outside semlog's bound context: records logged inside that handler carry no `trace_id`, and the ERROR completion event is emitted before the handler's response is sent, so its `http.response.status_code` is `null`.

**Route 2, wrapping the application.**

```python
import logging

import semlog
from fastapi import FastAPI
from semlog import ASGIMiddleware

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    return {"id": order_id}


app = ASGIMiddleware(app, log_requests=True)
```

The wrapping line goes after the routes are registered: `ASGIMiddleware` is an ASGI callable, not a `FastAPI` instance, so an `@app.get(...)` below it raises `AttributeError`. semlog's layer is now the outermost one, so a global exception handler runs inside its context: the records it logs carry the request's `trace_id`, and the completion event carries the real final `http.response.status_code`.

**Which one to choose.** Route 1 if the service has no global `Exception` handler, or if the middleware stack has to stay under FastAPI's own control. Route 2 if it does have one and those records need the trace id.

A sync `def` endpoint keeps the same context under either route: Starlette runs it in a worker thread that inherits the caller's context.

### Django

`configure()` goes in an `AppConfig.ready()`, not in `settings.py`. Django reads `settings.py`, then applies the project's logging configuration, and only afterwards calls `ready()`. A project whose `LOGGING` setting declares a `root` entry, the common shape, has its root handlers replaced at that point: a `configure()` call made from `settings.py` loses its pipeline moments later, the service goes back to printing plain text, and the completion event disappears with no error anywhere. Called from `ready()`, it runs after that configuration and survives it.

**Route 1, the middleware class in `settings.MIDDLEWARE`.** This is the recommended route.

```python
# settings.py
MIDDLEWARE = [
    "semlog.DjangoMiddleware",
    # ... other middleware ...
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

Placed outermost (first in `MIDDLEWARE`, the default recommendation), it covers every other middleware's own logging, but a competing `process_exception` closer to the view can pre-empt semlog's own hook (see below); placed closer to the view instead, it maximizes exception-capture priority at the cost of narrower context coverage. Pick whichever tradeoff fits the deployment.

`process_exception` stashes an unhandled view exception without swallowing it, so the completion event still carries the real final status and a rendered traceback; Django's own exception handling is never modified. Two permanent limitations, neither a defect: it cannot observe an exception raised by another middleware's own code, since only a view exception ever reaches it, and a competing `process_exception` registered closer to the view can return a response first, pre-empting semlog's own hook for that request entirely.

That traceback is rendered once per exception instance. Django's own `django.request` logger propagates to the root logger, so in a service where it is left enabled it logs the same exception first and renders the traceback there; the completion event then carries `exception.type` and `exception.message` without repeating it.

This route's completion event is enabled through the Django setting [`SEMLOG_LOG_REQUESTS`](#semlog_log_requests) instead of a constructor keyword, since Django instantiates a `MIDDLEWARE` entry with a single positional argument.

**Route 2, wrapping the WSGI or the ASGI application.** For a deployment that prefers to keep semlog outside `MIDDLEWARE` entirely, wrap whichever callable the server imports.

```python
# wsgi.py
import os

from django.core.wsgi import get_wsgi_application

import semlog

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

application = semlog.WSGIMiddleware(get_wsgi_application(), log_requests=True)
```

```python
# asgi.py
import os

from django.core.asgi import get_asgi_application

import semlog

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

application = semlog.ASGIMiddleware(get_asgi_application(), log_requests=True)
```

`configure()` still belongs in `AppConfig.ready()` here, and `semlog.DjangoMiddleware` stays out of `MIDDLEWARE`. This route gives up exception detail, and that is not a defect either: Django converts a view exception into a 500 response before the wrapper sees it, so the wrapper has nothing left to render and cannot attach the traceback. Its completion event is an INFO record carrying `http.response.status_code` 500 and no `exception.*` field at all, where route 1 emits an ERROR record naming the exception. Trace context binding, `http.request.id` and streaming bodies behave identically in both.

One project-side detail belongs to this route: it imports `semlog` before Django reads its settings, so a project whose `LOGGING` dictionary omits `"disable_existing_loggers": False` disables every logger that already existed, semlog's completion-event logger among them, and the event stops appearing with no error. Keep that key in the dictionary.

**Do not combine the two routes.** Using the class together with `WSGIMiddleware` or `ASGIMiddleware` wrapping the same application is unsupported: each layer parses the inbound `traceparent` independently and mints its own `span_id`, so with the completion event enabled on both, telemetry is duplicated. Use exactly one.

### aiohttp

`AiohttpMiddleware` is an `aiohttp.web` middleware, registered on the application itself. semlog never imports aiohttp: there is no package extra to install, and the wheel still declares zero dependencies.

```python
import logging

import semlog
from aiohttp import web

semlog.configure(service_name="my-aiohttp-service")

logger = logging.getLogger(__name__)


async def get_order(request):
    order_id = request.match_info["order_id"]
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    return web.json_response({"id": order_id})


app = web.Application(middlewares=[semlog.AiohttpMiddleware(log_requests=True)])
app.router.add_get("/orders/{order_id}", get_order)

web.run_app(app, access_log=None)
```

Register it first in `middlewares=[...]`, the outermost position, so every other middleware's own logging runs inside the request context. Its completion event is enabled with `log_requests=True`, like the WSGI and ASGI wrappers; unlike Django, no setting is read.

`access_log=None` belongs with `log_requests=True`. aiohttp's own access logger writes one plain-text line per request, so leaving it enabled reports every request twice: once as a JSON record and once as text.

A handler that raises `web.HTTPNotFound`, or any other `web.HTTPException`, is answered by aiohttp with that status, so its completion event is an INFO record carrying it, with no traceback. Every other exception is a real failure that aiohttp answers 500 for, including a `ClientResponseError` raised by an outbound call the handler made: that one gets an ERROR record with the traceback, never the outbound call's own status. Only `asyncio.CancelledError` produces no event at all.

`event.duration` covers the handler's execution, not the response transmission: aiohttp sends the response after the middleware chain has returned. A `StreamResponse` the handler writes to, and a WebSocket session it keeps open, are inside that interval, because the handler awaits them; a deferred asynchronous body and a `FileResponse` send happen after it, and code running there sees no bound context. Read the context inside the handler, or carry it with `contextvars.copy_context()` into work handed to an executor.

## Configuration

Every `configure()` parameter is keyword-only; there is no settings object or dictionary. An invalid value or combination raises `ValueError` immediately, at startup, never later at runtime.

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
| Output | `search_dir` | `None` (current working directory) | directory that `pyproject.toml` detection searches upward from |
| Privacy | `redact_keys` | `()` | added to the built-in redaction list, which cannot be turned off |
| Distributed context | `baggage_allow` | `()` | baggage keys copied into attributes; process-wide default for the middlewares and `operation()` |
| Distributed context | `baggage_prefix` | `"baggage."` | attribute-key prefix for copied baggage members |
| Distributed context | `accept_inbound_baggage` | `True` | `False` ignores an inbound `baggage` header entirely, for public trust boundaries |
| Limits | `max_attributes` | 128 (or environment variable) | see STANDARDS.md section 6 |
| Limits | `max_attribute_length` | no limit (or environment variable) | see STANDARDS.md section 6 |
| Transport | `queue` | `True` | `False` writes synchronously, with no internal queue and no writer thread |
| Transport | `queue_size` | `10000` | maximum number of queued lines |
| Transport | `overflow` | `"block"` | `"block"` or `"drop"`, see [Queue overflow](#queue-overflow) |
| Mode | `mode` | `None` (resolves to `"full"`) | `"full"`, `"hybrid"` or `"off"`; parameter > `SEMLOG_MODE` > `[tool.semlog].mode` (3.11+) > `"full"` |
| Catalog | `catalog` | `None` | event catalog document (JSON) |
| Catalog | `catalog_mode` | `"off"` without a catalog, `"warn"` with one | `"off"`, `"warn"` or `"strict"` |

### Precedence and environment variables

For the identity and limit parameters below, precedence, from highest to lowest, is always:

1. an explicit `configure()` parameter;
2. the `OTEL_*` environment variable;
3. `pyproject.toml`, on Python 3.11 and later only;
4. the built-in default.

`mode` follows its own precedence chain instead, described in [Modes](#modes).

Recognized environment variables:

| Variable | Sets |
|---|---|
| `OTEL_SERVICE_NAME` | `service.name` (`service_name`) |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace` (`service_namespace`), `service.version` (`service_version`), `service.instance.id` (`service_instance_id`) and `deployment.environment.name` (`environment`); also the fallback for `service.name` (`service_name`) |
| `OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT`, `OTEL_ATTRIBUTE_COUNT_LIMIT` | the `max_attributes` limit |
| `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` | the `max_attribute_length` limit |
| `SEMLOG_MODE` | `mode` (see [Modes](#modes)) |

When both variables of a limit are set, the `OTEL_LOGRECORD_*` variable wins over the generic `OTEL_ATTRIBUTE_*` one.

`pyproject.toml` detection:

- On Python 3.11 and later, `service_name` and `service_version` are read from `[project]` with the standard library's `tomllib`; `service_name` falls back to `[tool.poetry]`.
- Python 3.10 has no `tomllib`, so on 3.10 `pyproject.toml` is not read and a startup diagnostic says why.

### Queue overflow

Each record is rendered on the calling thread and queued for a single writer thread that writes it to `stdout`. When the queue is full, `overflow` decides what happens:

| Mode | Behavior | Use it when |
|---|---|---|
| `overflow="block"` (default) | The call waits for room in the queue; no record is lost | No record may be lost and an occasional wait is acceptable |
| `overflow="drop"` | The call never waits. At 90% capacity, records below `WARNING` are dropped; the last 10% is reserved for `WARNING`, `ERROR` and `CRITICAL`. Every drop is counted and reported | Application latency matters more than log completeness |
| `queue=False` | Synchronous write, with no queue and no writer thread | Short scripts, debugging, environments without threads |

### SEMLOG_LOG_REQUESTS

Not a `configure()` parameter, and deliberately kept out of the table above: Django instantiates a `settings.MIDDLEWARE` entry with a single positional argument, so no keyword from `configure()` can reach `DjangoMiddleware.__init__` through `settings.MIDDLEWARE` directly. Set the Django setting instead, to enable [`DjangoMiddleware`](#django)'s own completion event, the same one `WSGIMiddleware`/`ASGIMiddleware` enable through their own `log_requests=True` constructor keyword:

```python
# settings.py
SEMLOG_LOG_REQUESTS = True
```

Default `False`, read once, when Django constructs the middleware. A non-boolean value raises `ValueError` naming the value and its source. It is a single boolean, not a configuration object or dictionary, and it adds no public name.

## Modes

`mode` selects how much of semlog is active in a process: `"full"` (the default, and the right choice for a new service), `"hybrid"`, and `"off"`.

For a service already running in production, with its own existing log lines, `hybrid` is the adoption path. Every line the service already prints keeps printing byte-identically. A call written with the `semlog=True` keyword is hidden from that same printed output and becomes one JSON record instead; `logger.info("event.name", extra={...}, semlog=True)` is the shape of such a call. `off` behaves as if semlog were never installed, except that the keyword itself never raises, so it stays safe to leave in call sites while rolling back. `full` is the end state, and the default for a new service with no existing log lines to preserve: the adoption path is `hybrid`, then `full` once its output has been reviewed. `mode` is switched by environment, with no code change.

- **`full`**: every log call goes through semlog's pipeline and becomes one JSON record per line, exactly as shown in [Output](#output).
- **`hybrid`**: `configure()` never touches the root logger's existing handlers or level. A call made with `semlog=True` is hidden from every `StreamHandler` and instead emitted as one semlog JSON record; every other call keeps printing exactly as it did before semlog was installed. See [Limitations](#limitations) for the exact scope of this suppression.
- **`off`**: `configure()` installs nothing and does not touch the root logger. `semlog=True` still never raises, but produces no JSON output and no other side effect of its own; `operation()`, `bind()` and both middlewares keep working as inert pass-throughs.

### Configuration sources

`mode` resolves from, in order of precedence:

1. the `mode` keyword argument to `configure()`;
2. the `SEMLOG_MODE` environment variable;
3. the `mode` key under `[tool.semlog]` in `pyproject.toml`;
4. the default, `"full"`.

```toml
[tool.semlog]
mode = "hybrid"
```

Reading `pyproject.toml` needs the standard library's `tomllib`, available on Python 3.11 and later. On Python 3.10 this source is skipped entirely, and resolution falls through to the next one. The file is searched from the current working directory (or `configure(search_dir=...)`, when given) and upward through its parent directories. A container image built without the project's source tree, or with its working directory set elsewhere, often has no `pyproject.toml` to find; that source is then skipped silently, the same as on Python 3.10. An empty `SEMLOG_MODE` counts as absent and falls through to the next source. `[tool.semlog].mode` is different: there, an empty string is a real declared value. A value outside `"full"`, `"hybrid"` and `"off"`, from any of the three sources, raises `ValueError` naming both the invalid value and the source it came from.

### The root logger level in hybrid mode

`hybrid` deliberately never touches the root logger, its level included, and the standard library leaves it at `WARNING`. Severity filtering for a marked call follows the standard library's ordinary effective-level rules, so in a process that has never set a root level, an `INFO` record marked `semlog=True` is filtered out before hybrid's routing ever sees it, and no JSON is written. The `http.server.request` completion event is `INFO` on a successful request, so it disappears too, silently; the `ERROR` one, emitted when the application raises, still comes through.

`configure(level=...)` does not help: that parameter sets the root level in `full` mode only. Instead, set the root level in the application, before or after `configure()`:

```python
import logging

import semlog

logging.getLogger().setLevel(logging.INFO)
semlog.configure(service_name="checkout", mode="hybrid")
```

A service that already calls `logging.basicConfig(level=logging.INFO)` or applies its own `dictConfig` with a root level needs nothing extra. `full` mode is unaffected: it always sets an explicit root level, `INFO` by default.

### Limitations

- **Hybrid's suppression is scoped to `StreamHandler.handle`.** Only `logging.StreamHandler` instances and subclasses that reach that method are hidden from a marked record. A handler outside that synchronous dispatch, such as a `logging.handlers.QueueHandler` paired with a `QueueListener`, or a `logging.handlers.MemoryHandler`, may still render a marked record as text; so may a `StreamHandler` subclass that overrides `handle()` without calling `super().handle()`. None of this is a defect, only the documented edge of what a method-level patch can reach.
- **`off` is a process-start switch, not a live toggle.** A process that starts in `off` mode (or with `SEMLOG_MODE=off`) behaves as if semlog were never installed. Reconfiguring an already-running process from `full` or `hybrid` to `off` leaves the JSON pipeline already installed on the root logger attached; it does not tear it down.
- **Uninstalling semlog while marked calls remain raises `TypeError`.** If `semlog` is removed from a service that still has `semlog=True` call sites, an enabled call at that call site fails with `TypeError`, rather than failing silently. Remove the keyword from call sites before uninstalling.

## Output

Every record is one JSON object per line, in UTF-8. This record was logged inside an `operation()` that received a `traceparent` header, and is indented here for reading; semlog never indents its output:

```json
{
  "timestamp": "2026-09-14T18:49:31.719135Z",
  "severity_text": "INFO",
  "severity_number": 9,
  "event_name": "payment.authorization.completed",
  "body": null,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "6914640b649ae6de",
  "trace_flags": "01",
  "http.request.id": "01a0a140-c547-72e5-9455-a8b81f4a94d7",
  "otel.scope.name": "payments.service",
  "app.payment.amount": 15000,
  "app.identity": "api.payments",
  "service.name": "payments",
  "service.namespace": null,
  "service.version": "1.4.0",
  "service.instance.id": "936c21f2-7be1-4006-933a-e84d39621fe7",
  "deployment.environment.name": "production",
  "telemetry.sdk.name": "semlog",
  "telemetry.sdk.version": "0.4.0",
  "telemetry.sdk.language": "python"
}
```

Keys are flat, dotted strings. Trace fields appear only when a trace context is bound, and an optional value that is unknown is `null`, never an empty string. Sensitive keys such as `password` or `token` are always replaced by `"REDACTED"`. The full field order, the severity mapping and the presence rules are in [STANDARDS.md](STANDARDS.md), and the formal schema is [`schemas/log-record.schema.json`](schemas/log-record.schema.json).

## Compatibility

The test suite runs in CI on CPython 3.10, 3.11, 3.12, 3.13 and 3.14; 3.15 also runs there and is allowed to fail. The package is pure Python (`py3-none-any` wheel). It ships a PEP 561 `py.typed` marker, so a type checker reads the annotations the package carries instead of treating it as untyped. Framework integration is tested in CI against these versions:

| Framework | Version | Python | Role |
|---|---|---|---|
| FastAPI (Starlette 0.17.1) | 0.71.0 | 3.10 | minimum |
| FastAPI (Starlette 1.6.0) | 0.141.1 | 3.10, 3.14 | latest |
| Django | 3.2.9 | 3.10 | minimum |
| Django | 5.2 LTS | 3.10, 3.14 | latest |
| Django | 6.1 | 3.12, 3.14 | latest |
| aiohttp | 3.10.0 | 3.10 | minimum |
| aiohttp | 3.14.3 | 3.10, 3.14 | latest |

FastAPI is tested with `async def` and sync `def` endpoints. Django is tested with sync views over WSGI and with async and sync views over ASGI. aiohttp is tested against a real application on a loopback socket, covering streaming, WebSocket and error responses.

## Documentation

- [STANDARDS.md](STANDARDS.md): the normative record contract, severity mapping, extension limits, pipeline guarantees and compatibility policy, with requirement IDs and a traceability annex.
- [`schemas/log-record.schema.json`](schemas/log-record.schema.json) and [`schemas/event-catalog.schema.json`](schemas/event-catalog.schema.json): JSON Schema 2020-12 for every record and for the event catalog.
- [Agent guide](src/semlog/agent_guide.md): the call-site reference for coding agents, with rules, migration and review checklists, shipped inside the package (`python -m semlog llm`). [llms.txt](llms.txt) is the agent-facing entry point to the repository.
- [SECURITY.md](SECURITY.md): how to report a vulnerability privately.
- [CHANGELOG.md](CHANGELOG.md): notable changes, in the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. semlog follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Contributing

Contributions are welcome as pull requests to `main`. Commit messages follow Conventional Commits, and every code change follows strict test-driven development. [AGENTS.md](AGENTS.md) has the setup, test, lint and build commands, the tooling policy and the branch protection rules. Report security vulnerabilities privately, as described in [SECURITY.md](SECURITY.md), never in a public issue.

## License

semlog is licensed under the [Apache License 2.0](LICENSE) (SPDX: `Apache-2.0`).
