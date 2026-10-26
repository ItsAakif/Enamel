"""The optional dev-OTP file (F-06 remediation).

``apps.portal.adapters.otp.DevOtpAdapter`` can append the one-time code to the
file named by ``PORTAL_OTP_LOG_PATH``. The file is off by default; the compose
rehearsal stack turns it on so the end-to-end harness can read the code of a
synthetic patient when the API does not echo it (``DEBUG`` is false in the
production profile, so the echo is never allowed there).

These tests pin the contract: nothing is written when the path is empty, the
line is written when it is set, and a broken path never breaks the request.
"""

from __future__ import annotations

import json

import pytest

from apps.portal.adapters.otp import DevOtpAdapter

pytestmark = pytest.mark.django_db


def test_nothing_is_written_when_the_path_is_empty(settings, tmp_path):
    target = tmp_path / "portal_otp.log"
    settings.PORTAL_OTP_LOG_PATH = ""

    DevOtpAdapter().send(channel="email", contact="a@example.com", code="123456")

    # The adapter never creates a file on its own: the path has to be asked for.
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_the_code_is_appended_to_the_configured_file(settings, tmp_path):
    target = tmp_path / "nested" / "portal_otp.log"
    settings.PORTAL_OTP_LOG_PATH = str(target)

    DevOtpAdapter().send(channel="email", contact="a@example.com", code="123456")
    DevOtpAdapter().send(channel="sms", contact="+919999999999", code="654321")

    lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["channel"] == "email"
    assert first["contact"] == "a@example.com"
    assert first["code"] == "123456"
    assert first["ts"].endswith("Z")

    second = json.loads(lines[1])
    assert second["code"] == "654321"


def test_a_broken_path_never_breaks_the_request(settings):
    # A directory that cannot be created: the adapter has to swallow the error
    # so a code request still succeeds.
    settings.PORTAL_OTP_LOG_PATH = "/proc/definitely-not-writable/portal_otp.log"

    DevOtpAdapter().send(channel="email", contact="a@example.com", code="123456")


def test_the_request_still_succeeds_when_the_file_cannot_be_written(
    settings, owner, patient_factory
):
    from rest_framework.test import APIClient

    settings.PORTAL_OTP_LOG_PATH = "/proc/definitely-not-writable/portal_otp.log"
    patient = patient_factory(owner.clinic)
    client = APIClient()

    response = client.post(
        "/api/v1/portal/auth/request-code/",
        {"clinic_slug": owner.clinic.slug, "contact": patient.phone_e164},
        format="json",
    )

    assert response.status_code == 202, response.content


@pytest.mark.parametrize("value", ["", "   ", None])
def test_a_blank_path_is_treated_as_off(settings, tmp_path, value):
    target = tmp_path / "portal_otp.log"
    settings.PORTAL_OTP_LOG_PATH = value

    DevOtpAdapter().send(channel="email", contact="a@example.com", code="123456")

    assert not target.exists()
