# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-09-16

### Added

- `DjangoMiddleware`, a `settings.MIDDLEWARE` entry serving both
  synchronous and asynchronous Django deployments through exactly one
  class: dual sync/async marking with a stdlib-only coroutine-function
  shim, no `django`/`asgiref` import at `semlog` import time, per-request
  trace/baggage/identity binding, the existing `full`/`hybrid`/`off` mode
  guarantees, `process_exception` capturing an unhandled view exception
  without swallowing it, and context preservation throughout a
  `StreamingHttpResponse` body (verified on Django 3.2.9, the oldest
  supported row), with a documented `FileResponse` carve-out.
- The Django completion event, enabled through the new
  `SEMLOG_LOG_REQUESTS` setting instead of a constructor keyword, since
  Django instantiates a `settings.MIDDLEWARE` entry with a single
  positional argument; default off, read once at construction, an
  explicit `log_requests=` keyword still wins, and a non-boolean value
  raises `ValueError` naming the value and its source.
- Documentation for the Django `settings.MIDDLEWARE` recipe (outermost
  placement recommendation and its ordering tradeoff, the permanent
  `process_exception` limitations, and the unsupported coexistence with
  `WSGIMiddleware`/`ASGIMiddleware`), and the FastAPI
  `app.add_middleware(semlog.ASGIMiddleware, ...)` recipe alongside the
  existing wrapper recipe, with its exception-handler caveat, in
  README.md, README.es.md, the agent guide and llms.txt.
- Documentation for the remaining integration routes, so each framework
  section now covers every supported way in, with a note on which to
  choose: the FastAPI wrapper recipe beside `add_middleware`, and the
  Django `WSGIMiddleware`/`ASGIMiddleware` wrapper around
  `get_wsgi_application()`/`get_asgi_application()`, with the note that
  Django converts a view exception into a 500 response before the wrapper
  sees it, so that route cannot attach the traceback.
- Documentation for hybrid mode's root logger level: hybrid leaves that
  level as it found it, the standard library leaves it at `WARNING`, and
  an `INFO` record marked `semlog=True` (the `http.server.request`
  completion event included) is filtered out before hybrid's routing
  runs. `configure(level=...)` applies in `full` mode only, so the
  application sets the root level itself.
- A README opening that says what semlog is before anything else, and a
  `Philosophy` section stating the positions the library is built on,
  in both editions.
- A PEP 561 `py.typed` marker inside the package, so a type checker reads
  the annotations semlog already carries instead of ignoring the package.
- PyPI keywords, a `Documentation` project URL and the
  `Intended Audience :: Developers`,
  `Topic :: Software Development :: Libraries :: Python Modules` and
  `Typing :: Typed` classifiers.

### Changed

- CP-015's public surface grows from eight names to nine, adding
  `DjangoMiddleware`, with a documented exception for the
  `SEMLOG_LOG_REQUESTS` Django setting.
- CP-017 removes both its numeric budgets (the 1500-line source-code
  budget and the 6-class budget); only the no-abstract-base-class and
  no-class-factory rules remain, with human review as the guardrail
  against unbounded growth. `tests/test_source_budget.py` is deleted.
- The recipe caveats move from code comments into prose: a fenced block
  is byte-identical across both README editions, so a caveat written
  inside one reached a Spanish reader in English.

### Fixed

- The FastAPI wrapper example registered its routes on the already
  wrapped object, so copying it raised `AttributeError` on import. The
  wrapping line now comes after the routes, in both README editions and
  in the agent guide.
- Both README editions showed `"telemetry.sdk.version":"0.2.0"` in
  records presented as real output of a 0.3.0 package. The value is now
  checked against the version `pyproject.toml` declares.

## [0.2.0] - 2026-09-15

### Added

- Three execution modes, selected through `configure(mode=...)`: `full`
  (the default and the end state for a new service), `hybrid` (the
  adoption path for a service already running in production: every
  existing log line keeps printing byte-identically, while a call written
  as `logger.info("event.name", extra={...}, semlog=True)` is hidden from
  that same printed output and instead becomes one JSON record), and
  `off` (behaves as if semlog were never installed, except that the
  keyword itself never raises). `mode`
  resolves from the `mode` parameter, then the `SEMLOG_MODE` environment
  variable, then `[tool.semlog].mode` in `pyproject.toml` (Python 3.11 and
  later only), then the `"full"` default; an invalid value from any
  source raises `ValueError` naming the value and its source.
- The `semlog=True` call-site keyword, accepted by every `logging.Logger`
  method (`debug`, `info`, `warning`, `error`, `critical`, `exception`,
  `log`) in every mode, including before `configure()` runs, without ever
  raising and without mutating the caller's `extra` dict.

### Fixed

- The writer thread and the direct/synchronous emission path now resolve
  the output stream robustly: a replaced `sys.stdout` lacking a `.buffer`
  attribute no longer kills the writer thread or terminates the process.
- `flush(timeout=None)` now detects a dead or never-started writer thread
  and returns promptly instead of waiting out the full timeout for a
  marker that could never be processed.
- A `queue.Full` escaping the at-fork hook's writer shutdown no longer
  leaves a second writer thread running in the forking process; a
  leftover second writer previously deadlocked a later fork waiting on a
  sentinel the wrong writer would consume.

## [0.1.0] - 2026-09-14

### Added

- Structured JSON Lines logging on top of the standard library's `logging`,
  with zero runtime dependencies, for CPython 3.10 to 3.14.
- A public API of eight names: `configure()`, `WSGIMiddleware`,
  `ASGIMiddleware`, `operation()`, `bind()`, `inject()`, `flush()`, and
  `llm()`.
- The logging pipeline: a formatter that enriches, redacts, limits, renders
  exceptions, and encodes each record on the calling thread; a bounded queue
  handler with `block` and `drop` overflow modes that keeps room for
  `WARNING` and above; and a lazily started writer thread with fork handling
  and shutdown at exit. `capture_loggers` routes third-party loggers that
  install their own handlers through the same pipeline.
- A stable record contract with OpenTelemetry field names and severity
  mapping, published as JSON Schema 2020-12 for log records and for the
  event catalog.
- W3C Trace Context and Baggage propagation: the middlewares continue or
  start a trace, `inject()` writes the headers for outbound calls, and
  request ids are RFC 9562 UUIDv7.
- Always-on redaction of sensitive keys and attribute count and length
  limits, applied to call-site attributes in a single pass with bounded
  caches for repeated keys. Nested values that coerce to NaN or Infinity are
  sanitized, so their records are still emitted.
- An optional `http.server.request` event per request (`log_requests=True`)
  that never includes `url.query`.
- The event catalog with `off`, `warn`, and `strict` validation modes.
- `semlog.llm()` and `python -m semlog llm`, returning the packaged,
  version-matched agent guide.
- `STANDARDS.md` with requirement identifiers and a traceability annex,
  `README.md` in English with its Spanish translation `README.es.md`,
  `BENCHMARKS.md`, the agent guide, `llms.txt`, `AGENTS.md`, `SECURITY.md`,
  and `RELEASING.md`.
- A test suite that traces every normative requirement to at least one
  test, including the FastAPI/Starlette and Django compatibility matrix at
  the minimum and latest supported versions.
- The benchmark harness comparing semlog with the standard library's
  `logging`, loguru, and structlog, including a field-scaling scenario from
  0 to 100 call-site attributes. `BENCHMARKS.md` documents its methodology;
  results are not published until the maintainer validates them.
- Packaging as a pure-Python wheel through the `uv_build` backend, with
  PyPI classifiers and no `Requires-Dist` entries.
- Continuous integration and release automation on GitHub Actions: tests,
  lint, and wheel checks; a pull request check that derives the version
  from Conventional Commits and requires its CHANGELOG.md section; tagging
  of each new version merged to `main`; and PyPI Trusted Publishing with
  PEP 740 attestations after a maintainer approves the `pypi` environment.
