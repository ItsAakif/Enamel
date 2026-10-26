"""The portal flow must run under the portal role in every suite run (F-10).

The patient portal is a **separate principal** with its own database role
(``enamel_portal`` and its bootstrap role ``enamel_portal_auth``). The
middleware switches the connection to that role with ``SET LOCAL ROLE`` so the
patient-scoped policies are in force.

The switch is refused when the connection may not ``SET ROLE``. The test
fixture role (``enamel_test``) is a member of no role, so without the owner
connection the portal requests of that run would execute under the fixture
role and the portal policies would stay unexercised. This module proves that
the portal role is actually in force while a portal request runs - in the
owner run and in the test-fixture-role run alike.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


@contextmanager
def _record_role_switches():
    """Record every role the portal middleware switches to, with the role the
    connection actually ran under."""

    from apps.core import portal_context

    seen: list[tuple[str, str]] = []
    original = portal_context.set_rls_role

    def spy(role):
        result = original(role)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            seen.append((role, cursor.fetchone()[0]))
        return result

    with mock.patch.object(portal_context, "set_rls_role", spy):
        yield seen


@pytest.fixture
def role_switches():
    return _record_role_switches


def test_the_portal_request_runs_under_the_portal_role(
    owner, patient_factory, portal_login, role_switches
):
    """The authenticated portal request runs as ``enamel_portal``."""

    patient = patient_factory(owner.clinic)
    portal = portal_login(owner.clinic, patient)

    with role_switches() as seen:
        response = portal.get("/api/v1/portal/appointments/")

    assert response.status_code == 200, response.content
    assert seen, "the portal request did not switch the role at all"
    roles = {role for role, _ in seen}
    assert "enamel_portal" in roles, roles
    for role, current in seen:
        assert current == role, (role, current)


def test_the_portal_bootstrap_runs_under_the_bootstrap_role(
    owner, patient_factory, role_switches
):
    """The one-time-code bootstrap runs as ``enamel_portal_auth``."""

    from apps.portal.adapters.otp import OUTBOX

    patient = patient_factory(owner.clinic)
    from rest_framework.test import APIClient

    client = APIClient()
    with role_switches() as seen:
        requested = client.post(
            "/api/v1/portal/auth/request-code/",
            {"clinic_slug": owner.clinic.slug, "contact": patient.phone_e164},
            format="json",
        )
        assert requested.status_code == 202, requested.content
        challenge_id = requested.json()["challenge_id"]
        code = OUTBOX[-1]["code"]
        verified = client.post(
            "/api/v1/portal/auth/verify/",
            {"challenge_id": challenge_id, "code": code},
            format="json",
        )
        assert verified.status_code == 200, verified.content

    roles = {role for role, _ in seen}
    assert "enamel_portal_auth" in roles, roles
    for role, current in seen:
        assert current == role, (role, current)
