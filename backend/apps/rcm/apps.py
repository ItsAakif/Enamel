"""RCM app configuration (T134)."""

from __future__ import annotations

from django.apps import AppConfig


class RcmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rcm"
    verbose_name = "Insurance and RCM"
