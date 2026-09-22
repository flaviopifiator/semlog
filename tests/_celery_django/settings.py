"""Minimal Django settings for the Celery matrix's Django-layout fixture.
Only what `django.setup()` and the Celery Django fixup need; no database
migration or web-serving concern applies here."""

from __future__ import annotations

SECRET_KEY = "semlog-celery-matrix-fixture"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "tests._celery_django.myapp",
]

# Read via `app.config_from_object("django.conf:settings", namespace="CELERY")`
# in `tests/_celery_django/celery_app.py`: the namespaced form Django/Celery
# projects use in practice.
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
