"""Plain-layout Celery app fixture (no Django) for `tests/test_celery_
matrix.py`'s subprocess matrix (STANDARDS.md CEL-002/003/007, CP-020).
Loaded via `sys.executable -m celery -q -A tests._celery_plain_app
worker|beat ...` (global `-q` before `-A`, addendum A3). `semlog.celery
(app)` is called directly in this app-creation module, matching the
agent guide's own Celery recipe. See `tests/_celery_matrix_support.py`
for the shared task/burst/publish wiring and every recognized
environment variable. Not itself a `test*.py` file, so the traceability
checker skips it.
"""

from __future__ import annotations

import os

from celery import Celery

import semlog

from . import _celery_matrix_support as support

app = Celery("semlog-matrix-plain")
app.conf.update(
    broker_url="memory://",
    result_backend="cache+memory://",
)

semlog.configure(service_name="semlog-celery-matrix-plain", search_dir=".")

support.maybe_patch_no_sleep()

if os.environ.get("SEMLOG_CELERY_INTEGRATE", "1") != "0":
    semlog.celery(app)

marked_task = support.register(app)
