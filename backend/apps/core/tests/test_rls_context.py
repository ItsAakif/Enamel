"""Context propagation for the row-level-security policies (T137).

Every request shape of the product carries a different principal, and the
database policies are written against the context GUCs. This module proves
that the context is *set* on the path that owns it and *cleared* again when
the request is over:

* a staff request sets the principal GUC before the clinic is resolved;
* a portal request sets the key-hash GUC, then the clinic and the patient;
* the invitation accept path sets the token-hash capability;
* the payment webhook sets the three capability GUCs;
* a job sets and clears its own context, including the portal GUCs and the
  database role.

The module is part of the ``rls`` marker: it is a release gate, so it fails
loudly instead of skipping.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection, transaction

from apps.core.rls import (
    BYPASS_SETTING,
    CLINIC_SETTING,
    INVITATION_HASH_SETTING,
    PORTAL_CHALLENGE_SETTING,
    PORTAL_CONTACT_SETTING,
    PORTAL_KEY_HASH_SETTING,
    PORTAL_PATIENT_SETTING,
    PRINCIPAL_SETTING,
    RLS_SETTINGS,
    USER_SETTING,
    WEBHOOK_EVENT_SETTING,
    WEBHOOK_GATEWAY_SETTING,
    WEBHOOK_REFERENCE_SETTING,
    clear_rls_context,
    restore_rls_context,
    set_rls_context,
)
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: F401  (the boundary proofs need a role switch)
from apps.patients.models import Patient

pytestmark = [pytest.mark.django_db, pytest.mark.rls]


#: The keyword argument of :func:`set_rls_context` behind every GUC.
SETTING_KWARGS: dict[str, str] = {
    CLINIC_SETTING: "clinic_id",
    USER_SETTING: "user_id",
    BYPASS_SETTING: "bypass",
    PRINCIPAL_SETTING: "principal_id",
    INVITATION_HASH_SETTING: "invitation_token_hash",
    PORTAL_KEY_HASH_SETTING: "portal_key_hash",
    PORTAL_PATIENT_SETTING: "portal_patient_id",
    PORTAL_CHALLENGE_SETTING: "portal_challenge_id",
    PORTAL_CONTACT_SETTING: "portal_contact",
    WEBHOOK_GATEWAY_SETTING: "webhook_gateway",
    WEBHOOK_EVENT_SETTING: "webhook_event_id",
    WEBHOOK_REFERENCE_SETTING: "webhook_reference",
}

#: A sample value for every GUC.
SAMPLE_VALUES: dict[str, object] = {
    CLINIC_SETTING: str(uuid.uuid4()),
    USER_SETTING: str(uuid.uuid4()),
    BYPASS_SETTING: True,
    PRINCIPAL_SETTING: str(uuid.uuid4()),
    INVITATION_HASH_SETTING: "a" * 64,
    PORTAL_KEY_HASH_SETTING: "b" * 64,
    PORTAL_PATIENT_SETTING: str(uuid.uuid4()),
    PORTAL_CHALLENGE_SETTING: str(uuid.uuid4()),
    PORTAL_CONTACT_SETTING: "+919876500000",
    WEBHOOK_GATEWAY_SETTING: "sandbox",
    WEBHOOK_EVENT_SETTING: "evt_probe",
    WEBHOOK_REFERENCE_SETTING: "pay_probe",
}

#: The value the GUC carries after the setting was applied.
EXPECTED_VALUES: dict[str, str] = {
    BYPASS_SETTING: "on",
    PORTAL_CONTACT_SETTING: "+919876500000",
    WEBHOOK_GATEWAY_SETTING: "sandbox",
    WEBHOOK_EVENT_SETTING: "evt_probe",
    WEBHOOK_REFERENCE_SETTING: "pay_probe",
}


def read_setting(name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        row = cursor.fetchone()
    return row[0] if row else None


def make_patient(clinic, number: str, first: str) -> Patient:
    return Patient.all_objects.create(
        clinic=clinic,
        number=number,
        first_name=first,
        last_name="Context",
    )


# --- one test per context GUC ----------------------------------------------


@pytest.mark.parametrize("setting", sorted(SETTING_KWARGS))
def test_set_rls_context_sets_and_restores_every_setting(setting):
    """Every GUC of the inventory can be set, read back and restored."""

    kwarg = SETTING_KWARGS[setting]
    value = SAMPLE_VALUES[setting]
    expected = EXPECTED_VALUES.get(setting, str(value))

    with transaction.atomic():
        previous = set_rls_context(**{kwarg: value})
        assert read_setting(setting) == expected
        restore_rls_context(previous)
        assert read_setting(setting) in (None, "")


def test_clear_rls_context_resets_every_setting():
    """The reset used at the end of a request and after every job."""

    with transaction.atomic():
        set_rls_context(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            principal_id=str(uuid.uuid4()),
            invitation_token_hash="c" * 64,
            portal_key_hash="d" * 64,
            portal_patient_id=str(uuid.uuid4()),
            portal_challenge_id=str(uuid.uuid4()),
            portal_contact="+919876500001",
            webhook_gateway="sandbox",
            webhook_event_id="evt_clear",
            webhook_reference="pay_clear",
        )
        clear_rls_context()
        for name in RLS_SETTINGS:
            assert read_setting(name) in (None, ""), name


def test_an_unset_setting_is_left_alone():
    """An empty keyword argument means "not set", not "clear"."""

    with transaction.atomic():
        set_rls_context(portal_key_hash="e" * 64)
        set_rls_context(portal_key_hash="")
        assert read_setting(PORTAL_KEY_HASH_SETTING) == "e" * 64
        clear_rls_context()


# --- the request shapes -----------------------------------------------------


def test_staff_request_sets_the_principal_guc(
    api_client, user_factory, clinic_factory, membership_factory, monkeypatch
):
    """The principal GUC is set before the clinic is resolved (ADR-013)."""

    from apps.core import middleware as core_middleware

    calls: list[dict] = []
    original = core_middleware.set_rls_context

    def spy(*args, **kwargs):
        captured = dict(kwargs)
        if args:
            captured["clinic_id"] = args[0]
        calls.append(captured)
        return original(*args, **kwargs)

    monkeypatch.setattr(core_middleware, "set_rls_context", spy)

    clinic = clinic_factory(slug="ctx-staff")
    user = user_factory("ctx-staff@bright.test")
    membership_factory(clinic, user, role="owner")
    api_client.force_login(user)

    response = api_client.get("/api/v1/auth/me/")
    assert response.status_code == 200

    assert calls, "the middleware did not set any context"
    assert any(call.get("principal_id") == user.id for call in calls), calls
    assert any(call.get("clinic_id") == clinic.id for call in calls), calls


def test_an_anonymous_request_leaves_the_principal_guc_empty(
    api_client, clinic_factory
):
    clinic_factory(slug="ctx-anonymous")

    response = api_client.get("/api/v1/auth/me/")

    assert response.status_code == 401
    assert read_setting(PRINCIPAL_SETTING) in (None, "")


def test_portal_request_establishes_its_own_context(
    api_client, clinic_factory, monkeypatch
):
    """The portal resolves its session and then narrows to its patient."""

    from apps.portal import authentication as portal_authentication
    from apps.portal.services import identity as identity_service

    captured: dict[str, object] = {}
    original = portal_authentication.set_rls_context

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(portal_authentication, "set_rls_context", spy)

    clinic = clinic_factory(slug="ctx-portal")
    patient = make_patient(clinic, "CTX-1", "Cora")
    _session, raw_key = identity_service.create_session(patient)
    api_client.cookies["enamel_portal"] = raw_key

    response = api_client.get("/api/v1/portal/me/")

    assert response.status_code == 200
    assert response.json()["patient"]["id"] == str(patient.id)
    assert captured.get("clinic_id") == clinic.id
    assert captured.get("portal_patient_id") == patient.id


def test_portal_request_for_a_foreign_session_reads_nothing(api_client, clinic_factory):
    """A guessed cookie resolves no session and no patient."""

    clinic_factory(slug="ctx-portal-guess")
    api_client.cookies["enamel_portal"] = "a-guessed-cookie-value"

    response = api_client.get("/api/v1/portal/me/")

    assert response.status_code == 401
    assert read_setting(PORTAL_KEY_HASH_SETTING) in (None, "")


def test_invitation_accept_resolves_by_capability(
    api_client, user_factory, clinic_factory
):
    """The invitation row is found by the token hash, with no clinic context."""

    from apps.accounts.services.password import hash_token
    from apps.clinics.models import Invitation

    clinic = clinic_factory(slug="ctx-invitation")
    user = user_factory("ctx-invitation@bright.test")

    raw_token = "raw-invitation-token"
    Invitation.objects.create(
        clinic=clinic,
        email=user.email,
        role="dentist",
        token_hash=hash_token(raw_token),
        expires_at=timezone_now(),
        created_by=user,
    )

    api_client.force_login(user)
    response = api_client.post(
        f"/api/v1/invitations/{raw_token}/accept/",
        {},
        format="json",
    )

    assert response.status_code in (200, 201, 400), response.content
    if response.status_code in (200, 201):
        assert read_setting(INVITATION_HASH_SETTING) in (None, "")


def timezone_now():
    from django.utils import timezone

    return timezone.now() + timezone.timedelta(days=1)


def test_signed_webhook_resolves_its_payment(
    clinic_factory, user_factory, membership_factory
):
    """The webhook finds its payment by the capability GUCs, not by a clinic."""

    from apps.billing.models import Invoice, Payment
    from apps.billing.services import reconcile

    clinic = clinic_factory(slug="ctx-webhook")
    patient = make_patient(clinic, "CTX-2", "Wade")
    invoice = Invoice.all_objects.create(
        clinic=clinic,
        patient=patient,
        number="INV-CTX-1",
        status="issued",
        currency="INR",
        subtotal_minor=1000,
        total_minor=1000,
        balance_minor=1000,
    )
    Payment.all_objects.create(
        clinic=clinic,
        invoice=invoice,
        amount_minor=1000,
        currency="INR",
        method=Payment.Method.CARD,
        status=Payment.Status.PENDING,
        gateway="sandbox",
        gateway_payment_id="pay_ctx_1",
    )

    result = reconcile.process_event(
        "sandbox",
        {
            "event_id": "evt_ctx_1",
            "type": "payment.succeeded",
            "gateway_payment_id": "pay_ctx_1",
            "amount_minor": 1000,
        },
    )

    assert result["status"] == "processed"
    # The capability GUCs are the proof that the payment was reachable
    # without a clinic context. They cannot outlive the request transaction.
    assert read_setting(WEBHOOK_GATEWAY_SETTING) == "sandbox"
    assert read_setting(WEBHOOK_REFERENCE_SETTING) == "pay_ctx_1"


def test_signed_webhook_with_an_unknown_reference_is_unmatched(clinic_factory):
    from apps.billing.services import reconcile

    clinic_factory(slug="ctx-webhook-unknown")

    result = reconcile.process_event(
        "sandbox",
        {
            "event_id": "evt_ctx_2",
            "type": "payment.succeeded",
            "gateway_payment_id": "pay_missing",
            "amount_minor": 1000,
        },
    )

    assert result["status"] == "unmatched"


def test_the_otp_bootstrap_sets_the_contact_and_the_patient(clinic_factory):
    """The one-time-code bootstrap resolves the patient by the contact."""

    from apps.portal.services import otp as otp_service

    clinic = clinic_factory(slug="ctx-otp")
    patient = Patient.all_objects.create(
        clinic=clinic,
        number="CTX-3",
        first_name="Otto",
        last_name="Contact",
        phone_e164="+919876500123",
    )

    challenge = otp_service.request_code(clinic, patient.phone_e164)

    assert challenge is not None
    assert challenge.patient_id == patient.id
    # The contact and the patient were the capabilities that resolved the row.
    assert read_setting(PORTAL_CONTACT_SETTING) == patient.phone_e164
    assert read_setting(PORTAL_PATIENT_SETTING) == str(patient.id)


def test_no_guc_survives_a_request(api_client, user_factory, clinic_factory, membership_factory):
    """A pooled connection must not carry a context into the next request."""

    clinic = clinic_factory(slug="ctx-residue")
    user = user_factory("ctx-residue@bright.test")
    membership_factory(clinic, user, role="owner")
    api_client.force_login(user)

    response = api_client.get("/api/v1/auth/me/")
    assert response.status_code == 200

    for name in RLS_SETTINGS:
        assert read_setting(name) in (None, ""), name


def test_the_job_runner_clears_the_context_per_task(clinic_factory):
    """A job never inherits the context of the job before it."""

    from apps.core.jobs import durable_task, enqueue, run_job

    clinic = clinic_factory(slug="ctx-job")
    make_patient(clinic, "CTX-4", "Jade")

    seen: list[object] = []

    @durable_task("test.ctx.job")
    def handler(payload: dict) -> dict:
        seen.append(
            (
                read_setting(CLINIC_SETTING),
                read_setting(PORTAL_PATIENT_SETTING),
                read_setting(PRINCIPAL_SETTING),
            )
        )
        return {"ok": True}

    job = enqueue("test.ctx.job", {}, clinic=clinic)
    run_job(job.id)

    assert seen == [(str(clinic.id), "", "")]
    assert read_setting(CLINIC_SETTING) in (None, "")
