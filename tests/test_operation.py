"""`operation()` tests (design #162 §2.3, STANDARDS TCP-005).

`operation()` is the shared context-manager primitive for non-HTTP work
(jobs, consumers, CLIs) -- the same header-binding logic the middlewares
use, exposed publicly: called with headers, it parses them like an inbound
request; called without headers while already inside an operation, it
starts a child (same `trace_id`, new `span_id`); called without headers
outside any operation, it starts a new trace.
"""

from __future__ import annotations

import unittest

from semlog._context import current, operation


class OperationHeaderParsingTests(unittest.TestCase):
    """Proves: TCP-005"""

    def test_called_with_headers_parses_them_like_an_inbound_request(self):
        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }
        with operation(headers=headers) as snapshot:
            self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", snapshot.trace_id)
            self.assertEqual(current(), snapshot)
        self.assertIsNone(current())

    def test_called_without_headers_outside_any_operation_starts_a_new_trace(self):
        with operation() as snapshot:
            self.assertEqual(32, len(snapshot.trace_id))
            self.assertEqual(16, len(snapshot.span_id))
        self.assertIsNone(current())

    def test_called_without_headers_inside_an_operation_starts_a_child(self):
        with operation() as parent:
            with operation() as child:
                self.assertEqual(parent.trace_id, child.trace_id)
                self.assertNotEqual(parent.span_id, child.span_id)
            self.assertEqual(current(), parent)  # reset back to the parent scope
        self.assertIsNone(current())

    def test_exception_inside_the_scope_still_resets(self):
        with self.assertRaises(ValueError), operation():
            raise ValueError("boom")
        self.assertIsNone(current())


if __name__ == "__main__":
    unittest.main()
