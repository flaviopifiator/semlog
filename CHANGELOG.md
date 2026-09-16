# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
