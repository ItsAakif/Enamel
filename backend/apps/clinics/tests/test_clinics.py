"""Clinic, location, membership and invitation API tests."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.clinics.models import Clinic, Invitation, Location, Membership

pytestmark = pytest.mark.django_db

TOKEN_RE = re.compile(r"[?&]token=([A-Za-z0-9_\-]+)")


def token_from_mail(mailoutbox) -> str:
    match = TOKEN_RE.search(mailoutbox[-1].body)
    assert match
    return match.group(1)


def make_member(user_factory, clinic_factory, membership_factory, role="owner", slug="clinic-a"):
    user = user_factory(f"{role}-{slug}@bright.test")
    clinic = clinic_factory(slug=slug)
    membership = membership_factory(clinic, user, role=role)
    return user, clinic, membership


def test_clinic_create_adds_owner_membership_and_default_location(
    api_client, user_factory, clinic_factory, membership_factory
):
    user, first, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="first-clinic"
    )
    api_client.force_login(user)

    response = api_client.post(
        "/api/v1/clinics/",
        {"name": "Second Clinic", "slug": "second-clinic", "timezone": "Asia/Kolkata"},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["membership"]["role"] == "owner"
    second = Clinic.objects.get(slug="second-clinic")
    assert Location.all_objects.filter(clinic=second).count() == 1

    listing = api_client.get("/api/v1/clinics/")
    assert listing.status_code == 200
    assert {row["slug"] for row in listing.json()} == {"first-clinic", "second-clinic"}
    assert {row["id"] for row in listing.json()} == {str(first.id), str(second.id)}


def test_clinic_slug_collision_is_a_field_error(
    api_client, user_factory, clinic_factory, membership_factory
):
    user, _, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="taken-slug"
    )
    api_client.force_login(user)

    response = api_client.post(
        "/api/v1/clinics/",
        {"name": "Duplicate", "slug": "taken-slug"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "slug" in response.json()["error"]["field_errors"]


def test_clinic_scope_header_rules(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("multi-scope@bright.test")
    first = clinic_factory(slug="scope-one")
    second = clinic_factory(slug="scope-two")
    membership_factory(first, user)
    membership_factory(second, user)
    api_client.force_login(user)

    missing = api_client.get(f"/api/v1/clinics/{first.id}/locations/")
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "clinic_required"

    ok = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/",
        HTTP_X_CLINIC_ID=str(first.id),
    )
    assert ok.status_code == 200

    foreign_path = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert foreign_path.status_code == 404
    assert foreign_path.json()["error"]["code"] == "not_found"

    invalid_header = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/",
        HTTP_X_CLINIC_ID="not-a-uuid",
    )
    assert invalid_header.status_code == 404

    unknown_clinic = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/",
        HTTP_X_CLINIC_ID="00000000-0000-0000-0000-000000000000",
    )
    assert unknown_clinic.status_code == 404


def test_single_membership_is_auto_selected(
    api_client, user_factory, clinic_factory, membership_factory
):
    user, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="auto-clinic"
    )
    Location.all_objects.create(clinic=clinic, name="Main location")
    api_client.force_login(user)

    response = api_client.get(f"/api/v1/clinics/{clinic.id}/locations/")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_location_permissions_and_crud(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, role="owner", slug="perm-clinic"
    )
    dentist = user_factory("dentist-perm@bright.test")
    membership_factory(clinic, dentist, role="dentist")

    api_client.force_login(dentist)
    assert (
        api_client.get(
            f"/api/v1/clinics/{clinic.id}/locations/",
            HTTP_X_CLINIC_ID=str(clinic.id),
        ).status_code
        == 200
    )
    denied = api_client.post(
        f"/api/v1/clinics/{clinic.id}/locations/",
        {"name": "New room"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    members_denied = api_client.get(
        f"/api/v1/clinics/{clinic.id}/members/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert members_denied.status_code == 403

    api_client.force_login(owner)
    created = api_client.post(
        f"/api/v1/clinics/{clinic.id}/locations/",
        {"name": "Room 1"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert created.status_code == 201
    location_id = created.json()["id"]

    patched = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/locations/{location_id}/",
        {"name": "Room One"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Room One"

    deleted = api_client.delete(
        f"/api/v1/clinics/{clinic.id}/locations/{location_id}/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert deleted.status_code == 204
    assert not Location.all_objects.filter(pk=location_id).exists()


def test_unauthenticated_clinic_request_is_401(api_client, clinic_factory):
    clinic = clinic_factory(slug="anon-clinic")

    response = api_client.get(
        f"/api/v1/clinics/{clinic.id}/locations/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_last_owner_cannot_be_demoted_or_removed(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, membership = make_member(
        user_factory, clinic_factory, membership_factory, slug="last-owner"
    )
    api_client.force_login(owner)

    demote = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"role": "assistant"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "last_owner"

    removed = api_client.delete(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert removed.status_code == 409
    assert removed.json()["error"]["code"] == "last_owner"

    second_owner = user_factory("second-owner@bright.test")
    membership_factory(clinic, second_owner, role="owner")

    demote = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"role": "assistant"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "assistant"


def test_suspension_revokes_session_and_denies_clinic_access(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, role="owner", slug="susp-clinic"
    )
    dentist = user_factory("suspended@bright.test")
    membership = membership_factory(clinic, dentist, role="dentist")

    from rest_framework.test import APIClient

    dentist_client = APIClient()
    dentist_client.post(
        "/api/v1/auth/login/",
        {"email": "suspended@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    assert dentist_client.get("/api/v1/auth/me/").status_code == 200

    api_client.force_login(owner)
    response = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"status": "suspended"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    membership.refresh_from_db()
    assert membership.status == "suspended"
    assert membership.is_active is False

    # The live session is revoked by the epoch bump.
    revoked = dentist_client.get("/api/v1/auth/me/")
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "session_expired"

    # A fresh session is authenticated but has no active membership: the clinic
    # endpoints answer 403 (the clinic is not foreign to this user).
    fresh = APIClient()
    fresh.force_login(dentist)
    denied = fresh.get(
        f"/api/v1/clinics/{clinic.id}/locations/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


def test_member_suspension_can_be_reversed(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, role="owner", slug="reactivate"
    )
    assistant = user_factory("assistant-reactivate@bright.test")
    membership = membership_factory(clinic, assistant, role="assistant")
    api_client.force_login(owner)

    suspended = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"status": "suspended"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert suspended.status_code == 200

    active = api_client.patch(
        f"/api/v1/clinics/{clinic.id}/members/{membership.id}/",
        {"status": "active"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert active.status_code == 200
    assert active.json()["is_active"] is True


def test_invitation_accept_flow(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, role="owner", slug="invite-clinic"
    )
    invitee = user_factory("invitee@bright.test")
    api_client.force_login(owner)

    created = api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "invitee@bright.test", "role": "dentist"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert created.status_code == 201
    assert "token" not in created.json()
    invitation = Invitation.objects.get(pk=created.json()["id"])
    assert invitation.token_hash
    assert invitation.email == "invitee@bright.test"
    assert invitation.expires_at > timezone.now()
    assert mailoutbox[-1].to == ["invitee@bright.test"]

    raw_token = token_from_mail(mailoutbox)

    from rest_framework.test import APIClient

    invitee_client = APIClient()
    invitee_client.force_login(invitee)
    accepted = invitee_client.post(
        f"/api/v1/invitations/{raw_token}/accept/",
    )
    assert accepted.status_code == 200
    assert accepted.json()["membership"]["role"] == "dentist"
    assert accepted.json()["clinic"]["id"] == str(clinic.id)
    assert Membership.all_objects.get(clinic=clinic, user=invitee).is_active

    locations = invitee_client.get(
        f"/api/v1/clinics/{clinic.id}/locations/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert locations.status_code == 200

    replay = invitee_client.post(f"/api/v1/invitations/{raw_token}/accept/")
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "invitation_used"


def test_invitation_wrong_email_is_403(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="wrong-email"
    )
    wrong_user = user_factory("not-invited@bright.test")
    api_client.force_login(owner)
    api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "invited@bright.test", "role": "assistant"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    raw_token = token_from_mail(mailoutbox)

    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(wrong_user)
    response = client.post(f"/api/v1/invitations/{raw_token}/accept/")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invitation_email_mismatch"


def test_invitation_expired_and_revoked_codes(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="expiry-clinic"
    )
    invitee = user_factory("expiring@bright.test")
    api_client.force_login(owner)

    api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "expiring@bright.test", "role": "assistant"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    raw_token = token_from_mail(mailoutbox)

    from rest_framework.test import APIClient

    client = APIClient()
    with freeze_time(timezone.now() + timedelta(days=8)):
        client.force_login(invitee)
        expired = client.post(f"/api/v1/invitations/{raw_token}/accept/")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "invitation_expired"

    second = api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/",
        {"email": "expiring@bright.test", "role": "assistant"},
        format="json",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    second_token = token_from_mail(mailoutbox)
    revoked = api_client.post(
        f"/api/v1/clinics/{clinic.id}/invitations/{second.json()['id']}/revoke/",
        HTTP_X_CLINIC_ID=str(clinic.id),
    )
    assert revoked.status_code == 204

    response = client.post(f"/api/v1/invitations/{second_token}/accept/")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "invitation_revoked"


def test_reinvite_revokes_previous_token(
    api_client, user_factory, clinic_factory, membership_factory, mailoutbox
):
    owner, clinic, _ = make_member(
        user_factory, clinic_factory, membership_factory, slug="reinvite-clinic"
    )
    api_client.force_login(owner)

    for _ in range(2):
        api_client.post(
            f"/api/v1/clinics/{clinic.id}/invitations/",
            {"email": "reinvite@bright.test", "role": "assistant"},
            format="json",
            HTTP_X_CLINIC_ID=str(clinic.id),
        )

    first_match = TOKEN_RE.search(mailoutbox[0].body)
    assert first_match
    first_token = first_match.group(1)
    invitee = user_factory("reinvite@bright.test")

    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(invitee)
    response = client.post(f"/api/v1/invitations/{first_token}/accept/")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "invitation_revoked"
