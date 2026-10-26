"""Demo mode: the portal request-code response may reveal the code.

``DEMO_MODE`` is a deployment-level switch for the hosted public demo of this
repository. When off (the default, and the only state the production settings
allow) the request-code response is unchanged. When on, the response carries
the generated code in ``demo_code`` plus a ``demo_notice``, and the frontend
shows a clearly labelled demo panel. The production settings
(``config/settings/prod.py``) refuse to start with it; the dedicated demo
deployment runs ``config.settings.demo``.
"""

from __future__ import annotations

import pytest

from apps.portal.adapters.otp import OUTBOX
from apps.portal.models import PatientSession

pytestmark = pytest.mark.django_db

REQUEST_CODE = "/api/v1/portal/auth/request-code/"
VERIFY = "/api/v1/portal/auth/verify/"


def _request_code(client, clinic, contact):
    return client.post(
        REQUEST_CODE, {"clinic_slug": clinic.slug, "contact": contact}, format="json"
    )


def test_demo_mode_is_off_by_default():
    from django.conf import settings

    assert settings.DEMO_MODE is False


def test_request_code_default_returns_no_demo_code(owner, patient_factory):
    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic)
    client = APIClient()

    response = _request_code(client, owner.clinic, patient.phone_e164)

    assert response.status_code == 202
    assert "demo_code" not in response.json()
    assert "demo_notice" not in response.json()
    assert "code" not in response.json()  # DEBUG is false in the test settings


def test_request_code_in_demo_mode_returns_the_code_and_notice(owner, patient_factory):
    from django.test import override_settings
    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic)
    client = APIClient()

    with override_settings(DEMO_MODE=True):
        response = _request_code(client, owner.clinic, patient.phone_e164)

    assert response.status_code == 202
    body = response.json()
    assert body["demo_code"] == OUTBOX[-1]["code"]
    assert body["demo_notice"]
    # The code key stays reserved for the DEBUG echo, which the prod-shaped
    # test settings never enable.
    assert "code" not in body


def test_demo_mode_unknown_contact_still_gets_no_code(owner):
    from django.test import override_settings
    from rest_framework.test import APIClient

    client = APIClient()

    with override_settings(DEMO_MODE=True):
        response = _request_code(client, owner.clinic, "+919999999991")

    assert response.status_code == 202
    assert "demo_code" not in response.json()
    assert "demo_notice" not in response.json()


def test_demo_code_actually_signs_the_patient_in(owner, patient_factory):
    from django.test import override_settings
    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic, email="demo.patient@portal.test")
    client = APIClient()

    with override_settings(DEMO_MODE=True):
        response = _request_code(client, owner.clinic, patient.email)
    challenge_id = response.json()["challenge_id"]
    demo_code = response.json()["demo_code"]

    verified = client.post(VERIFY, {"challenge_id": challenge_id, "code": demo_code}, format="json")

    assert verified.status_code == 200, verified.content
    assert PatientSession.objects.filter(patient=patient, revoked_at__isnull=True).count() == 1
