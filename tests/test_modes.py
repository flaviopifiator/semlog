"""Execution-mode resolution tests (LM-001) and the root-logger level
scoping that comes with it (LP-010, scoped to `full` mode only).

Precedence: explicit `configure(mode=...)` parameter > `SEMLOG_MODE`
environment variable > `[tool.semlog].mode` in `pyproject.toml` (3.11+
only, mirroring SI-001/SI-005's existing precedent) > the `"full"`
default. A value that is present but outside `"full"`/`"hybrid"`/`"off"`,
from any source, raises `ValueError` naming the invalid value and its
source -- except an EMPTY `SEMLOG_MODE`, which is treated as absent and
falls through to the next source (validate-wu69 #338, m3): an empty
string under `[tool.semlog].mode` is still a real declared value and
still raises.
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
    """`configure(mode=...)` resolution precedence and validation.

    Proves: LM-001
    """

    def tearDown(self):
        reset_pipeline()
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_explicit_parameter_wins_over_every_other_source(self):
        # `service_name` is given explicitly (irrelevant to mode
        # precedence itself) so this full-mode configure() never reaches
        # the SI-005 pyproject-detection diagnostic, which on Python 3.10
        # would otherwise leak a real JSON line to the process's actual
        # stdout during the suite (NIT 2, validate-wu25b #337).
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"SEMLOG_MODE": "hybrid"}, clear=True),
        ):
            _write_pyproject(tmp, "off")
            configure(mode="full", service_name="svc", search_dir=tmp)
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
        # `service_name` avoids `_identity.py`'s own, separately-checked
        # real `sys.version_info` (only `semlog._modes.sys.version_info` is
        # mocked here): on an actual 3.10 interpreter this configure() call
        # would otherwise also hit the SI-005 "no tomllib" diagnostic.
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("semlog._modes.sys.version_info", (3, 10, 0, "final", 0)),
        ):
            _write_pyproject(tmp, "hybrid")
            configure(service_name="svc", search_dir=tmp)
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
            configure(service_name="svc", search_dir=tmp)
        self.assertEqual("full", _modes.state.mode)

    def test_empty_semlog_mode_env_var_is_treated_as_absent(self):
        """validate-wu69 #338, m3: an empty `SEMLOG_MODE` (declared but
        left blank, a common deployment idiom) falls through to the next
        source instead of being treated as an invalid value."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"SEMLOG_MODE": ""}, clear=True),
        ):
            _write_pyproject(tmp, "hybrid")
            configure(service_name="svc", search_dir=tmp)
        expected = "hybrid" if sys.version_info >= (3, 11) else "full"
        self.assertEqual(expected, _modes.state.mode)

    @unittest.skipUnless(
        sys.version_info >= (3, 11), "pyproject.toml mode detection needs tomllib"
    )
    def test_empty_pyproject_mode_value_raises_value_error(self):
        """validate-wu69 #338, m3: unlike an empty `SEMLOG_MODE`, an empty
        string under `[tool.semlog].mode` is a real declared value, not an
        absent one, and still raises like any other invalid value."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError) as ctx,
        ):
            _write_pyproject(tmp, "")
            configure(service_name="svc", search_dir=tmp)
        message = str(ctx.exception)
        self.assertIn("[tool.semlog].mode in pyproject.toml", message)


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
            configure(mode="full", service_name="svc", search_dir=".")
        self.assertEqual(logging.INFO, self.root.getEffectiveLevel())

    def test_hybrid_mode_leaves_the_root_level_untouched(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(mode="hybrid", service_name="svc", search_dir=".")
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


class PreConfigureOffTests(unittest.TestCase):
    """`SEMLOG_MODE=off` is read at import time (no file I/O), so it is
    honored even before `configure()` ever runs.

    Proves: LM-005
    """

    def setUp(self):
        import semlog._context as context_module

        self._context_module = context_module
        self._saved_warned = context_module._bind_warned
        context_module._bind_warned = False
        self._saved_mode = _modes.state.mode
        self._saved_marking = _modes.state.marking

    def tearDown(self):
        self._context_module._bind_warned = self._saved_warned
        _modes.state.mode = self._saved_mode
        _modes.state.marking = self._saved_marking

    def test_initial_mode_reads_semlog_mode_env_var_without_file_io(self):
        with mock.patch.dict("os.environ", {"SEMLOG_MODE": "off"}, clear=True):
            self.assertEqual("off", _modes._initial_mode())

    def test_bind_before_configure_stays_silent_when_initial_mode_is_off(self):
        _modes.state.mode = "off"  # simulates import-time SEMLOG_MODE=off
        from semlog._context import bind, current

        logger = logging.getLogger("semlog")
        sentinel = []
        handler = logging.Handler()
        handler.emit = lambda record: sentinel.append(record)
        logger.addHandler(handler)
        try:
            bind({"app.a": 1})  # must not raise, must not warn
        finally:
            logger.removeHandler(handler)
        self.assertEqual([], sentinel)
        self.assertIsNone(current())


if __name__ == "__main__":
    unittest.main()
