"""Custom namespace and closed attribute-group tests (STANDARDS §5, §8).

Proves LRC-007 and LRC-013: call-site custom attributes are flattened
under exactly one configurable namespace root, an already-dotted extra
key passes through unprefixed (a declared OTel semconv key or an
already-qualified custom key), and allowlisted baggage attributes land
under the configured baggage prefix.
"""

from __future__ import annotations

import json
import logging
import unittest

from semlog._context import Snapshot, pop, push
from semlog._format import Formatter


def _identity():
    """An identity dict (`_identity.resolve_identity`'s return shape,
    engram #238)."""
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


def _record_with_extra(extra):
    logger = logging.getLogger("semlog.tests.namespace")
    return logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "app.custom.event",
        (),
        None,
        extra=extra,
    )


class CustomNamespaceTests(unittest.TestCase):
    """Proves: LRC-007"""

    def test_bare_extra_key_flattened_under_configured_namespace(self):
        formatter = Formatter(identity=_identity(), namespace="app")
        record = _record_with_extra({"rule": "velocity-check"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("velocity-check", parsed["app.rule"])
        self.assertNotIn("rule", parsed)

    def test_configured_namespace_is_not_hardcoded(self):
        formatter = Formatter(identity=_identity(), namespace="svc")
        record = _record_with_extra({"rule": "velocity-check"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("velocity-check", parsed["svc.rule"])
        self.assertNotIn("app.rule", parsed)


class ClosedAttributeGroupTests(unittest.TestCase):
    """Covers the baggage-prefix-group and dotted-passthrough mechanics of
    LRC-013. Deliberately NOT tagged ``Proves: LRC-013`` here: this class
    only exercises passthrough/baggage mechanics, while the "declared OTel
    semconv key accepted" scenario (the piece that actually removes
    LRC-013 from ``NOT_YET_PROVEN``) is proven by
    ``tests/test_catalog.py::DeclaredSemconvKeyTests``, once the catalog
    mechanism landed in task 2.21/2.22.
    """

    def test_already_dotted_extra_key_passes_through_unprefixed(self):
        formatter = Formatter(identity=_identity(), namespace="app")
        record = _record_with_extra({"http.response.status_code": 200})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(200, parsed["http.response.status_code"])
        self.assertNotIn("app.http.response.status_code", parsed)

    def test_allowlisted_baggage_key_accepted_under_baggage_prefix(self):
        snapshot = Snapshot(baggage={"user_tier": "gold"})
        token = push(snapshot)
        try:
            formatter = Formatter(identity=_identity(), namespace="app")
            record = _record_with_extra({})
            parsed = json.loads(formatter.format(record))
        finally:
            pop(token)
        self.assertEqual("gold", parsed["baggage.user_tier"])

    def test_custom_baggage_prefix_is_configurable(self):
        snapshot = Snapshot(baggage={"user_tier": "gold"})
        token = push(snapshot)
        try:
            formatter = Formatter(
                identity=_identity(), namespace="app", baggage_prefix="bg."
            )
            record = _record_with_extra({})
            parsed = json.loads(formatter.format(record))
        finally:
            pop(token)
        self.assertEqual("gold", parsed["bg.user_tier"])
        self.assertNotIn("baggage.user_tier", parsed)

    def test_to_log_attributes_output_does_not_double_prefix_through_the_formatter(
        self,
    ):
        """Proves: TCP-007

        End-to-end regression guard for the baggage double-prefix bug
        (engram #238/#239 follow-up): `to_log_attributes`'s output, fed
        directly into `Snapshot.baggage` the way a future
        `operation()`/`bind()` will, must produce exactly one `baggage.`
        prefix through the formatter, never `baggage.baggage.x`."""
        from semlog._baggage import parse_baggage, to_log_attributes

        members = parse_baggage("user_tier=gold")
        baggage_attributes = to_log_attributes(members, allow=("user_tier",))
        snapshot = Snapshot(baggage=baggage_attributes)
        token = push(snapshot)
        try:
            formatter = Formatter(identity=_identity(), namespace="app")
            record = _record_with_extra({})
            parsed = json.loads(formatter.format(record))
        finally:
            pop(token)
        self.assertEqual("gold", parsed["baggage.user_tier"])
        self.assertNotIn("baggage.baggage.user_tier", parsed)


if __name__ == "__main__":
    unittest.main()
