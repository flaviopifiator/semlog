"""Public-surface audit (task 3.17; design #170 §8; STANDARDS CP-015):
`semlog.__all__` is exactly the 11 documented names, in the design's order,
and nothing else public leaks from the package `dict`.
"""

from __future__ import annotations

import unittest

import semlog

EXPECTED_NAMES = (
    "configure",
    "WSGIMiddleware",
    "ASGIMiddleware",
    "DjangoMiddleware",
    "AiohttpMiddleware",
    "operation",
    "bind",
    "inject",
    "flush",
    "llm",
    "celery",
)


class PublicSurfaceTests(unittest.TestCase):
    """Proves: CP-015

    Confirmation-only, like tasks 2.40/3.6: every name below already
    landed with its own capability's GREEN step, so this audit is
    expected to pass on first run with no production change."""

    def test_all_is_exactly_the_eleven_documented_names_in_order(self):
        self.assertEqual(EXPECTED_NAMES, semlog.__all__)

    def test_every_public_name_is_callable(self):
        for name in EXPECTED_NAMES:
            self.assertTrue(callable(getattr(semlog, name)), name)

    def test_no_undocumented_public_name_in_all(self):
        module_public = {
            name
            for name in vars(semlog)
            if not name.startswith("_") and not name.startswith("__")
        }
        # `__all__` itself and any imported submodule alias are not part of
        # the documented public surface; only names semlog.__all__ lists
        # (or a strict subset thereof) may remain after that exclusion.
        self.assertTrue(module_public.issubset(set(EXPECTED_NAMES) | {"annotations"}))


if __name__ == "__main__":
    unittest.main()
