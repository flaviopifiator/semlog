"""`bind()` tests: merge semantics (design #162 §2.3, TCP-005) and the
TCP-012 out-of-scope no-op/warn-once behavior (design-part3 #223 §11,
P3-ADR-11; STANDARDS.md §8.3).
"""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from semlog import _modes
from semlog._config import configure
from semlog._context import bind, current, operation

from ._pipeline_support import reset_pipeline


class BindMergeSemanticsTests(unittest.TestCase):
    """Proves: TCP-005"""

    def test_bound_attributes_merge_rather_than_replace(self):
        with operation():
            bind({"app.a": 1})
            bind({"app.b": 2})
            self.assertEqual({"app.a": 1, "app.b": 2}, current().attributes)

    def test_a_later_bind_overwrites_only_its_own_key(self):
        with operation():
            bind({"app.a": 1, "app.b": 2})
            bind({"app.a": 99})
            self.assertEqual({"app.a": 99, "app.b": 2}, current().attributes)


class BindOutOfScopeTests(unittest.TestCase):
    """Proves: TCP-012"""

    def setUp(self):
        import semlog._context as context_module

        self._context_module = context_module
        self._saved_warned = context_module._bind_warned
        context_module._bind_warned = False

    def tearDown(self):
        self._context_module._bind_warned = self._saved_warned

    def test_outside_a_scope_does_not_raise_and_touches_no_context(self):
        bind({"app.a": 1})  # must not raise
        self.assertIsNone(current())

    def test_first_out_of_scope_call_emits_exactly_one_warning_with_call_site(self):
        with self.assertLogs("semlog", level="WARNING") as captured:
            bind({"app.a": 1})
        self.assertEqual(1, len(captured.records))
        self.assertEqual(logging.WARNING, captured.records[0].levelno)
        self.assertTrue(captured.records[0].stack_info)

    def test_second_out_of_scope_call_is_a_silent_no_op(self):
        with self.assertLogs("semlog", level="WARNING"):
            bind({"app.a": 1})
        logger = logging.getLogger("semlog")
        sentinel = []
        handler = logging.Handler()
        handler.emit = lambda record: sentinel.append(record)
        logger.addHandler(handler)
        try:
            bind({"app.b": 2})
        finally:
            logger.removeHandler(handler)
        self.assertEqual([], sentinel)

    def test_in_scope_bind_is_unaffected_and_emits_no_warning(self):
        logger = logging.getLogger("semlog")
        sentinel = []
        handler = logging.Handler()
        handler.emit = lambda record: sentinel.append(record)
        logger.addHandler(handler)
        try:
            with operation():
                bind({"app.a": 1})
                self.assertEqual({"app.a": 1}, current().attributes)
        finally:
            logger.removeHandler(handler)
        self.assertEqual([], sentinel)

    def test_even_the_first_out_of_scope_call_is_silent_in_off_mode(self):
        """Proves: TCP-012"""
        logger = logging.getLogger("semlog")
        sentinel = []
        handler = logging.Handler()
        handler.emit = lambda record: sentinel.append(record)
        logger.addHandler(handler)
        try:
            with mock.patch.dict("os.environ", {}, clear=True):
                configure(mode="off", service_name="svc", search_dir=".")
            bind({"app.a": 1})  # the FIRST out-of-scope call in this process
        finally:
            logger.removeHandler(handler)
            reset_pipeline()
            _modes.state.mode = "full"
            _modes.state.marking = False
        self.assertEqual([], sentinel)


if __name__ == "__main__":
    unittest.main()
