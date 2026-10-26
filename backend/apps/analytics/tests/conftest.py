"""Fixtures shared by the analytics tests (T133)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from apps.clinics.models import Location, Provider
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number
from apps.scheduling.models import Appointment, AppointmentType, AvailabilityRule

MONTH_START = date(2026, 6, 1)
MONTH_END = date(2026, 6, 30)
#: June 2026 has 22 weekdays; the rule is Monday-Friday 09:00-12:00 (180 min).
EXPECTED_AVAILABLE_MINUTES = 22 * 180


class StaffClient:
    def __init__(self, client, user, clinic, membership, role: str) -> None:
        self.client = client
        self.user = user
        self.clinic = clinic
        self.membership = membership
        self.role = role

    def _kwargs(self, kwargs: dict) -> dict:
        kwargs.setdefault("HTTP_X_CLINIC_ID", str(self.clinic.id))
        return kwargs

    def get(self, url, params=None, **kwargs):
        return self.client.get(url, params, **self._kwargs(kwargs))

    def post(self, url, data=None, **kwargs):
        kwargs.setdefault("format", "json")
        return self.client.post(url, data, **self._kwargs(kwargs))


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, **extra):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(f"{role}-{suffix}@enamel.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"clinic-{suffix}", **extra)
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def assistant(staff_factory):
    return staff_factory(role="assistant")


@pytest.fixture
def location_factory(db):
    def _create(clinic, *, name: str = "Main", **extra):
        return Location.all_objects.create(clinic=clinic, name=name, **extra)

    return _create


@pytest.fixture
def provider_factory(db, user_factory, membership_factory):
    def _create(clinic, *, display_name: str = "Dr Test", membership=None, **extra):
        if membership is None:
            suffix = uuid.uuid4().hex[:8]
            user = user_factory(f"prov-{suffix}@enamel.test")
            membership = membership_factory(clinic, user, role="dentist")
        return Provider.all_objects.create(
            clinic=clinic, membership=membership, display_name=display_name, **extra
        )

    return _create


@pytest.fixture
def appointment_type_factory(db):
    def _create(clinic, *, code: str = "checkup", name: str = "Checkup", **extra):
        return AppointmentType.all_objects.create(
            clinic=clinic, code=code, name=name, duration_min=30, **extra
        )

    return _create


@pytest.fixture
def patient_factory(db):
    def _create(clinic, *, first_name="Ada", last_name="Lovelace", **extra):
        return Patient.all_objects.create(
            clinic=clinic,
            number=allocate_patient_number(clinic),
            first_name=first_name,
            last_name=last_name,
            **extra,
        )

    return _create


@pytest.fixture
def rule_factory(db):
    def _create(clinic, location, *, weekday=0, start_time=time(9, 0), end_time=time(12, 0), **extra):
        if isinstance(start_time, str):
            start_time = time.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = time.fromisoformat(end_time)
        return AvailabilityRule.all_objects.create(
            clinic=clinic,
            location=location,
            kind=AvailabilityRule.Kind.WORKING,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
            **extra,
        )

    return _create


@pytest.fixture
def make_appointment(db):
    def _create(seed, start, *, end=None, status="scheduled", **extra):
        appointment_type = extra.pop("appointment_type", seed["appointment_type"])
        if end is None:
            end = start + timedelta(minutes=appointment_type.duration_min)
        return Appointment.all_objects.create(
            clinic=extra.pop("clinic", seed["clinic"]),
            location=extra.pop("location", seed["location"]),
            patient=extra.pop("patient", seed["patient"]),
            provider=extra.pop("provider", seed["provider"]),
            appointment_type=appointment_type,
            start_at=start,
            end_at=end,
            status=status,
            **extra,
        )

    return _create


def utc(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    """A UTC instant for a clinic-local (Asia/Kolkata) wall-clock time."""

    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def seeded_month(
    owner,
    location_factory,
    provider_factory,
    appointment_type_factory,
    patient_factory,
    rule_factory,
    make_appointment,
):
    """A hand-computable June 2026 clinic.

    Appointments: 3 completed, 1 cancelled, 1 no_show, 1 scheduled, 1 confirmed
    (7 total; 5 consume booked time -> 150 minutes). Availability: 22 weekdays
    x 180 minutes = 3960 minutes.

    Money: one invoice issued in the window (total 500000, paid 300000,
    balance 200000) and two succeeded payments summing 300000 plus one pending
    payment that must not be counted.

    Messaging/recall/waitlist: 2 recalls due + 1 completed, 2 reminders sent
    (1 delivered), 1 failed, 3 waitlist offers (1 claimed, 1 expired).
    """

    from apps.billing.models import Invoice, Payment
    from apps.messaging.models import Message, RecallStatus, RecallType
    from apps.scheduling.models import WaitlistEntry, WaitlistOffer

    clinic = owner.clinic
    location = location_factory(clinic)
    provider = provider_factory(clinic, membership=owner.membership)
    appointment_type = appointment_type_factory(clinic)
    for weekday in range(5):
        rule_factory(clinic, location, weekday=weekday)

    p1 = patient_factory(clinic)
    p2 = patient_factory(clinic, first_name="Grace")
    p3 = patient_factory(clinic, first_name="Alan")
    seed = {
        "clinic": clinic,
        "location": location,
        "provider": provider,
        "appointment_type": appointment_type,
        "patient": p1,
    }

    make_appointment(seed, utc(2026, 6, 1, 9, 0), patient=p1, status="completed")
    make_appointment(seed, utc(2026, 6, 2, 9, 0), patient=p1, status="completed")
    make_appointment(seed, utc(2026, 6, 3, 9, 0), patient=p1, status="cancelled")
    # p2 is returning: an appointment exists before the window.
    make_appointment(seed, utc(2026, 5, 20, 9, 0), patient=p2, status="completed")
    make_appointment(seed, utc(2026, 6, 1, 10, 0), patient=p2, status="completed")
    make_appointment(seed, utc(2026, 6, 2, 10, 0), patient=p2, status="scheduled")
    make_appointment(seed, utc(2026, 6, 4, 9, 0), patient=p3, status="no_show")
    make_appointment(seed, utc(2026, 6, 5, 9, 0), patient=p3, status="confirmed")

    invoice = Invoice.all_objects.create(
        clinic=clinic,
        patient=p1,
        number="INV-000001",
        status=Invoice.Status.PARTIALLY_PAID,
        currency="INR",
        subtotal_minor=500000,
        total_minor=500000,
        paid_minor=300000,
        balance_minor=200000,
        issued_at=utc(2026, 6, 2, 11, 0),
    )
    Payment.all_objects.create(
        clinic=clinic,
        invoice=invoice,
        amount_minor=200000,
        currency="INR",
        status=Payment.Status.SUCCEEDED,
        captured_at=utc(2026, 6, 2, 12, 0),
    )
    Payment.all_objects.create(
        clinic=clinic,
        invoice=invoice,
        amount_minor=100000,
        currency="INR",
        status=Payment.Status.SUCCEEDED,
        captured_at=utc(2026, 6, 3, 12, 0),
    )
    Payment.all_objects.create(
        clinic=clinic,
        invoice=invoice,
        amount_minor=999999,
        currency="INR",
        status=Payment.Status.PENDING,
        captured_at=utc(2026, 6, 4, 12, 0),
    )

    recall_type = RecallType.all_objects.create(
        clinic=clinic, name="Six month", interval_months=6, template_key="recall"
    )
    RecallStatus.all_objects.create(
        clinic=clinic,
        patient=p1,
        recall_type=recall_type,
        due_at=utc(2026, 6, 10, 9, 0),
        status=RecallStatus.Status.DUE,
    )
    RecallStatus.all_objects.create(
        clinic=clinic,
        patient=p2,
        recall_type=recall_type,
        due_at=utc(2026, 6, 20, 9, 0),
        status=RecallStatus.Status.DUE,
    )
    RecallStatus.all_objects.create(
        clinic=clinic,
        patient=p3,
        recall_type=recall_type,
        due_at=utc(2026, 5, 1, 9, 0),
        last_completed_at=utc(2026, 6, 15, 9, 0),
        status=RecallStatus.Status.COMPLETED,
    )

    def _message(patient, status, *, sent=None, delivered=None, created=None):
        message = Message.all_objects.create(
            clinic=clinic,
            patient=patient,
            channel="sms",
            purpose="appointment_reminders",
            status=status,
            sent_at=sent,
            delivered_at=delivered,
        )
        if created is not None:
            Message.all_objects.filter(pk=message.pk).update(created_at=created)
        return message

    _message(p1, Message.Status.DELIVERED, sent=utc(2026, 6, 1, 8, 0), delivered=utc(2026, 6, 1, 8, 1), created=utc(2026, 6, 1, 7, 0))
    _message(p2, Message.Status.SENT, sent=utc(2026, 6, 2, 8, 0), created=utc(2026, 6, 2, 7, 0))
    _message(p3, Message.Status.FAILED, created=utc(2026, 6, 3, 7, 0))

    entry = WaitlistEntry.all_objects.create(
        clinic=clinic,
        patient=p1,
        appointment_type=appointment_type,
        window_start=utc(2026, 6, 1, 0, 0),
        window_end=utc(2026, 6, 30, 0, 0),
        status=WaitlistEntry.Status.ACTIVE,
    )
    offers = []
    for index, status_value in enumerate(
        (WaitlistOffer.Status.CLAIMED, WaitlistOffer.Status.EXPIRED, WaitlistOffer.Status.OFFERED)
    ):
        offer = WaitlistOffer.all_objects.create(
            clinic=clinic,
            entry=entry,
            start_at=utc(2026, 6, 8 + index, 9, 0),
            end_at=utc(2026, 6, 8 + index, 9, 30),
            provider=provider,
            expires_at=utc(2026, 6, 30, 9, 0),
            status=status_value,
        )
        WaitlistOffer.all_objects.filter(pk=offer.pk).update(
            created_at=utc(2026, 6, 5 + index, 8, 0)
        )
        offers.append(offer)

    return {
        "seed": seed,
        "clinic": clinic,
        "location": location,
        "provider": provider,
        "appointment_type": appointment_type,
        "patients": {"p1": p1, "p2": p2, "p3": p3},
        "invoice": invoice,
        "recall_type": recall_type,
        "waitlist_entry": entry,
        "offers": offers,
    }
