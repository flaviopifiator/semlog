"""The Django-layout fixture's Celery app-creation module: `semlog.
celery(app)` is called here, directly after `Celery()` construction,
exactly like the plain-layout fixture and the agent guide's own recipe
-- only `configure()` moves to `AppConfig.ready()` (`myapp/apps.py`).
Loaded via `sys.executable -m celery -q -A tests._celery_django.
celery_app worker|beat ...` (global `-q` before `-A`, addendum A3), with
`DJANGO_SETTINGS_MODULE=tests._celery_django.settings` set in the
subprocess environment before this module is imported, so the Celery
Django fixup connects on construction. See `tests/_celery_matrix_
support.py` for the shared task/burst/publish wiring and every
recognized environment variable. Not itself a `test*.py` file, so the
traceability checker skips it.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests._celery_django.settings")

from celery import Celery

import semlog

from .. import _celery_matrix_support as support

app = Celery("semlog-matrix-django")
app.config_from_object("django.conf:settings", namespace="CELERY")

support.maybe_patch_no_sleep()

if os.environ.get("SEMLOG_CELERY_INTEGRATE", "1") != "0":
    semlog.celery(app)

marked_task = support.register(app)
