"""End-to-end output-schema conformance (engram #246): `configure()` wired
onto the real pipeline, driven through real WSGI/ASGI requests and plain
call sites, with every emitted line validated against
`schemas/log-record.schema.json`.

Regression guard for the bug the orchestrator's smoke test found: real
output was missing `otel.scope.name` (schema `required`) and never carried
`http.request.id` even when a request id was bound (design #162 §4.1 row
9). No `jsonschema` package: the validator below implements the small
keyword subset the schema itself declares it uses (`type`, `required`,
`pattern`, `enum`, `minimum`/`maximum`, `propertyNames`,
`dependentRequired`, `additionalProperties`).
"""

from __future__ import annotations

import json
import logging
import re
import unittest
from pathlib import Path
from unittest import mock

from semlog import _transport
from semlog._config import configure
from semlog._middleware import ASGIMiddleware, WSGIMiddleware

from ._asgi_support import call_asgi, http_scope
from ._pipeline_support import FakeStdout, reset_pipeline
from ._wsgi_support import environ, recording_start_response

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1] / "schemas" / "log-record.schema.json"
    ).read_text(encoding="utf-8")
)

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
    "object": dict,
    "array": (list, tuple),
}


def _matches_type(value, type_spec) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    return isinstance(value, tuple(_TYPE_MAP[t] for t in types))


def validate_record(instance: dict, schema: dict = SCHEMA) -> list[str]:
    """Validate `instance` against the schema's keyword subset; return a
    list of human-readable errors (empty means conformant)."""
    errors: list[str] = []
    if not isinstance(instance, dict):
        return [f"instance is not a JSON object: {instance!r}"]

    name_pattern = re.compile(schema["propertyNames"]["pattern"])
    for key in instance:
        if not name_pattern.match(key):
            errors.append(f"property name {key!r} violates propertyNames pattern")

    for key in schema.get("required", []):
        if key not in instance:
            errors.append(f"missing required property {key!r}")

    properties = schema.get("properties", {})
    for key, value in instance.items():
        prop = properties.get(key)
        if prop is None:
            continue
        if "type" in prop and not _matches_type(value, prop["type"]):
            errors.append(f"{key!r}={value!r} does not match type {prop['type']!r}")
            continue
        if (
            "pattern" in prop
            and isinstance(value, str)
            and not re.match(prop["pattern"], value)
        ):
            errors.append(f"{key!r}={value!r} violates pattern {prop['pattern']!r}")
        if "enum" in prop and value not in prop["enum"]:
            errors.append(f"{key!r}={value!r} not in enum {prop['enum']!r}")
        if (
            "minimum" in prop
            and isinstance(value, (int, float))
            and value < prop["minimum"]
        ):
            errors.append(f"{key!r}={value!r} below minimum {prop['minimum']!r}")
        if (
            "maximum" in prop
            and isinstance(value, (int, float))
            and value > prop["maximum"]
        ):
            errors.append(f"{key!r}={value!r} above maximum {prop['maximum']!r}")

    for key, deps in schema.get("dependentRequired", {}).items():
        if key in instance:
            for dep in deps:
                if dep not in instance:
                    errors.append(f"{key!r} present but dependent {dep!r} missing")

    if schema.get("additionalProperties") is False:
        allowed = set(properties)
        errors += [
            f"additional property not allowed: {k!r}"
            for k in instance
            if k not in allowed
        ]

    return errors


class NoThirdPartyJsonSchemaDependencyTests(unittest.TestCase):
    """Proves: DOC-004, CP-008

    Task 4.7: the validator above already implements the small stdlib-only
    JSON Schema 2020-12 keyword subset the two published schemas declare
    they use, and `RealPipelineSchemaConformanceTests` already runs it over
    every distinct line shape the pipeline emits (plain call sites, WSGI
    and ASGI requests, exceptions, redaction, extra attributes, and both
    optional request-completion event outcomes). This class adds the two
    remaining structural facts that make the CP-008/DOC-004 citation
    complete: no `jsonschema` package is imported anywhere in the
    repository (the stdlib alternative STANDARDS.md CP-008 names), and both
    published schema documents actually declare the JSON Schema 2020-12
    dialect this validator implements.
    """

    def test_no_jsonschema_import_anywhere_in_the_repository(self):
        repo_root = Path(__file__).resolve().parents[1]
        offenders = []
        for path in (*repo_root.glob("src/**/*.py"), *repo_root.glob("tests/**/*.py")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*(import jsonschema|from jsonschema\b)", text):
                offenders.append(str(path.relative_to(repo_root)))
        self.assertEqual([], offenders)

    def test_both_published_schemas_declare_the_2020_12_dialect(self):
        schemas_dir = Path(__file__).resolve().parents[1] / "schemas"
        for name in ("log-record.schema.json", "event-catalog.schema.json"):
            with self.subTest(schema=name):
                document = json.loads((schemas_dir / name).read_text(encoding="utf-8"))
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    document["$schema"],
                )


class SchemaValidatorSelfTests(unittest.TestCase):
    """Fixture self-tests over the validator itself, never the real pipeline."""

    def test_conformant_instance_has_no_errors(self):
        instance = {
            "timestamp": "2026-09-11T00:00:00.000000Z",
            "severity_text": "INFO",
            "severity_number": 9,
            "event_name": "demo.ok",
            "body": None,
            "otel.scope.name": "demo",
            "service.name": "svc",
            "service.namespace": None,
            "service.version": None,
            "service.instance.id": None,
            "deployment.environment.name": None,
            "telemetry.sdk.name": "semlog",
            "telemetry.sdk.version": "0",
            "telemetry.sdk.language": "python",
        }
        self.assertEqual([], validate_record(instance))

    def test_missing_required_property_is_reported(self):
        self.assertTrue(any("otel.scope.name" in e for e in validate_record({})))

    def test_dependent_required_flags_partial_trace_group(self):
        instance = {"trace_id": "a" * 32}
        errors = validate_record(instance)
        self.assertTrue(any("span_id" in e for e in errors))
        self.assertTrue(any("trace_flags" in e for e in errors))

    def test_bad_severity_number_range_is_reported(self):
        errors = validate_record({"severity_number": 99})
        self.assertTrue(any("severity_number" in e for e in errors))

    def test_bad_property_name_is_reported(self):
        errors = validate_record({"Not-Lowercase": "x"})
        self.assertTrue(any("Not-Lowercase" in e for e in errors))


class RealPipelineSchemaConformanceTests(unittest.TestCase):
    """Drives the real `configure()`-installed pipeline; captures real
    stdout via a fake `sys.stdout` (same idiom as
    `test_configure.py::PipelineWiringTests`)."""

    def tearDown(self):
        reset_pipeline()

    def _capture(self, work) -> list[dict]:
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(service_name="schema-conformance", search_dir=".")
            work()
            _transport.flush(timeout=2)
        text = fake_stdout.buffer.getvalue().decode("utf-8").strip()
        return [json.loads(line) for line in text.splitlines() if line]

    def test_plain_log_conforms_and_carries_scope_name(self):
        lines = self._capture(
            lambda: logging.getLogger("demo.api").info("demo.plain.logged")
        )
        self.assertEqual(1, len(lines))
        self.assertEqual([], validate_record(lines[0]))
        self.assertEqual("demo.api", lines[0]["otel.scope.name"])
        self.assertNotIn("trace_id", lines[0])
        self.assertNotIn("http.request.id", lines[0])

    def test_wsgi_request_log_carries_trace_and_request_id(self):
        def app(env, start_response):
            start_response("200 OK", [])
            logging.getLogger("demo.wsgi").info("demo.request.handled")
            return [b""]

        middleware = WSGIMiddleware(app)
        env = environ(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            }
        )
        lines = self._capture(lambda: list(middleware(env, recording_start_response())))
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", record["trace_id"])
        self.assertIn("http.request.id", record)

    def test_asgi_request_log_carries_trace_and_request_id(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            logging.getLogger("demo.asgi").info("demo.request.handled")
            await send({"type": "http.response.body", "body": b""})

        import asyncio

        middleware = ASGIMiddleware(app)
        scope = http_scope(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            }
        )
        lines = self._capture(lambda: asyncio.run(call_asgi(middleware, scope)))
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", record["trace_id"])
        self.assertIn("http.request.id", record)

    def test_exception_log_conforms(self):
        def work():
            logger = logging.getLogger("demo.worker")
            try:
                raise ValueError("boom")
            except ValueError:
                logger.exception("demo.job.failed")

        lines = self._capture(work)
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertIn("exception.stacktrace", record)

    def test_redacted_key_log_conforms(self):
        lines = self._capture(
            lambda: logging.getLogger("demo.api").info(
                "demo.secret.logged", extra={"password": "hunter2"}
            )
        )
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("REDACTED", record["app.password"])

    def test_extra_attributes_log_conforms(self):
        lines = self._capture(
            lambda: logging.getLogger("demo.api").info(
                "demo.attrs.logged", extra={"app.user_id": "42"}
            )
        )
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("42", record["app.user_id"])

    def test_wsgi_request_event_conforms_and_never_carries_url_query(self):
        """Proves: HTM-007"""

        def app(env, start_response):
            start_response("200 OK", [])
            return [b""]

        middleware = WSGIMiddleware(app, log_requests=True)
        env = environ(PATH_INFO="/orders", QUERY_STRING="token=secret")
        lines = self._capture(lambda: list(middleware(env, recording_start_response())))
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("http.server.request", record["event_name"])
        self.assertEqual("INFO", record["severity_text"])
        self.assertEqual("/orders", record["url.path"])
        self.assertEqual(200, record["http.response.status_code"])
        self.assertNotIn("url.query", record)

    def test_asgi_request_event_conforms_and_never_carries_url_query(self):
        """Proves: HTM-007"""

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b""})

        import asyncio

        middleware = ASGIMiddleware(app, log_requests=True)
        scope = http_scope(path="/orders")
        scope["query_string"] = b"token=secret"
        lines = self._capture(lambda: asyncio.run(call_asgi(middleware, scope)))
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("http.server.request", record["event_name"])
        self.assertEqual("/orders", record["url.path"])
        self.assertEqual(200, record["http.response.status_code"])
        self.assertNotIn("url.query", record)

    def test_request_event_on_exception_conforms_and_carries_the_error_once(self):
        """Proves: HTM-007"""

        def app(env, start_response):
            raise ValueError("boom")

        middleware = WSGIMiddleware(app, log_requests=True)
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(service_name="schema-conformance", search_dir=".")
            with self.assertRaises(ValueError):
                middleware(environ(), recording_start_response())
            _transport.flush(timeout=2)
        text = fake_stdout.buffer.getvalue().decode("utf-8").strip()
        lines = [json.loads(line) for line in text.splitlines() if line]
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual([], validate_record(record))
        self.assertEqual("http.server.request", record["event_name"])
        self.assertEqual("ERROR", record["severity_text"])
        self.assertIn("exception.stacktrace", record)
        self.assertNotIn("url.query", record)


if __name__ == "__main__":
    unittest.main()
