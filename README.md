**English** | [Español](README.es.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/semlog-logo-dark.svg">
    <img alt="semlog" src="docs/assets/semlog-logo-light.svg" width="360">
  </picture>
</p>

# semlog

**Structured JSON logging for Python's standard `logging`: a stable, schema-backed record contract with OpenTelemetry field names, W3C Trace Context and zero dependencies.**

[![CI](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml/badge.svg)](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml)
[![Python 3.10-3.14](https://img.shields.io/badge/python-3.10--3.14-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI 0.71+](https://img.shields.io/badge/FastAPI-%E2%89%A5%200.71-009688?logo=fastapi&logoColor=white)](.github/workflows/ci.yml)
[![Django 3.2.9+](https://img.shields.io/badge/Django-%E2%89%A5%203.2.9-092E20?logo=django&logoColor=white)](.github/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Requirements proven: 103/103](https://img.shields.io/badge/requirements%20proven-103%2F103-brightgreen)](STANDARDS.md)
<!-- Enable after the first PyPI release:
[![PyPI](https://img.shields.io/pypi/v/semlog)](https://pypi.org/project/semlog/)
-->

## Why semlog

Application code keeps using `logging.getLogger(__name__)`; semlog decides what every record looks like and makes that shape a contract.

- **A stable JSON contract.** Field order, types and `null` rules are specified in [STANDARDS.md](STANDARDS.md) and published as a JSON Schema ([`schemas/log-record.schema.json`](schemas/log-record.schema.json)). Removing or renaming a field is a major version change.
- **OpenTelemetry field names.** `severity_text`, `severity_number`, `trace_id`, `span_id`, `service.*` and `telemetry.sdk.*` follow the OpenTelemetry Logs Data Model, with Semantic Conventions pinned to v1.44.0.
- **W3C Trace Context built in.** The WSGI and ASGI middlewares read `traceparent`, `tracestate` and allowlisted `baggage`, and `inject()` propagates them on outbound calls. No tracing SDK is needed.
- **Zero runtime dependencies.** semlog is built entirely on the standard library. Third-party loggers that propagate to the root logger go through the same pipeline, and `capture_loggers` covers the ones that install their own handlers.
- **Requirement traceability.** Every normative requirement in STANDARDS.md has an ID and at least one test that cites it; the test suite fails when a requirement has no citing test.

## Installation

semlog requires Python 3.10 or later. It is not on PyPI yet, so install it from the Git repository:

```bash
pip install "git+https://github.com/flaviopifiator/semlog.git"
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
{"timestamp":"2026-09-14T18:49:31.659966Z","severity_text":"INFO","severity_number":9,"event_name":"order.created","body":null,"otel.scope.name":"__main__","app.order.id":"ord_42","app.order.total":1500,"service.name":"checkout","service.namespace":null,"service.version":null,"service.instance.id":"b821b60b-7f5e-44a9-88f8-7cf734286abe","deployment.environment.name":null,"telemetry.sdk.name":"semlog","telemetry.sdk.version":"0.1.0","telemetry.sdk.language":"python"}
```

Three rules keep records useful:

- The message is a static event name, such as `order.created`, never an f-string.
- Variables go in `extra`, under `app.` or an OpenTelemetry key such as `url.path`.
- `logger.exception(...)` is called once, where the exception is handled.

### Public API

semlog exposes exactly eight names. Everything else stays standard `logging`.

| Name | Use it to |
|---|---|
| `configure(...)` | Configure the process once, at startup |
| `WSGIMiddleware(app)` | Wrap a WSGI application: trace context and `http.request.id` on every record of a request |
| `ASGIMiddleware(app)` | The same, for an ASGI application |
| `operation(headers=None)` | Correlate work outside HTTP, such as jobs, queue consumers and CLI commands |
| `bind(attributes)` | Add fields to every later record of the current request or operation |
| `inject(headers, *, trusted=True)` | Propagate trace context to an outbound call |
| `flush(timeout=None)` | Wait until every queued record is written, before `os._exit()` or in tests |
| `llm()` | Return the agent guide bundled with the installed version (also `python -m semlog llm`) |

Full signatures and semantics are in the [agent guide](src/semlog/agent_guide.md).

## Framework recipes

### FastAPI

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

### Django

Wrap the WSGI or ASGI entry point, and turn off Django's own logging configuration so it does not replace the root handler that `configure()` installs.

```python
# wsgi.py
import semlog
from django.core.wsgi import get_wsgi_application
from semlog import WSGIMiddleware

semlog.configure(service_name="my-django-service")
application = WSGIMiddleware(get_wsgi_application(), log_requests=True)
```

```python
# asgi.py
import semlog
from django.core.asgi import get_asgi_application
from semlog import ASGIMiddleware

semlog.configure(service_name="my-django-service")
application = ASGIMiddleware(get_asgi_application(), log_requests=True)
```

```python
# settings.py
LOGGING_CONFIG = None  # Django must not replace the root handler configure() installs
```

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

Precedence, from highest to lowest, is always:

1. an explicit `configure()` parameter;
2. the `OTEL_*` environment variable;
3. `pyproject.toml`, on Python 3.11 and later only;
4. the built-in default.

Recognized environment variables:

| Variable | Sets |
|---|---|
| `OTEL_SERVICE_NAME` | `service.name` (`service_name`) |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace` (`service_namespace`), `service.version` (`service_version`), `service.instance.id` (`service_instance_id`) and `deployment.environment.name` (`environment`); also the fallback for `service.name` (`service_name`) |
| `OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT`, `OTEL_ATTRIBUTE_COUNT_LIMIT` | the `max_attributes` limit |
| `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` | the `max_attribute_length` limit |

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

### Hybrid mode

In `hybrid` mode, only records logged with `semlog=True` reach semlog's own JSON output; every other line stays exactly as it already prints. This suppression targets `logging.StreamHandler` and its subclasses only. A handler outside that synchronous dispatch, such as a `logging.handlers.QueueHandler` paired with a `QueueListener`, or a `logging.handlers.MemoryHandler`, may still render a marked record as text; this is a documented limitation, not a defect.

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
  "telemetry.sdk.version": "0.1.0",
  "telemetry.sdk.language": "python"
}
```

Keys are flat, dotted strings. Trace fields appear only when a trace context is bound, and an optional value that is unknown is `null`, never an empty string. Sensitive keys such as `password` or `token` are always replaced by `"REDACTED"`. The full field order, the severity mapping and the presence rules are in [STANDARDS.md](STANDARDS.md), and the formal schema is [`schemas/log-record.schema.json`](schemas/log-record.schema.json).

## Compatibility

The test suite runs in CI on CPython 3.10, 3.11, 3.12, 3.13 and 3.14; 3.15 also runs there and is allowed to fail. The package is pure Python (`py3-none-any` wheel). Framework integration is tested in CI against these versions:

| Framework | Version | Python | Role |
|---|---|---|---|
| FastAPI (Starlette 0.17.1) | 0.71.0 | 3.10 | minimum |
| FastAPI (Starlette 1.6.0) | 0.141.1 | 3.10, 3.14 | latest |
| Django | 3.2.9 | 3.10 | minimum |
| Django | 5.2 LTS | 3.10, 3.14 | latest |
| Django | 6.1 | 3.12, 3.14 | latest |

FastAPI is tested with `async def` and sync `def` endpoints. Django is tested with sync views over WSGI and with async and sync views over ASGI.

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
