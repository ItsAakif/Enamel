"""Core app configuration.

Connects the RLS maintenance bypass to every new database connection when the
process was started for a migration/maintenance command (see ``manage.py``).
Request and Celery processes never set ``ENAMEL_RLS_BYPASS``, so their
connections keep RLS enforced.

It also carries the **platform-admin mode** (ADR-013, decision D-1): the
separately permissioned admin service connects with its own role and switches
the connection to owner-level access, so the platform-wide rows (the audit
chain, every clinic) are reachable there and nowhere else.
"""

from __future__ import annotations

import os
import re

from django.apps import AppConfig
from django.db.backends.signals import connection_created

#: A role name is interpolated into a statement, so it is validated first.
_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _enable_bypass(sender, connection, **kwargs) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.bypass_rls', 'on', false)")


def _enable_platform_admin(sender, connection, **kwargs) -> None:
    """Switch the connection to the platform-admin role (owner-level access)."""

    if connection.vendor != "postgresql":
        return
    role = os.environ.get("PLATFORM_ADMIN_ROLE", "").strip()
    if not role:
        return
    if not _ROLE_NAME_RE.match(role):
        raise ValueError(f"Invalid PLATFORM_ADMIN_ROLE: {role!r}")
    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self) -> None:
        if os.environ.get("ENAMEL_RLS_BYPASS") == "1":
            connection_created.connect(
                _enable_bypass,
                dispatch_uid="apps.core.rls.enable_bypass",
            )
        if os.environ.get("PLATFORM_ADMIN_MODE") == "1":
            connection_created.connect(
                _enable_platform_admin,
                dispatch_uid="apps.core.rls.enable_platform_admin",
            )
