"""Reserved-key fail-fast tests (STANDARDS §2, LRC-006, LRC-011).

Proves LRC-006 and LRC-011: a reserved stdlib LogRecord attribute name
passed via `extra` fails synchronously at the call site with `KeyError`,
matching stdlib's own `Logger.makeRecord` failure point -- never silently
absorbed by the pipeline or deferred to the writer thread. This module
also documents the fixed set of 23 reserved names LRC-006 declares.
"""

from __future__ import annotations

import logging
import sys
import unittest

from semlog._format import RESERVED_KEYS


class ReservedKeySetTests(unittest.TestCase):
    """Proves: LRC-006"""

    def test_reserved_keys_match_the_declared_set_of_23_names(self):
        expected = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
        self.assertEqual(expected, RESERVED_KEYS)
        self.assertEqual(23, len(RESERVED_KEYS))

    def test_dotted_custom_keys_cannot_collide(self):
        for key in RESERVED_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(".", key)


class ReservedKeyFailFastTests(unittest.TestCase):
    """Proves: LRC-011"""

    def test_reserved_name_raises_keyerror_synchronously_at_call_site(self):
        logger = logging.getLogger("semlog.tests.reserved_keys")
        with self.assertRaises(KeyError):
            logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "app.thing.happened",
                (),
                None,
                extra={"module": "override"},
            )

    def test_failure_is_not_silently_absorbed_by_a_handler(self):
        # No handler is installed beyond NullHandler; if the call below did
        # not raise, nothing else in this test would ever surface the bug.
        # The level is set explicitly so the call reaches makeRecord at all
        # (a logger left at the stdlib WARNING default would skip an INFO
        # call before extra validation ever runs -- LP-010's own concern).
        logger = logging.getLogger("semlog.tests.reserved_keys.silent")
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.INFO)
        with self.assertRaises(KeyError):
            # The collision below is the exact behavior under test.
            logger.info("app.thing.happened", extra={"process": -1})  # noqa: G101

    @unittest.skipUnless(
        sys.version_info >= (3, 12),
        "taskName is only a reserved LogRecord attribute on Python 3.12+",
    )
    def test_taskname_collision_on_312_plus(self):
        logger = logging.getLogger("semlog.tests.reserved_keys.taskname")
        logger.setLevel(logging.INFO)
        with self.assertRaises(KeyError):
            logger.info("app.thing.happened", extra={"taskName": "x"})


if __name__ == "__main__":
    unittest.main()
