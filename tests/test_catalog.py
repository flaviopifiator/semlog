"""Event catalog validation-mode tests (STANDARDS §5, LRC-008).

Proves LRC-008: the three catalog validation modes (`off`, `warn`,
`strict`), applied only to records that carry an `event_name` (design
#162 §2.1 step 3d -- third-party text records are never catalog-checked).
`warn` batches every violation on one record into exactly one diagnostic
line through the library's own `"semlog"` internal logger namespace
(SI-003); `strict` raises `ValueError` at the call site before any record
is produced; `off` performs no check at all.

Also completes LRC-013's "declared OTel semconv key accepted" scenario,
left partly proven by `tests/test_namespace.py`'s `ClosedAttributeGroupTests`
(engram #188 batch 3 note): now that the catalog mechanism exists, a
call-site key explicitly declared for an event in the catalog is accepted
in `strict` mode, proving the closed-group membership the catalog grants.
"""

from __future__ import annotations

import json
import logging
import unittest

from semlog._format import Formatter


def _identity():
    return {
        "service.name": "svc",
        "service.namespace": None,
        "service.version": None,
        "service.instance.id": "11111111-1111-1111-1111-111111111111",
        "deployment.environment.name": None,
        "telemetry.sdk.name": "semlog",
        "telemetry.sdk.version": "0.0.0",
        "telemetry.sdk.language": "python",
    }


def _record(event_name, extra=None, args=()):
    logger = logging.getLogger("semlog.tests.catalog")
    return logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        event_name,
        args,
        None,
        extra=extra or {},
    )


_CATALOG = {
    "events": {
        "payment.authorization.completed": {
            "attributes": {"app.payment.outcome": {"type": "string"}}
        }
    }
}


class CatalogOffModeTests(unittest.TestCase):
    """Proves: LRC-008"""

    def test_off_mode_performs_no_check_even_with_unknown_event_and_fields(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="off"
        )
        record = _record("payment.unknown.event", {"app.rogue": 1})
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertEqual(1, parsed["app.rogue"])


class CatalogStrictModeTests(unittest.TestCase):
    """Proves: LRC-008"""

    def test_strict_mode_raises_on_undeclared_event(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="strict"
        )
        record = _record("payment.unknown.event")
        with self.assertRaises(ValueError):
            formatter.format(record)

    def test_strict_mode_raises_on_undeclared_field_for_a_declared_event(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="strict"
        )
        record = _record("payment.authorization.completed", {"app.unknown": 1})
        with self.assertRaises(ValueError):
            formatter.format(record)

    def test_strict_mode_accepts_a_fully_declared_record(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="strict"
        )
        record = _record(
            "payment.authorization.completed", {"app.payment.outcome": "approved"}
        )
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertEqual("approved", parsed["app.payment.outcome"])


class CatalogWarnModeTests(unittest.TestCase):
    """Proves: LRC-008"""

    def test_warn_mode_still_emits_the_record(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="warn"
        )
        record = _record("payment.unknown.event")
        with self.assertLogs("semlog", level="WARNING"):
            parsed = json.loads(formatter.format(record))
        self.assertEqual("payment.unknown.event", parsed["event_name"])

    def test_warn_mode_batches_every_violation_on_one_record_into_one_line(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="warn"
        )
        record = _record(
            "payment.unknown.event",
            {"app.first.unknown": 1, "app.second.unknown": 2},
        )
        with self.assertLogs("semlog", level="WARNING") as cm:
            formatter.format(record)
        self.assertEqual(1, len(cm.output))
        message = cm.output[0]
        self.assertIn("payment.unknown.event", message)
        self.assertIn("app.first.unknown", message)
        self.assertIn("app.second.unknown", message)

    def test_third_party_text_records_are_never_catalog_checked(self):
        formatter = Formatter(
            identity=_identity(), catalog=_CATALOG, catalog_mode="strict"
        )
        record = _record("Starting up on port %s", args=(8080,))
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertIsNone(parsed["event_name"])


class DeclaredSemconvKeyTests(unittest.TestCase):
    """Proves: LRC-013"""

    def test_declared_otel_semconv_key_accepted_in_strict_mode(self):
        catalog = {
            "events": {
                "http.server.request": {
                    "attributes": {"http.response.status_code": {"type": "integer"}}
                }
            }
        }
        formatter = Formatter(
            identity=_identity(), catalog=catalog, catalog_mode="strict"
        )
        record = _record("http.server.request", {"http.response.status_code": 200})
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertEqual(200, parsed["http.response.status_code"])


if __name__ == "__main__":
    unittest.main()
