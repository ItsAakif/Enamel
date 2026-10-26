"""Portal authentication: OTP, sessions and cookie separation (T121)."""

from __future__ import annotations

import pytest

from apps.portal.adapters.otp import OUTBOX
from apps.portal.models import PatientSession, PortalOtpChallenge

pytestmark = pytest.mark.django_db

REQUEST_CODE = "/api/v1/portal/auth/request-code/"
VERIFY = "/api/v1/portal/auth/verify/"


def _wrong_code(real: str) -> str:
    value = (int(real) + 1) % 1_000_000
    return f"{value:06d}"


def _request_code(client, clinic, contact):
    return client.post(
        REQUEST_CODE, {"clinic_slug": clinic.slug, "contact": contact}, format="json"
    )


def test_unknown_contact_returns_same_generic_response(owner, patient_factory):
    from rest_framework.test import APIClient

    known = patient_factory(owner.clinic, first_name="Known")
    client = APIClient()

    known_response = _request_code(client, owner.clinic, known.phone_e164)
    unknown_response = _request_code(client, owner.clinic, "+919999999999")

    assert known_response.status_code == 202
    assert unknown_response.status_code == 202
    assert set(known_response.json().keys()) == set(unknown_response.json().keys())
    assert unknown_response.json()["status"] == "accepted"
    # No patient data leaks for either response.
    assert "patient" not in unknown_response.json()
    assert PortalOtpChallenge.objects.count() == 1


def test_valid_code_creates_a_session(owner, patient_factory):
    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic)
    client = APIClient()
    response = _request_code(client, owner.clinic, patient.phone_e164)
    challenge_id = response.json()["challenge_id"]
    code = OUTBOX[-1]["code"]

    verified = client.post(
        VERIFY, {"challenge_id": challenge_id, "code": code}, format="json"
    )
    assert verified.status_code == 200, verified.content
    assert client.cookies["enamel_portal"].value
    assert "enamel_session" not in client.cookies
    assert PatientSession.objects.filter(patient=patient).count() == 1

    me = client.get("/api/v1/portal/me/")
    assert me.status_code == 200
    assert me.json()["patient"]["id"] == str(patient.id)


def test_sixth_wrong_attempt_invalidates_the_challenge(owner, patient_factory):
    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic)
    client = APIClient()
    response = _request_code(client, owner.clinic, patient.phone_e164)
    challenge_id = response.json()["challenge_id"]
    real = OUTBOX[-1]["code"]
    wrong = _wrong_code(real)

    for _ in range(6):
        attempt = client.post(
            VERIFY, {"challenge_id": challenge_id, "code": wrong}, format="json"
        )
        assert attempt.status_code == 400
        assert attempt.json()["error"]["code"] == "invalid_otp"

    challenge = PortalOtpChallenge.objects.get(pk=challenge_id)
    assert challenge.used_at is not None

    # Even the correct code is now rejected.
    retry = client.post(
        VERIFY, {"challenge_id": challenge_id, "code": real}, format="json"
    )
    assert retry.status_code == 400
    assert PatientSession.objects.count() == 0


def test_staff_cookie_cannot_access_portal(owner, patient_factory):
    patient_factory(owner.clinic)
    response = owner.get("/api/v1/portal/me/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_portal_cookie_cannot_access_staff_app(owner, patient_factory, portal_login):
    patient = patient_factory(owner.clinic)
    portal = portal_login(owner.clinic, patient)
    response = portal.get("/api/v1/patients/")
    assert response.status_code == 401


def test_logout_revokes_session_and_clears_cookie(owner, patient_factory, portal_login):
    patient = patient_factory(owner.clinic)
    portal = portal_login(owner.clinic, patient)

    assert portal.get("/api/v1/portal/me/").status_code == 200
    logout = portal.post("/api/v1/portal/auth/logout/")
    assert logout.status_code == 204
    assert PatientSession.objects.filter(patient=patient, revoked_at__isnull=True).count() == 0

    assert portal.get("/api/v1/portal/me/").status_code == 401
