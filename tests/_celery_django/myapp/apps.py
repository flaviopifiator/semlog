"""The Django-layout fixture's own `AppConfig`: `configure()` goes in
`ready()`, matching the agent guide's own Django recipe (A5-M6: the
Celery recipe restates that Django keeps `configure()` in `AppConfig.
ready()`, never in `settings.py` or the Celery app-creation module).
"""

from __future__ import annotations

from django.apps import AppConfig

import semlog


class MatrixAppConfig(AppConfig):
    name = "tests._celery_django.myapp"

    def ready(self):
        semlog.configure(service_name="semlog-celery-matrix-django", search_dir=".")
