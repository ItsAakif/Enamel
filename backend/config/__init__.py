"""Enamel Django project package.

The Celery application is imported here so ``@shared_task`` registrations work in
worker processes. The import is guarded so tooling (migrations, static collection,
plain Django imports) still works if Celery is unavailable.
"""

try:  # pragma: no cover - import guard
    from .celery import app as celery_app
except ImportError:  # pragma: no cover - Celery is a declared dependency
    celery_app = None

__all__ = ("celery_app",)
