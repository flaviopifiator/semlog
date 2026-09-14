"""`inject()` tests: client-agnostic outbound propagation (design #162 §2.3;
TCP-006, TCP-008, TCP-009, TCP-011).
"""

from __future__ import annotations

import unittest

from semlog._context import inject, operation


class InjectTests(unittest.TestCase):
    """Proves: TCP-006, TCP-008, TCP-009, TCP-011"""

    def test_injects_valid_traceparent_into_any_mapping(self):
        with operation(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            }
        ) as snapshot:
            headers: dict[str, str] = {}
            result = inject(headers)
            self.assertIsNone(result)
            self.assertEqual(
                f"00-4bf92f3577b34da6a3ce929d0e0e4736-{snapshot.span_id}-01",
                headers["traceparent"],
            )

    def test_no_current_operation_leaves_mapping_unchanged(self):
        headers: dict[str, str] = {}
        inject(headers)
        self.assertEqual({}, headers)

    def test_existing_traceparent_any_case_is_left_unchanged(self):
        with operation():
            headers = {"Traceparent": "00-existing-existing-01"}
            inject(headers)
            self.assertEqual("00-existing-existing-01", headers["Traceparent"])
            self.assertNotIn("traceparent", headers)

    def test_baggage_injected_only_when_trusted(self):
        with operation(headers={"baggage": "tier=gold"}, baggage_allow=("tier",)):
            trusted_headers: dict[str, str] = {}
            inject(trusted_headers, trusted=True)
            self.assertEqual("tier=gold", trusted_headers["baggage"])

    def test_baggage_stripped_at_the_trust_boundary(self):
        with operation(headers={"baggage": "tier=gold"}, baggage_allow=("tier",)):
            untrusted_headers: dict[str, str] = {}
            inject(untrusted_headers, trusted=False)
            self.assertNotIn("baggage", untrusted_headers)
            self.assertIn("traceparent", untrusted_headers)

    def test_no_x_prefixed_or_other_nonstandard_header_is_added(self):
        with operation(headers={"baggage": "tier=gold"}, baggage_allow=("tier",)):
            headers: dict[str, str] = {}
            inject(headers)
            self.assertEqual({"traceparent", "baggage"}, set(headers))


if __name__ == "__main__":
    unittest.main()
