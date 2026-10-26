"""T103 audit log tests: hash chain, redaction, API, immutability.

The suite covers:

1. an event carries actor, request id and clinic;
2. 100 sequential events verify and a raw-SQL tamper is detected;
3. sensitive fields are stored as ``"[redacted]"`` only;
4. audit rows are clinic-scoped (foreign clinic -> 404);
5. an existing row cannot be saved or deleted;
6. the query endpoint paginates and filters.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.core.audit import record, verify_chain
from apps.core.models import AuditEvent
from apps.core.rls import bypass_rls

pytestmark = pytest.mark.django_db

TOKEN_RE = re.compile(r"[?&]token=([A-Za-z0-9_\-]+)")


def token_from_mail(mailoutbox) -> str:
    match = TOKEN_RE.search(mailoutbox[-1].body)
    assert match
    return match.group(1)


def make_member(user_factory, clinic_factory, membership_factory, role="owner", slug="audit-clinic"):
    user = user_factory(f"{role}-{slug}@audit.test")
    clinic = clinic_factory(slug=slug)
    membership = membership_factory(clinic, user, role=role)
    return user, clinic, membership


def seed_events(clinic, count: int, *, actor=None, entity_id: str | None = None) -> None:
    for index in range(count):
        record(
            "location.updated",
            clinic=clinic,
            actor=actor,
            entity_type="clinics.location",
            entity_id=entity_id or str(uuid.uuid4()),
            after={"index": index},
        )


# --- chain integrity -------------------------------------------------------


def test_chain_verifies_for_100_sequential_events(clinic_factory):
    clinic = clinic_factory(slug="chain-100")

    seed_events(clinic, 100)

    ok, count, broken = verify_chain(clinic.id)
    assert ok is True
    assert count == 100
    assert broken is None

    rows = list(
        AuditEvent.objects.filter(clinic=clinic)
        .order_by("id")
        .values_list("prev_hash", "hash")
    )
    assert len(rows) == 100
    assert rows[0][0] == ""
    previous = ""
    for prev_hash, digest in rows:
        assert prev_hash == previous
        assert len(digest) == 64
        previous = digest


def test_chains_are_per_clinic(clinic_factory):
    first = clinic_factory(slug="chain-a")
    second = clinic_factory(slug="chain-b")

    seed_events(first, 3)
    seed_events(second, 2)

    ok_first, count_first, _ = verify_chain(first.id)
    ok_second, count_second, _ = verify_chain(second.id)

    assert (ok_first, count_first) == (True, 3)
    assert (ok_second, count_second) == (True, 2)
    # The second clinic's chain starts from the empty hash, not clinic A's head.
    first_event = AuditEvent.objects.filter(clinic=second).order_by("id").first()
    assert first_event.prev_hash == ""


def test_tampering_one_row_via_raw_sql_breaks_the_chain(clinic_factory):
    clinic = clinic_factory(slug="tamper-chain")
    seed_events(clinic, 5)
    target = list(AuditEvent.objects.filter(clinic=clinic).order_by("id"))[2]

    # The append-only trigger blocks ordinary UPDATEs; the maintenance bypass is
    # the explicit escape hatch used to simulate tampering for this test.
    with bypass_rls(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_auditevent SET after = %s::jsonb WHERE id = %s",
            [json.dumps({"index": 999}), target.pk],
        )

    ok, count, broken = verify_chain(clinic.id)
    assert ok is False
    assert broken == target.pk
    assert count == 2  # the two rows before the tampered one verified


def test_platform_events_share_one_chain(clinic_factory):
    clinic = clinic_factory(slug="platform-clinic")
    record("auth.password_reset_requested", entity_type="accounts.user", entity_id=str(uuid.uuid4()))
    record("auth.password_reset_requested", entity_type="accounts.user", entity_id=str(uuid.uuid4()))
    seed_events(clinic, 1)

    ok, count, broken = verify_chain(None)
    assert (ok, count, broken) == (True, 2, None)


# --- redaction -------------------------------------------------------------


def test_sensitive_fields_are_redacted(clinic_factory):
    clinic = clinic_factory(slug="redaction-clinic")

    event = record(
        "patient.updated",
        clinic=clinic,
        entity_type="patients.patient",
        entity_id=str(uuid.uuid4()),
        before={
            "notes": "patient reports sharp pain upper left molar",
            "phone": "+919000000000",
            "status": "active",
            "amount_minor": 100,
        },
        after={
            "notes": "changed",
            "full_name": "Jane Doe",
            "treatment_notes": "extraction planned",
            "address_json": {"line1": "12 Secret Street", "country": "IN"},
            "status": "archived",
            "amount_minor": 200,
            "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        },
    )
    event.refresh_from_db()

    assert event.before == {
        "notes": "[redacted]",
        "phone": "[redacted]",
        "status": "active",
        "amount_minor": 100,
    }
    assert event.after["notes"] == "[redacted]"
    assert event.after["full_name"] == "[redacted]"
    assert event.after["treatment_notes"] == "[redacted]"
    assert event.after["address_json"] == "[redacted]"
    assert event.after["status"] == "archived"
    assert event.after["amount_minor"] == 200
    assert event.after["id"] == "0f8fad5b-d9cb-469f-a165-70867728950e"

    stored = json.dumps({"before": event.before, "after": event.after})
    assert "sharp pain" not in stored
    assert "Secret Street" not in stored
    assert "Jane Doe" not in stored
    assert "+919000000000" not in stored


def test_nested_sensitive_fields_are_redacted(clinic_factory):
    clinic = clinic_factory(slug="nested-redaction")
    event = record(
        "patient.updated",
        clinic=clinic,
        entity_type="patients.patient",
        entity_id=str(uuid.uuid4()),
        after={
            "patient": {"notes": "free text", "number": "P-0001"},
            "allergies": [{"substance": "penicillin", "reaction": "rash"}],
            "flags": ["notes", "ok"],
        },
    )
    event.refresh_from_db()

    assert event.after["patient"]["notes"] == "[redacted]"
    assert event.after["patient"]["number"] == "P-0001"
    assert event.after["allergies"] == "[redacted]"
    assert event.after["flags"] == ["notes", "ok"]


def test_no_raw_ip_is_stored(clinic_factory):
    clinic = clinic_factory(slug="ip-clinic")
    event = record(
        "clinic.updated",
        clinic=clinic,
        entity_type="clinics.clinic",
        entity_id=str(clinic.pk),
        after={"name": "Renamed"},
    )
    event.refresh_from_db()

    # No request -> empty hash; with a request the raw address is never stored.
    assert event.ip_hash == ""

    from apps.core.audit import hash_ip

    digest = hash_ip("203.0.113.7")
    assert digest and digest != "203.0.113.7" and len(digest) == 64


# --- immutability ----------------------------------------------------------


def test_existing_rows_cannot_be_saved_or_deleted(clinic_factory):
    from apps.core.errors import AuditImmutableError

    clinic = clinic_factory(slug="immutable-clinic")
    seed_events(clinic, 1)
    event = AuditEvent.objects.filter(clinic=clinic).first()

    event.after = {"index": 999}
    with pytest.raises(AuditImmutableError):
        event.save()

    with pytest.raises(AuditImmutableError):
        event.delete()

    # And the stored row is untouched.
    event.refresh_from_db()
    assert event.after == {"index": 0}


# --- API -------------------------------------------------------------------


def test_audit_endpoint_is_clinic_scoped(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic_a, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="scope-a"
    )
    other_owner = user_factory("other-owner@audit.test")
    clinic_b = clinic_factory(slug="scope-b")
    membership_factory(clinic_b, other_owner, role="owner")

    seed_events(clinic_b, 2, actor=other_owner)

    api_client.force_login(owner)
    own = api_client.get(
        f"/api/v1/clinics/{clinic_a.id}/audit/",
        HTTP_X_CLINIC_ID=str(clinic_a.id),
    )
    assert own.status_code == 200
    assert own.json()["results"] == []

    foreign = api_client.get(
        f"/api/v1/clinics/{clinic_b.id}/audit/",
        HTTP_X_CLINIC_ID=str(clinic_a.id),
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    api_client.force_login(other_owner)
    visible = api_client.get(
        f"/api/v1/clinics/{clinic_b.id}/audit/",
        HTTP_X_CLINIC_ID=str(clinic_b.id),
    )
    assert visible.status_code == 200
    assert len(visible.json()["results"]) == 2


def test_audit_endpoint_requires_the_owner_role(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="role-audit"
    )
    dentist = user_factory("dentist-audit@audit.test")
    membership_factory(clinic, dentist, role="dentist")
    seed_events(clinic, 1, actor=owner)

    api_client.force_login(dentist)
    denied = api_client.get(
        f"/api/v1/clinics/{clinic.id}/audit/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


def test_audit_endpoint_filters_and_paginates(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="page-audit"
    )
    location_id = str(uuid.uuid4())
    membership_id = str(uuid.uuid4())
    seed_events(clinic, 25, actor=owner, entity_id=location_id)
    for index in range(5):
        record(
            "membership.role_changed",
            clinic=clinic,
            actor=owner,
            entity_type="clinics.membership",
            entity_id=membership_id,
            before={"role": "assistant"},
            after={"role": "dentist", "index": index},
        )

    api_client.force_login(owner)
    base = f"/api/v1/clinics/{clinic.id}/audit/"
    headers = {"HTTP_X_CLINIC_ID": str(clinic.id)}

    first = api_client.get(f"{base}?page_size=10", **headers)
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"next", "previous", "results"}
    assert body["previous"] is None
    assert body["next"] is not None
    assert len(body["results"]) == 10
    first_ids = [row["id"] for row in body["results"]]
    assert first_ids == sorted(first_ids, reverse=True)

    second = api_client.get(body["next"], **headers)
    assert second.status_code == 200
    second_ids = [row["id"] for row in second.json()["results"]]
    assert set(first_ids).isdisjoint(second_ids)
    assert max(second_ids) < min(first_ids)

    by_entity = api_client.get(
        f"{base}?entity_type=clinics.membership&entity_id={membership_id}",
        **headers,
    )
    assert by_entity.status_code == 200
    rows = by_entity.json()["results"]
    assert len(rows) == 5
    assert {row["action"] for row in rows} == {"membership.role_changed"}
    assert all(row["entity_id"] == membership_id for row in rows)

    in_range = api_client.get(
        f"{base}?from=2000-01-01&to=2099-12-31&page_size=100", **headers
    )
    assert len(in_range.json()["results"]) == 30

    empty_range = api_client.get(f"{base}?from=2000-01-01&to=2000-01-02", **headers)
    assert empty_range.json()["results"] == []


def test_audit_endpoint_validates_filters(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="invalid-audit"
    )
    seed_events(clinic, 1, actor=owner)
    api_client.force_login(owner)
    base = f"/api/v1/clinics/{clinic.id}/audit/"
    headers = {"HTTP_X_CLINIC_ID": str(clinic.id)}

    single_bound = api_client.get(f"{base}?from=2020-01-01", **headers)
    assert single_bound.status_code == 400
    assert single_bound.json()["error"]["code"] == "validation_error"

    bad_date = api_client.get(f"{base}?from=not-a-date&to=2020-01-01", **headers)
    assert bad_date.status_code == 400
    assert "from" in bad_date.json()["error"]["field_errors"]

    reversed_range = api_client.get(f"{base}?from=2021-01-01&to=2020-01-01", **headers)
    assert reversed_range.status_code == 400

    bad_cursor = api_client.get(f"{base}?cursor=not-a-cursor", **headers)
    assert bad_cursor.status_code == 400
    assert "cursor" in bad_cursor.json()["error"]["field_errors"]

    too_long = api_client.get(f"{base}?entity_type={'x' * 200}", **headers)
    assert too_long.status_code == 400


# --- service integration ---------------------------------------------------


def test_clinic_settings_update_captures_actor_and_request_id(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="settings-audit"
    )
    api_client.force_login(owner)

    response = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/",
        {"name": "Renamed Clinic"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
        HTTP_X_REQUEST_ID="req_audit_test_1",
    )

    assert response.status_code == 200
    event = AuditEvent.objects.get(clinic=clinic, action="clinic.updated")
    assert event.actor_id == owner.id
    assert event.request_id == "req_audit_test_1"
    assert event.entity_type == "clinics.clinic"
    assert event.entity_id == str(clinic.id)
    assert event.before == {"name": clinic.name}
    assert event.after == {"name": "Renamed Clinic"}
    assert event.ip_hash  # derived from REMOTE_ADDR, never the raw address
    assert event.ip_hash != "127.0.0.1"
    assert event.prev_hash == ""
    assert len(event.hash) == 64


def test_location_mutations_are_audited(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="location-audit"
    )
    api_client.force_login(owner)
    headers = {"HTTP_X_CLINIC_ID": str(clinic.id)}

    created = api_client.post(
        f"/api/v1/clinics/{clinic.id}/locations/",
        {"name": "Audit Room", "address_json": {"line1": "9 Hidden Way"}},
        format="json",
        **headers,
    )
    assert created.status_code == 201
    location_id = created.json()["id"]

    created_event = AuditEvent.objects.get(clinic=clinic, action="location.created")
    assert created_event.entity_id == location_id
    assert created_event.after["name"] == "Audit Room"
    assert created_event.after["address_json"] == "[redacted]"

    patched = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/locations/{location_id}/",
        {"name": "Audit Room Two"},
        format="json",
        **headers,
    )
    assert patched.status_code == 200
    updated_event = AuditEvent.objects.get(clinic=clinic, action="location.updated")
    assert updated_event.before == {"name": "Audit Room"}
    assert updated_event.after == {"name": "Audit Room Two"}

    deleted = api_client.delete(
        f"/api/v1/clinics/{clinic.id}/locations/{location_id}/",
        **headers,
    )
    assert deleted.status_code == 204
    deleted_event = AuditEvent.objects.get(clinic=clinic, action="location.deleted")
    assert deleted_event.entity_id == location_id

    ok, count, _ = verify_chain(clinic.id)
    assert ok is True and count == 3


def test_membership_role_change_is_audited(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="member-audit"
    )
    target = user_factory("promoted@audit.test")
    membership = membership_factory(clinic, target, role="assistant")
    api_client.force_login(owner)

    response = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"role": "dentist"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )

    assert response.status_code == 200
    event = AuditEvent.objects.get(clinic=clinic, action="membership.role_changed")
    assert event.entity_type == "clinics.membership"
    assert event.entity_id == str(membership.id)
    assert event.before == {"role": "assistant"}
    assert event.after == {"role": "dentist"}
    assert event.actor_id == owner.id


def test_membership_status_change_and_removal_are_audited(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="status-audit"
    )
    target = user_factory("suspended-audit@audit.test")
    membership = membership_factory(clinic, target, role="assistant")
    api_client.force_login(owner)
    headers = {"HTTP_X_CLINIC_ID": str(clinic.id)}

    suspended = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"status": "suspended"},
        format="json",
        **headers,
    )
    assert suspended.status_code == 200
    status_event = AuditEvent.objects.get(clinic=clinic, action="membership.status_changed")
    assert status_event.after == {"status": "suspended"}

    removed = api_client.delete(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        **headers,
    )
    assert removed.status_code == 204
    removed_event = AuditEvent.objects.get(clinic=clinic, action="membership.removed")
    assert removed_event.after == {"status": "removed"}


def test_invitation_lifecycle_is_audited(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="invite-audit"
    )
    invitee = user_factory("invite-audit@audit.test")
    api_client.force_login(owner)
    headers = {"HTTP_X_CLINIC_ID": str(clinic.id)}

    created = api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "invite-audit@audit.test", "role": "dentist"},
        format="json",
        **headers,
    )
    assert created.status_code == 201
    created_event = AuditEvent.objects.get(clinic=clinic, action="invitation.created")
    assert created_event.after["email"] == "[redacted]"
    assert created_event.after["role"] == "dentist"

    raw_token = token_from_mail(mailoutbox)
    from rest_framework.test import APIClient

    invitee_client = APIClient()
    invitee_client.force_login(invitee)
    accepted = invitee_client.post(f"/api/v1/invitations/{raw_token}/accept/")
    assert accepted.status_code == 200
    accepted_event = AuditEvent.objects.get(clinic=clinic, action="invitation.accepted")
    assert accepted_event.actor_id == invitee.id
    assert accepted_event.after["role"] == "dentist"

    # A second invitation can be revoked, producing its own event.
    api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "revoke-audit@audit.test", "role": "assistant"},
        format="json",
        **headers,
    )
    second_id = (
        AuditEvent.objects.filter(clinic=clinic, action="invitation.created")
        .order_by("-id")
        .first()
        .entity_id
    )
    revoked = api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/{second_id}/revoke/",
        **headers,
    )
    assert revoked.status_code == 204
    assert AuditEvent.objects.filter(clinic=clinic, action="invitation.revoked").count() == 1

    ok, _, _ = verify_chain(clinic.id)
    assert ok is True


def test_login_and_logout_are_audited_for_a_single_clinic_user(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("login-audit@audit.test")
    clinic = clinic_factory(slug="login-audit")
    membership_factory(clinic, user, role="owner")

    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": "login-audit@audit.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    assert login.status_code == 200
    assert AuditEvent.objects.filter(clinic=clinic, action="auth.login").count() == 1

    logout = api_client.post(
        "/api/v1/auth/logout/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert logout.status_code == 204
    logout_event = AuditEvent.objects.get(clinic=clinic, action="auth.logout")
    assert logout_event.actor_id == user.id


def test_login_audit_is_skipped_for_an_ambiguous_clinic_user(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("multi-login@audit.test")
    first = clinic_factory(slug="multi-login-a")
    second = clinic_factory(slug="multi-login-b")
    membership_factory(first, user, role="owner")
    membership_factory(second, user, role="owner")

    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": "multi-login@audit.test", "password": "Str0ngPass!2026"},
        format="json",
    )

    assert response.status_code == 200
    # Documented deviation: no single clinic can be resolved, so no event.
    assert AuditEvent.objects.filter(action="auth.login").count() == 0


def test_password_reset_request_and_confirm_are_audited(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    user = user_factory("reset-audit@audit.test")
    clinic = clinic_factory(slug="reset-audit")
    membership_factory(clinic, user, role="owner")

    api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "reset-audit@audit.test"},
        format="json",
    )
    requested = AuditEvent.objects.get(action="auth.password_reset_requested")
    assert requested.clinic_id == clinic.id
    assert requested.actor_id == user.id

    raw_token = token_from_mail(mailoutbox)
    confirmed = api_client.post(
        "/api/v1/auth/password/confirm/",
        {"token": raw_token, "new_password": "N3wStr0ngPass!2026"},
        format="json",
    )
    assert confirmed.status_code == 204
    event = AuditEvent.objects.get(action="auth.password_reset_confirmed")
    assert event.clinic_id == clinic.id
    assert event.actor_id == user.id
    assert "password" not in json.dumps(event.after or {})
    assert event.after is None


def test_failed_audit_rolls_back_the_mutation(
    api_client, user_factory, clinic_factory, membership_factory, monkeypatch
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="rollback-audit"
    )
    api_client.force_login(owner)
    original_name = clinic.name

    def explode(*args, **kwargs):
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr("apps.clinics.views.record_audit", explode)

    response = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/",
        {"name": "Should Not Persist"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    clinic.refresh_from_db()
    assert clinic.name == original_name
    assert AuditEvent.objects.filter(clinic=clinic).count() == 0


# --- management command ----------------------------------------------------


def test_audit_verify_command_reports_a_healthy_chain(clinic_factory, capsys):
    clinic = clinic_factory(slug="verify-command")
    seed_events(clinic, 4)

    call_command("audit_verify", "--clinic", str(clinic.id))

    out = capsys.readouterr().out
    assert "chain ok: 4 events" in out


def test_audit_verify_command_exits_non_zero_on_tamper(clinic_factory, capsys):
    clinic = clinic_factory(slug="verify-tamper")
    seed_events(clinic, 3)
    target = list(AuditEvent.objects.filter(clinic=clinic).order_by("id"))[1]

    with bypass_rls(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_auditevent SET action = %s WHERE id = %s",
            ["location.deleted", target.pk],
        )

    with pytest.raises(SystemExit) as excinfo:
        call_command("audit_verify", "--clinic", str(clinic.id))

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert f"id={target.pk}" in err


def test_audit_verify_command_rejects_a_bad_uuid():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("audit_verify", "--clinic", "not-a-uuid")


def test_events_are_timestamped_and_ordered(clinic_factory):
    clinic = clinic_factory(slug="order-clinic")
    seed_events(clinic, 3)

    created = list(
        AuditEvent.objects.filter(clinic=clinic).order_by("id").values_list("created_at", flat=True)
    )
    assert created == sorted(created)
    assert all(value <= timezone.now() for value in created)
