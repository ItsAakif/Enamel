"""The demo-only single-role switch toggles FORCE RLS and restores it."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection


def _force_flag(table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "select relforcerowsecurity from pg_class where relname = %s", [table]
        )
        return bool(cursor.fetchone()[0])


@pytest.mark.django_db
def test_demo_single_role_disables_and_restores_force_rls():
    assert _force_flag("patients_patient") is True

    call_command("demo_single_role")
    assert _force_flag("patients_patient") is False

    call_command("demo_single_role", restore=True)
    assert _force_flag("patients_patient") is True


@pytest.mark.django_db
def test_demo_single_role_keeps_row_level_security_enabled():
    with connection.cursor() as cursor:
        cursor.execute(
            "select relrowsecurity from pg_class where relname = 'patients_patient'"
        )
        assert bool(cursor.fetchone()[0]) is True

    call_command("demo_single_role")

    with connection.cursor() as cursor:
        cursor.execute(
            "select relrowsecurity from pg_class where relname = 'patients_patient'"
        )
        assert bool(cursor.fetchone()[0]) is True
