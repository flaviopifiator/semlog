"""Real Celery CLI subprocess matrix (STANDARDS.md CEL-002, CEL-003,
CEL-007, CP-020; design #387 rev 2 + addendum #390 A2/A3/A6): drives
`sys.executable -m celery -q -A <fixture> worker ...` end to end (global
`-q` before `-A`, addendum A3), capturing real stdout/stderr, for both
the plain and Django-layout fixtures (`tests/_celery_plain_app.py`,
`tests/_celery_django/celery_app.py`). Skips cleanly, with an explicit
reason, when `celery` (and, for the Django rows, `django`) is not
installed, so `python -m unittest discover` stays stdlib-only
(CP-008/CP-013). Fixed argv, no shell (threat matrix: subprocess
invocations use a fixed argv list and no shell interpolation).

Run directly (celery-matrix CI job, phase 2):

    uv run --with "celery==5.6.3" python -m unittest tests.test_celery_matrix -v
    uv run --with "celery==5.6.3" --with "django==5.2" python -m unittest tests.test_celery_matrix -v
    uv run --python 3.10 --with "celery==5.2.7" python -m unittest tests.test_celery_matrix -v

Addendum A2: a pre-existing Celery 5.2.7 prefork SIGTERM race
(`RuntimeError('reentrant call ...')` from `apps/worker.py`'s own
`safe_say`) is unrelated to semlog; every prefork assertion here checks
only the JSON/text records actually produced, never the subprocess exit
code.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    import celery as _celery_pkg
except ImportError:  # pragma: no cover -- exercised only without celery installed
    _celery_pkg = None

try:
    import django as _django_pkg
except ImportError:  # pragma: no cover -- exercised only without django installed
    _django_pkg = None

REPO_ROOT = Path(__file__).resolve().parents[1]

# Celery's own text record format (design's Testing Strategy): a leading
# bracketed timestamp/level line, e.g. "[2026-09-10 17:52:14,460: INFO/
# MainProcess]". Direct stream writes (the banner, shutdown notices such
# as `safe_say`, and CLI warnings) never match this and are out of scope
# (SC-1, addendum A2/A5-M5).
_RECORD_LINE_RE = re.compile(
    r"^\[\d{4}-\d\d-\d\d .*: (DEBUG|INFO|WARNING|ERROR|CRITICAL)/"
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TS_RE = re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d([,.]\d+)?")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_ADDR_RE = re.compile(r"0x[0-9a-f]+")
_DUR_RE = re.compile(r"\d+\.\d+(e-\d+)?s\b")
_PID_RE = re.compile(r"pid[=: ]\d+")
_FORKPOOL_RE = re.compile(r"ForkPoolWorker-\d+")


def _normalize(text, *, tmp_path=None):
    """The design's own byte-identity normalization rules (Testing
    Strategy, `HybridByteIdentityTests`): strip run-variant values that
    are expected to differ between two otherwise-identical runs."""
    text = _ANSI_RE.sub("", text)
    text = _TS_RE.sub("<TS>", text)
    text = _UUID_RE.sub("<UUID>", text)
    text = _ADDR_RE.sub("<ADDR>", text)
    text = _DUR_RE.sub("<DUR>", text)
    text = _PID_RE.sub("pid=<PID>", text)
    text = _FORKPOOL_RE.sub("ForkPoolWorker-N", text)
    if tmp_path:
        text = text.replace(tmp_path, "<TMP>")
    return text


def _json_records(text):
    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _non_json_lines(text):
    lines = []
    for line in text.splitlines():
        try:
            json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)
    return lines


def _run_fixture(
    module, *, worker_args=(), env_extra=None, done_timeout=15, term_timeout=15
):
    """Run `sys.executable -m celery -q -A <module> worker ...`, wait for
    the fixture's own done-file (`tests/_celery_matrix_support.py`), send
    SIGTERM, and return `(returncode, stdout, stderr, done_seen, tmp_dir)`.
    """
    tmp_dir = tempfile.mkdtemp(prefix="semlog-celery-matrix-")
    done_file = os.path.join(tmp_dir, "done")
    env = dict(os.environ)
    env.pop("SEMLOG_MODE", None)
    env["SEMLOG_CELERY_DONE_FILE"] = done_file
    if env_extra:
        env.update({k: v for k, v in env_extra.items() if v is not None})
        for key, value in env_extra.items():
            if value is None:
                env.pop(key, None)
    # A fixed hostname removes kombu's own "No hostname was supplied"
    # warning, whose relative ordering against the "ready." line is
    # otherwise racy between two separate subprocess runs (observed on
    # the floor row) and unrelated to anything semlog does.
    argv = [
        sys.executable,
        "-m",
        "celery",
        "-q",
        "-A",
        module,
        "worker",
        "--hostname=semlogmatrixtest@localhost",
        *worker_args,
    ]
    proc = subprocess.Popen(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + done_timeout
    done_seen = False
    while time.monotonic() < deadline:
        if os.path.exists(done_file):
            done_seen = True
            break
        time.sleep(0.05)
    # A short grace period lets the worker finish writing the task's own
    # completion record before shutdown begins.
    time.sleep(0.3)
    proc.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = proc.communicate(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
    return proc.returncode, stdout, stderr, done_seen, tmp_dir


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class FullModeWorkerTests(unittest.TestCase):
    """Proves: CEL-002, CP-020

    Full mode: the task logger's WARNING/INFO/DEBUG records and the
    print-guard token each appear as exactly one JSON record, with no
    Celery-format text record duplicating them, across both fixture
    layouts and both the solo and prefork pools."""

    def _assert_full_mode(self, module, *, worker_args):
        _returncode, stdout, stderr, done_seen, _tmp = _run_fixture(
            module,
            worker_args=["--loglevel=DEBUG", *worker_args],
            env_extra={"SEMLOG_MODE": "full"},
        )
        self.assertTrue(done_seen, f"fixture never signaled done.\n{stdout}\n{stderr}")
        records = _json_records(stdout)

        for event_name in (
            "matrix.task_logger.warning",
            "matrix.task_logger.info",
            "matrix.task_logger.debug",
        ):
            with self.subTest(event_name=event_name):
                matching = [r for r in records if r.get("event_name") == event_name]
                self.assertEqual(1, len(matching), stdout)

        combined = stdout + stderr
        celery_format_lines = [
            line for line in combined.splitlines() if _RECORD_LINE_RE.match(line)
        ]
        self.assertEqual([], celery_format_lines, combined)

        print_records = [r for r in records if "matrix-print-token" in json.dumps(r)]
        self.assertEqual(1, len(print_records), stdout)
        raw_print_lines = [
            line for line in _non_json_lines(combined) if "matrix-print-token" in line
        ]
        self.assertEqual([], raw_print_lines, combined)

        marked = next(
            r for r in records if r.get("event_name") == "matrix.task_logger.info"
        )
        self.assertEqual(
            "f" * 32, marked.get("trace_id"), "publisher trace_id not continued"
        )

    def test_plain_layout_solo_pool(self):
        self._assert_full_mode("tests._celery_plain_app", worker_args=["-P", "solo"])

    def test_plain_layout_prefork_pool(self):
        self._assert_full_mode(
            "tests._celery_plain_app", worker_args=["-P", "prefork", "-c", "1"]
        )

    @unittest.skipIf(_django_pkg is None, "django is not installed in this environment")
    def test_django_layout_solo_pool(self):
        self._assert_full_mode(
            "tests._celery_django.celery_app", worker_args=["-P", "solo"]
        )

    @unittest.skipIf(_django_pkg is None, "django is not installed in this environment")
    def test_django_layout_prefork_pool(self):
        self._assert_full_mode(
            "tests._celery_django.celery_app", worker_args=["-P", "prefork", "-c", "1"]
        )


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class FullModeBeatTests(unittest.TestCase):
    """Proves: CEL-002, CP-020

    Addendum A5-M5: on the 5.2.7 row, beat prints its own banner even
    with `-q`, and a JSON record can land glued onto the banner's last
    line -- so this is a token-record-only assertion (beat's own startup
    record reaches JSON output at least once), never the stricter "no
    Celery-format text line at all" `FullModeWorkerTests` applies to a
    worker."""

    def _run_beat(self, module, *, env_extra=None, wait=3, term_timeout=10):
        env = dict(os.environ)
        env.pop("SEMLOG_MODE", None)
        if env_extra:
            env.update(env_extra)
        tmp_dir = tempfile.mkdtemp(prefix="semlog-celery-matrix-beat-")
        schedule_path = os.path.join(tmp_dir, "celerybeat-schedule")
        argv = [
            sys.executable,
            "-m",
            "celery",
            "-q",
            "-A",
            module,
            "beat",
            "--loglevel=INFO",
            f"-s={schedule_path}",
        ]
        proc = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(wait)
        proc.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=term_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
        # beat persists its own schedule state to a file; asserting that
        # none of it landed at the repository root (only under the
        # tempdir this method created above) guards against ever
        # regressing to a bare, cwd-relative `-s` default again.
        stray = list(REPO_ROOT.glob("celerybeat-schedule*"))
        self.assertEqual(
            [], stray, f"beat wrote a stray schedule file into the repo: {stray}"
        )
        return stdout, stderr

    def test_plain_layout_beat_startup_is_routed_as_json(self):
        stdout, stderr = self._run_beat(
            "tests._celery_plain_app", env_extra={"SEMLOG_MODE": "full"}
        )
        records = _json_records(stdout)
        starting = [r for r in records if "beat: Starting" in json.dumps(r)]
        self.assertEqual(1, len(starting), stdout + stderr)

    @unittest.skipIf(_django_pkg is None, "django is not installed in this environment")
    def test_django_layout_beat_startup_is_routed_as_json(self):
        stdout, stderr = self._run_beat(
            "tests._celery_django.celery_app", env_extra={"SEMLOG_MODE": "full"}
        )
        records = _json_records(stdout)
        starting = [r for r in records if "beat: Starting" in json.dumps(r)]
        self.assertEqual(1, len(starting), stdout + stderr)


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class HybridByteIdentityTests(unittest.TestCase):
    """Proves: CEL-003

    Hybrid mode leaves Celery's own text output byte-identical whether or
    not `celery(app)` was ever called, after the design's own
    normalization (SC-2, `--loglevel=INFO` pinned)."""

    @staticmethod
    def _drop_racy_kombu_warning(lines):
        # kombu's own "No hostname was supplied" connection warning is
        # unrelated to whether `celery(app)` was ever called, and its
        # relative position against the "ready." line races between two
        # separate subprocess runs of the SAME configuration (observed on
        # the floor row) -- excluded here the same way the design's own
        # normalization already scopes out direct-write banners and
        # shutdown notices (SC-1/SC-2), never part of the byte-identity
        # contract this test actually proves.
        return [line for line in lines if "No hostname was supplied" not in line]

    def test_plain_layout_output_is_identical_with_and_without_celery_app(self):
        _rc1, stdout_with, stderr_with, done1, tmp1 = _run_fixture(
            "tests._celery_plain_app",
            worker_args=["--loglevel=INFO", "-P", "solo"],
            env_extra={"SEMLOG_MODE": "hybrid", "SEMLOG_CELERY_INTEGRATE": "1"},
        )
        _rc2, stdout_without, stderr_without, done2, tmp2 = _run_fixture(
            "tests._celery_plain_app",
            worker_args=["--loglevel=INFO", "-P", "solo"],
            env_extra={"SEMLOG_MODE": "hybrid", "SEMLOG_CELERY_INTEGRATE": "0"},
        )
        self.assertTrue(done1, stdout_with + stderr_with)
        self.assertTrue(done2, stdout_without + stderr_without)

        normalized_stdout_with = self._drop_racy_kombu_warning(
            _normalize(stdout_with, tmp_path=tmp1).splitlines()
        )
        normalized_stdout_without = self._drop_racy_kombu_warning(
            _normalize(stdout_without, tmp_path=tmp2).splitlines()
        )
        self.assertEqual(normalized_stdout_without, normalized_stdout_with)

        normalized_stderr_with = self._drop_racy_kombu_warning(
            _normalize(stderr_with, tmp_path=tmp1).splitlines()
        )
        normalized_stderr_without = self._drop_racy_kombu_warning(
            _normalize(stderr_without, tmp_path=tmp2).splitlines()
        )
        self.assertEqual(normalized_stderr_without, normalized_stderr_with)


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class PreforkShutdownFlushTests(unittest.TestCase):
    """Proves: CEL-007

    design M3's discriminating fixture: a no-op `billiard.pool.time.sleep`
    removes the ~1s grace period that would otherwise mask a missing
    flush, and an import-time `worker_process_shutdown` receiver emits a
    burst of plain records BEFORE semlog's own flush receiver runs
    (connected later, design D5). Addendum A2: Celery 5.2.7's own
    pre-existing prefork SIGTERM race is ignored here on purpose; only the
    record count is asserted, never the subprocess exit code."""

    def test_every_burst_record_is_flushed_before_the_child_exits(self):
        burst_count = 2000
        _rc, stdout, stderr, done_seen, _tmp = _run_fixture(
            "tests._celery_plain_app",
            worker_args=["--loglevel=INFO", "-P", "prefork", "-c", "1"],
            env_extra={
                "SEMLOG_MODE": "full",
                "SEMLOG_CELERY_BURST": "1",
                "SEMLOG_CELERY_BURST_COUNT": str(burst_count),
                "SEMLOG_CELERY_NO_SLEEP": "1",
            },
            term_timeout=20,
        )
        self.assertTrue(done_seen, f"fixture never signaled done.\n{stdout}\n{stderr}")
        records = _json_records(stdout)
        burst_records = [
            r for r in records if r.get("event_name") == "matrix.burst.record"
        ]
        self.assertEqual(burst_count, len(burst_records))


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class SoloExitFlushTests(unittest.TestCase):
    """Proves: CEL-007

    The solo pool never sends `worker_process_shutdown`; this relies
    entirely on the pre-existing atexit-based drain (design D5's own
    "solo: no such signal; the atexit drain flushes" clause), triggered
    here through `worker_shutdown` instead."""

    def test_every_burst_record_is_flushed_on_solo_exit(self):
        burst_count = 2000
        _rc, stdout, stderr, done_seen, _tmp = _run_fixture(
            "tests._celery_plain_app",
            worker_args=["--loglevel=INFO", "-P", "solo"],
            env_extra={
                "SEMLOG_MODE": "full",
                "SEMLOG_CELERY_BURST_SOLO": "1",
                "SEMLOG_CELERY_BURST_COUNT": str(burst_count),
            },
            term_timeout=20,
        )
        self.assertTrue(done_seen, f"fixture never signaled done.\n{stdout}\n{stderr}")
        records = _json_records(stdout)
        burst_records = [
            r for r in records if r.get("event_name") == "matrix.burst.record"
        ]
        self.assertEqual(burst_count, len(burst_records))


if __name__ == "__main__":
    unittest.main()
