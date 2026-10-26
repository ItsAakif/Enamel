"""Provider CRUD: permissions, clinic scoping and audit (T112)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.clinics.models import Provider
from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db


def test_owner_can_create_list_patch_and_delete_a_provider(
    owner, membership_factory, user_factory
):
    member = user_factory("newdent@enamel.test")
    membership = membership_factory(owner.clinic, member, role="dentist")

    created = owner.post(
        reverse("scheduling:provider-list"),
        {
            "membership": str(membership.id),
            "display_name": "Dr New",
            "specialty": "Endodontics",
            "color": "#abcdef",
            "is_bookable": True,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["display_name"] == "Dr New"
    assert body["user_name"] == "Newdent"
    assert body["role"] == "dentist"
    provider_id = body["id"]
    assert AuditEvent.objects.filter(
        clinic=owner.clinic, action="provider.created"
    ).count() == 1

    listing = owner.get(reverse("scheduling:provider-list"))
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [provider_id]

    patched = owner.patch(
        reverse("scheduling:provider-detail", args=[provider_id]),
        {"display_name": "Dr Renamed", "is_bookable": False},
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Dr Renamed"
    assert patched.json()["is_bookable"] is False
    assert AuditEvent.objects.filter(
        clinic=owner.clinic, action="provider.updated"
    ).count() == 1

    deleted = owner.delete(reverse("scheduling:provider-detail", args=[provider_id]))
    assert deleted.status_code == 204
    assert not Provider.all_objects.filter(pk=provider_id).exists()
    assert AuditEvent.objects.filter(
        clinic=owner.clinic, action="provider.deleted"
    ).count() == 1


def test_provider_requires_scheduling_types_manage(assistant, membership_factory, user_factory):
    member = user_factory("blocked@enamel.test")
    membership = membership_factory(assistant.clinic, member, role="dentist")

    response = assistant.post(
        reverse("scheduling:provider-list"),
        {"membership": str(membership.id), "display_name": "Dr Blocked"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_duplicate_membership_is_rejected(owner, provider_factory):
    provider = provider_factory(owner.clinic, membership=owner.membership)

    response = owner.post(
        reverse("scheduling:provider-list"),
        {"membership": str(owner.membership.id), "display_name": "Dr Duplicate"},
    )

    assert response.status_code == 400
    assert "membership" in response.json()["error"]["field_errors"]
    assert Provider.all_objects.filter(clinic=owner.clinic).count() == 1
    assert provider.membership_id == owner.membership.id


def test_foreign_membership_is_rejected(owner, clinic_factory, membership_factory, user_factory):
    other = clinic_factory(slug="provider-foreign")
    member = user_factory("foreign@enamel.test")
    membership = membership_factory(other, member, role="dentist")

    response = owner.post(
        reverse("scheduling:provider-list"),
        {"membership": str(membership.id), "display_name": "Dr Foreign"},
    )

    assert response.status_code == 400
    assert "membership" in response.json()["error"]["field_errors"]


def test_cross_clinic_provider_is_404(owner, clinic_factory, provider_factory):
    other = clinic_factory(slug="provider-other")
    foreign = provider_factory(other)

    response = owner.get(reverse("scheduling:provider-detail", args=[str(foreign.id)]))

    assert response.status_code == 404
