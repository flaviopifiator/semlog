"""Execution-mode resolution tests (LM-001) and the root-logger level
scoping that comes with it (LP-010, scoped to `full` mode only).

Precedence: explicit `configure(mode=...)` parameter > `SEMLOG_MODE`
environment variable > `[tool.semlog].mode` in `pyproject.toml` (3.11+
only, mirroring SI-001/SI-005's existing precedent) > the `"full"`
default. Any value outside `"full"`/`"hybrid"`/`"off"`, from any source,
raises `ValueError` naming the invalid value and its source.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semlog import _modes
from semlog._config import configure

from ._pipeline_support import reset_pipeline


def _write_pyproject(directory, mode):
    (Path(directory) / "pyproject.toml").write_text(
        f'[tool.semlog]\nmode = "{mode}"\n', encoding="utf-8"
    )


class ModeResolutionTests(unittest.TestCase):
    """`configure(mode=...)` resolution precedence and validation. No
    `Proves:` line yet: LM-001 is not declared in STANDARDS.md until Phase 7
    of this change (same deferred-citation precedent as
    `test_class_budget.py`/`test_source_budget.py`); tag this class
    `Proves: LM-001` once that declaration lands."""

    def tearDown(self):
        reset_pipeline()
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_explicit_parameter_wins_over_every_other_source(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"SEMLOG_MODE": "hybrid"}, clear=True),
        ):
            _write_pyproject(tmp, "off")
            configure(mode="full", search_dir=tmp)
        self.assertEqual("full", _modes.state.mode)

    def test_environment_variable_wins_over_pyproject(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"SEMLOG_MODE": "off"}, clear=True),
        ):
            _write_pyproject(tmp, "hybrid")
            configure(search_dir=tmp)
        self.assertEqual("off", _modes.state.mode)

    @unittest.skipUnless(
        sys.version_info >= (3, 11), "pyproject.toml mode detection needs tomllib"
    )
    def test_pyproject_mode_resolved_only_on_3_11_and_later(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            _write_pyproject(tmp, "hybrid")
            configure(search_dir=tmp)
        self.assertEqual("hybrid", _modes.state.mode)

    def test_pyproject_mode_skipped_before_3_11(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("semlog._modes.sys.version_info", (3, 10, 0, "final", 0)),
        ):
            _write_pyproject(tmp, "hybrid")
            configure(search_dir=tmp)
        self.assertEqual("full", _modes.state.mode)

    def test_invalid_explicit_parameter_names_the_parameter_as_source(self):
        """NIT (validate-wu25 #333): every source's error message starts
        with "mode must be one of...", so a bare `assertIn("mode", ...)`
        passes no matter which source actually raised. Each of the three
        source-specific tests below asserts its OWN exact source phrase
        and that the OTHER two sources' phrases are absent."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError) as ctx,
        ):
            configure(mode="verbose", search_dir=tmp)
        message = str(ctx.exception)
        self.assertIn("verbose", message)
        self.assertIn("the mode parameter", message)
        self.assertNotIn("SEMLOG_MODE", message)
        self.assertNotIn("pyproject", message)

    def test_invalid_environment_variable_names_the_env_var_as_source(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"SEMLOG_MODE": "verbose"}, clear=True),
            self.assertRaises(ValueError) as ctx,
        ):
            configure(search_dir=tmp)
        message = str(ctx.exception)
        self.assertIn("verbose", message)
        self.assertIn("the SEMLOG_MODE environment variable", message)
        self.assertNotIn("the mode parameter", message)
        self.assertNotIn("pyproject", message)

    @unittest.skipUnless(
        sys.version_info >= (3, 11), "pyproject.toml mode detection needs tomllib"
    )
    def test_invalid_pyproject_value_names_pyproject_as_source(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError) as ctx,
        ):
            _write_pyproject(tmp, "verbose")
            configure(search_dir=tmp)
        message = str(ctx.exception)
        self.assertIn("verbose", message)
        self.assertIn("[tool.semlog].mode in pyproject.toml", message)
        self.assertNotIn("the mode parameter", message)
        self.assertNotIn("SEMLOG_MODE", message)

    def test_default_mode_is_full_with_no_source_given(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(search_dir=tmp)
        self.assertEqual("full", _modes.state.mode)


class RootLevelScopeTests(unittest.TestCase):
    """Proves: LP-010

    `configure()` sets an explicit root level only in `full` mode; hybrid
    and off must leave whatever level the root logger already had exactly
    as they found it."""

    def setUp(self):
        self.root = logging.getLogger()
        self._original_level = self.root.level
        self.root.setLevel(logging.WARNING)

    def tearDown(self):
        reset_pipeline()
        self.root.setLevel(self._original_level)
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_full_mode_sets_the_root_level(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(mode="full", search_dir=".")
        self.assertEqual(logging.INFO, self.root.getEffectiveLevel())

    def test_hybrid_mode_leaves_the_root_level_untouched(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(mode="hybrid", search_dir=".")
        self.assertEqual(logging.WARNING, self.root.level)

    def test_off_mode_leaves_the_root_level_untouched(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(mode="off", search_dir=".")
        self.assertEqual(logging.WARNING, self.root.level)


class ModeStateReloadTests(unittest.TestCase):
    """M5 (validate-wu25 #333): `_config.py` must reference `_modes.state`
    through the module (`from . import _modes`, then `_modes.state`), not
    via `from ._modes import state as name`. A bare name binding copies
    the object REFERENCE at import time; a later `importlib.reload` of
    `_modes` rebinds `_modes.state` to a brand-new `SimpleNamespace`, but
    a stale local name keeps pointing at the old one. `configure()` would
    then write `mode`/`marking` onto a disconnected object nobody else
    ever reads, so a mode change made after a reload never actually takes
    effect anywhere else in the process."""

    def tearDown(self):
        reset_pipeline()
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_configure_writes_reach_the_live_modes_state_after_a_reload(self):
        import importlib

        # A reload resets the module-level routing-armed guard (once that
        # attribute exists) without touching an already-installed wrapper;
        # `getattr`/a conditional restore keep this test valid across every
        # commit in this change, whether or not that attribute has landed
        # yet, and never let it make a later hybrid configure() double-wrap.
        saved_routing_armed = getattr(_modes, "_routing_armed", None)
        try:
            importlib.reload(_modes)
            with mock.patch.dict("os.environ", {}, clear=True):
                configure(mode="off", search_dir=".")
            # If configure() held a pre-reload reference, this reads the
            # fresh, correct object instead of a disconnected stale copy.
            self.assertEqual("off", _modes.state.mode)
        finally:
            if saved_routing_armed is not None:
                _modes._routing_armed = saved_routing_armed


if __name__ == "__main__":
    unittest.main()
