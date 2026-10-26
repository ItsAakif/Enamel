"""Patient search, filtering, ordering and cross-clinic isolation (T106)."""

from __future__ import annotations

import pytest

from apps.patients.tests.conftest import patient_payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_clinics(owner, staff_factory):
    other = staff_factory(role="owner")

    first = owner.post(
        "/api/v1/patients/",
        patient_payload(
            first_name="Aarav",
            last_name="Sharma",
            phone="+91 98765 00001",
            email="aarav.sharma@example.in",
        ),
    ).json()
    second = owner.post(
        "/api/v1/patients/",
        patient_payload(
            first_name="Meera",
            last_name="Iyer",
            phone="+91 98765 00002",
            email="meera.iyer@example.in",
        ),
    ).json()
    # Same-looking patient in another clinic: search must never cross over.
    foreign = other.post(
        "/api/v1/patients/",
        patient_payload(
            first_name="Aarav",
            last_name="Sharma",
            phone="+91 98765 00001",
            email="aarav.sharma@example.in",
        ),
    ).json()
    return owner, other, first, second, foreign


def _names(response):
    return [row["full_name"] for row in response.json()["results"]]


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("aar", {"Aarav Sharma"}),
        ("sharma", {"Aarav Sharma"}),
        ("Aarav Sharma", {"Aarav Sharma"}),
        ("iyer", {"Meera Iyer"}),
        ("SHARMA", {"Aarav Sharma"}),
    ],
)
def test_search_matches_partial_names(two_clinics, term, expected):
    owner, _other, _first, _second, _foreign = two_clinics
    response = owner.get("/api/v1/patients/", {"search": term})
    assert response.status_code == 200
    assert set(_names(response)) == expected


def test_search_matches_number(two_clinics):
    owner, _other, first, _second, _foreign = two_clinics
    response = owner.get("/api/v1/patients/", {"search": first["number"]})
    assert set(_names(response)) == {"Aarav Sharma"}

    partial = owner.get("/api/v1/patients/", {"search": first["number"][-4:]})
    assert set(_names(partial)) == {"Aarav Sharma"}


@pytest.mark.parametrize("term", ["9876500002", "+91 98765 00002", "+919876500002"])
def test_search_matches_normalized_phone(two_clinics, term):
    owner, _other, _first, _second, _foreign = two_clinics
    response = owner.get("/api/v1/patients/", {"search": term})
    assert set(_names(response)) == {"Meera Iyer"}


@pytest.mark.parametrize("term", ["meera.iyer@", "MEERA.IYER@EXAMPLE.IN", "example.in"])
def test_search_matches_email(two_clinics, term):
    owner, _other, _first, _second, _foreign = two_clinics
    response = owner.get("/api/v1/patients/", {"search": term})
    assert "Meera Iyer" in _names(response)


def test_search_never_crosses_clinics(two_clinics):
    owner, other, first, _second, foreign = two_clinics

    scoped = owner.get("/api/v1/patients/", {"search": "Sharma"})
    assert [row["id"] for row in scoped.json()["results"]] == [first["id"]]

    other_scoped = other.get("/api/v1/patients/", {"search": "Sharma"})
    assert [row["id"] for row in other_scoped.json()["results"]] == [foreign["id"]]

    by_number = owner.get("/api/v1/patients/", {"search": foreign["number"]})
    # Both clinics number from 1, so the number exists in both; only the local
    # row may be returned.
    assert {row["id"] for row in by_number.json()["results"]} == {first["id"]}
    assert other.get("/api/v1/patients/", {"search": first["number"]}).json()["count"] == 1


def test_status_source_and_location_filters(owner, patient_factory):
    from apps.clinics.models import Location

    room = Location.all_objects.create(clinic=owner.clinic, name="Filter room")
    patient_factory(
        owner.clinic,
        first_name="Active",
        last_name="Person",
        status="active",
        source="staff",
        primary_location=room,
    )
    patient_factory(
        owner.clinic,
        first_name="Inactive",
        last_name="Person",
        status="inactive",
        source="import",
        primary_location=None,
    )

    assert owner.get("/api/v1/patients/", {"status": "inactive"}).json()["count"] == 1
    assert owner.get("/api/v1/patients/", {"source": "import"}).json()["count"] == 1
    assert owner.get("/api/v1/patients/", {"location": str(room.id)}).json()["count"] == 1
    assert owner.get("/api/v1/patients/", {"status": "deceased"}).json()["count"] == 0


def test_ordering_options_and_invalid_value(owner, patient_factory):
    patient_factory(owner.clinic, first_name="Zed", last_name="Aardvark")
    patient_factory(owner.clinic, first_name="Amy", last_name="Zulu")

    newest_first = owner.get("/api/v1/patients/", {"ordering": "-created_at"})
    assert [row["last_name"] for row in newest_first.json()["results"]] == ["Zulu", "Aardvark"]

    by_number = owner.get("/api/v1/patients/", {"ordering": "-number"})
    assert [row["number"] for row in by_number.json()["results"]] == ["EN-000002", "EN-000001"]

    invalid = owner.get("/api/v1/patients/", {"ordering": "last_name; DROP TABLE"})
    assert invalid.status_code == 400
    error = invalid.json()["error"]
    assert error["code"] == "validation_error"
    assert "ordering" in error["field_errors"]


def test_search_pagination_keeps_the_filter(owner, patient_factory):
    for index in range(5):
        patient_factory(owner.clinic, first_name=f"Searchable{index}", last_name="Common")

    first_page = owner.get("/api/v1/patients/", {"search": "Searchable", "page_size": 2})
    body = first_page.json()
    assert body["count"] == 5
    assert len(body["results"]) == 2
    assert "search=Searchable" in body["next"]

    second_page = owner.get("/api/v1/patients/", {"search": "Searchable", "page_size": 2, "page": 2})
    assert len(second_page.json()["results"]) == 2
    assert {row["id"] for row in body["results"]}.isdisjoint(
        {row["id"] for row in second_page.json()["results"]}
    )
