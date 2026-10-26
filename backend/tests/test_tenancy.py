"""Tenant isolation and scoping primitives (release gate)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.clinics.models import Location, Membership
from apps.core.errors import ApiError
from apps.core.tenancy import (
    ClinicScope,
    CrossClinicWriteError,
    UnscopedQueryError,
    current_scope,
    with_clinic,
)

pytestmark = pytest.mark.django_db


def make_owner_of_two(user_factory, clinic_factory, membership_factory):
    user = user_factory("owner-two@bright.test")
    first = clinic_factory(slug="iso-one", name="Isolation One")
    second = clinic_factory(slug="iso-two", name="Isolation Two")
    membership_factory(first, user)
    membership_factory(second, user)
    return user, first, second


def make_location(clinic, name="Jane Doe"):
    return Location.all_objects.create(clinic=clinic, name=name)


def test_client_scope_from_request(user_factory, clinic_factory, membership_factory):
    user, first, second = make_owner_of_two(
        user_factory, clinic_factory, membership_factory
    )

    request = SimpleNamespace(user=user, headers={"X-Clinic-Id": str(first.id)})
    scope = ClinicScope.from_request(request)
    assert scope is not None
    assert scope.clinic_id == first.id

    foreign = SimpleNamespace(user=user, headers={"X-Clinic-Id": "not-a-uuid"})
    with pytest.raises(ApiError) as excinfo:
        ClinicScope.from_request(foreign)
    assert excinfo.value.code == "not_found"

    missing = SimpleNamespace(user=user, headers={})
    assert ClinicScope.from_request(missing) is None
    with pytest.raises(ApiError) as excinfo:
        ClinicScope.from_request(missing, required=True)
    assert excinfo.value.code == "clinic_required"


def test_tenant_manager_requires_scope(user_factory, clinic_factory, membership_factory):
    _, first, second = make_owner_of_two(user_factory, clinic_factory, membership_factory)
    first_location = make_location(first, "One")
    second_location = make_location(second, "Two")

    with pytest.raises(UnscopedQueryError):
        list(Location.objects.all())

    assert Location.all_objects.count() == 2

    with with_clinic(first):
        assert list(Location.objects.all()) == [first_location]
        created = Location(name="Inferred")
        created.save()
        assert created.clinic_id == first.id

    assert current_scope() is None

    with with_clinic(second):
        assert list(Location.objects.all()) == [second_location]
        assert (
            Location.all_objects.for_clinic(ClinicScope(clinic_id=first.id)).count() == 2
        )


def test_save_guard_rejects_cross_clinic_writes(
    user_factory, clinic_factory, membership_factory
):
    _, first, second = make_owner_of_two(user_factory, clinic_factory, membership_factory)
    foreign = Location(clinic=first, name="Foreign")

    with with_clinic(second), pytest.raises(CrossClinicWriteError):
        foreign.save()

    assert not Location.all_objects.filter(name="Foreign").exists()


def test_cross_clinic_isolation_suite(
    api_client, user_factory, clinic_factory, membership_factory
):
    """Two clinics with colliding names must never leak across any endpoint."""

    owner, first, second = make_owner_of_two(
        user_factory, clinic_factory, membership_factory
    )
    first_location = make_location(first, "Jane Doe")
    second_location = make_location(second, "Jane Doe")
    api_client.force_login(owner)

    # 1-3: list is scoped and contains no foreign id.
    list_a = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/",
        HTTP_X_CLINIC_ID=str(first.id),
    )
    assert list_a.status_code == 200
    assert [row["id"] for row in list_a.json()] == [str(first_location.id)]
    assert str(second_location.id) not in list_a.content.decode()

    # 4-5: the other clinic sees only its own row.
    list_b = api_client.get(
        f"/api/v1/clinics/{second.id}/locations/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert list_b.status_code == 200
    assert [row["id"] for row in list_b.json()] == [str(second_location.id)]
    assert str(first_location.id) not in list_b.content.decode()

    # 6-8: search cannot cross the tenant boundary despite identical names.
    search_a = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/?search=Jane",
        HTTP_X_CLINIC_ID=str(first.id),
    )
    assert search_a.status_code == 200
    assert [row["id"] for row in search_a.json()] == [str(first_location.id)]
    assert str(second_location.id) not in search_a.content.decode()

    # 9-10: detail is readable in-clinic, 404 from the other clinic.
    detail_in = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/{first_location.id}/",
        HTTP_X_CLINIC_ID=str(first.id),
    )
    assert detail_in.status_code == 200
    assert detail_in.json()["id"] == str(first_location.id)

    detail_out = api_client.get(
        f"/api/v1/clinics/{first.id}/locations/{first_location.id}/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert detail_out.status_code == 404

    # 11-13: write attempts across clinics are 404 and change nothing.
    patch_out = api_client.patch(
        f"/api/v1/clinics/{first.id}/locations/{first_location.id}/",
        {"name": "Hijacked"},
        format="json",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert patch_out.status_code == 404
    delete_out = api_client.delete(
        f"/api/v1/clinics/{first.id}/locations/{first_location.id}/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert delete_out.status_code == 404
    first_location.refresh_from_db()
    assert first_location.name == "Jane Doe"

    # 14-16: member lists are tenant scoped and not reachable cross-clinic.
    members_a = api_client.get(
        f"/api/v1/clinics/{first.id}/members/",
        HTTP_X_CLINIC_ID=str(first.id),
    )
    assert members_a.status_code == 200
    assert {row["clinic_id"] for row in members_a.json()} == {str(first.id)}
    assert len(members_a.json()) == 1

    members_out = api_client.get(
        f"/api/v1/clinics/{first.id}/members/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert members_out.status_code == 404

    # 17-18: clinic detail itself is 404 when addressed with a foreign header.
    clinic_out = api_client.get(
        f"/api/v1/clinics/{first.id}/",
        HTTP_X_CLINIC_ID=str(second.id),
    )
    assert clinic_out.status_code == 404
    assert clinic_out.json()["error"]["code"] == "not_found"

    # 19: the other tenant's data is still intact.
    assert Location.all_objects.filter(clinic=second).count() == 1


def test_missing_header_with_two_memberships_is_clinic_required(
    api_client, user_factory, clinic_factory, membership_factory
):
    owner, first, _ = make_owner_of_two(user_factory, clinic_factory, membership_factory)
    api_client.force_login(owner)

    response = api_client.get(f"/api/v1/clinics/{first.id}/locations/")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "clinic_required"


def test_membership_query_is_scoped(user_factory, clinic_factory, membership_factory):
    _, first, second = make_owner_of_two(user_factory, clinic_factory, membership_factory)

    with pytest.raises(UnscopedQueryError):
        list(Membership.objects.all())

    with with_clinic(first):
        assert Membership.objects.count() == 1
        assert Membership.objects.first().clinic_id == first.id
    with with_clinic(second):
        assert Membership.objects.count() == 1
        assert Membership.objects.first().clinic_id == second.id
