"""Idempotent demo seed: two clinics, four roles, two locations.

Running the command twice creates nothing. Credentials are printed only for
users created during the current run.

The seed also creates the patient-portal demo account (Demo Patient) in
``enamel-central`` with the documented contacts ``patient@enamel.demo`` /
``+919800000001`` plus one real appointment and one issued invoice. The portal
authenticates with a one-time code; the code is only revealed in the API
response (``demo_code``) when the deployment runs with ``DEMO_MODE``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.clinics.models import (
    Clinic,
    Location,
    Membership,
    Provider,
    default_location_name,
)
from apps.clinics.services.members import create_default_location
from apps.core.rls import bypass_rls, require_bypass_capable
from apps.core.tenancy import with_clinic

DEMO_PASSWORD = "EnamelDemo!2026"

CLINICS: tuple[dict[str, Any], ...] = (
    {
        "slug": "enamel-central",
        "name": "Enamel Central",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "country": "IN",
    },
    {
        "slug": "enamel-north",
        "name": "Enamel North",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "country": "IN",
    },
)

USERS: tuple[dict[str, str], ...] = (
    {
        "email": "owner@enamel.demo",
        "full_name": "Olivia Owner",
        "role": Membership.Role.OWNER,
        "clinic": "enamel-central",
    },
    {
        "email": "dentist@enamel.demo",
        "full_name": "Dev Dentist",
        "role": Membership.Role.DENTIST,
        "clinic": "enamel-central",
    },
    {
        "email": "reception@enamel.demo",
        "full_name": "Riya Receptionist",
        "role": Membership.Role.RECEPTIONIST,
        "clinic": "enamel-central",
    },
    {
        "email": "assistant@enamel.demo",
        "full_name": "Ari Assistant",
        "role": Membership.Role.ASSISTANT,
        "clinic": "enamel-central",
    },
)

# Bookable clinicians (T112 provider CRUD). Each entry links an existing demo
# membership to a ``clinics.Provider`` so the schedule has real providers to
# book. No new users or memberships are created here: the demo membership set is
# pinned by ``test_seed_demo_is_idempotent``.
PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "clinic": "enamel-central",
        "email": "dentist@enamel.demo",
        "display_name": "Dr Dev Dentist",
        "specialty": "General dentistry",
        "color": "#2563eb",
    },
    {
        "clinic": "enamel-central",
        "email": "owner@enamel.demo",
        "display_name": "Dr Olivia Owner",
        "specialty": "Orthodontics",
        "color": "#059669",
    },
    {
        "clinic": "enamel-north",
        "email": "owner@enamel.demo",
        "display_name": "Dr Olivia Owner",
        "specialty": "Orthodontics",
        "color": "#059669",
    },
)


#: The opening hours of the demo clinics: Monday to Saturday, 09:00-18:00.
#: Without them the availability engine has no working window, and a bookable
#: slot carries no provider lane - the schedule would be empty.
DEMO_WORKING_HOURS: tuple[tuple[int, str, str], ...] = tuple(
    (weekday, "09:00", "18:00") for weekday in range(6)
)


#: The bookable services of the demo clinics. Without an appointment type the
#: schedule has nothing to book.
APPOINTMENT_TYPES: tuple[dict[str, Any], ...] = (
    {
        "code": "exam",
        "name": "Exam and consultation",
        "duration_min": 30,
        "public_bookable": True,
        "color": "#2563eb",
    },
    {
        "code": "cleaning",
        "name": "Cleaning and polish",
        "duration_min": 45,
        "public_bookable": True,
        "color": "#059669",
    },
)


#: The patient-portal demo account. The hosted demo (and any ``DEMO_MODE``
#: deployment) documents this contact: the visitor enters the clinic
#: ``enamel-central`` plus one of the two contacts, requests a one-time code
#: and - because the server runs with DEMO_MODE - the response carries the
#: code in the ``demo_code`` field. There is no portal password: the portal
#: authenticates with a one-time code only.
DEMO_PORTAL_PATIENT: dict[str, str] = {
    "clinic": "enamel-central",
    "email": "patient@enamel.demo",
    "phone": "+919800000001",
    "first_name": "Demo",
    "last_name": "Patient",
}

def _seed_demo_portal_patient(clinic: Clinic, location: Location) -> int:
    """Create the demo portal patient; return the number of records made.

    Idempotent: the patient is created at most once and the documented
    contacts are kept in sync on every run.
    """

    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number

    spec = DEMO_PORTAL_PATIENT
    patient = Patient.all_objects.filter(clinic=clinic, email=spec["email"]).first()
    if patient is None:
        patient = Patient.all_objects.create(
            clinic=clinic,
            number=allocate_patient_number(clinic),
            first_name=spec["first_name"],
            last_name=spec["last_name"],
            email=spec["email"],
            phone_e164=spec["phone"],
            preferred_contact=Patient.PreferredContact.EMAIL,
            primary_location=location,
            source=Patient.Source.STAFF,
            notes=(
                "Demo account for the public patient-portal walkthrough. "
                "Documented in docs/USER_GUIDE.md."
            ),
        )
        return 1

    # Keep the documented contacts authoritative even if the row was edited.
    updates: list[str] = []
    if patient.phone_e164 != spec["phone"]:
        patient.phone_e164 = spec["phone"]
        updates.append("phone_e164")
    if patient.first_name != spec["first_name"] or patient.last_name != spec["last_name"]:
        patient.first_name = spec["first_name"]
        patient.last_name = spec["last_name"]
        updates.extend(["first_name", "last_name"])
    if updates:
        patient.save(update_fields=updates + ["updated_at"])
    return 0


def _seed_demo_portal_bookings(clinic: Clinic, patient: Any) -> dict[str, int]:
    """Book a realistic appointment and issue an invoice for the demo patient.

    Both are created at most once (the visit is only possible if the patient
    has an appointment and an invoice on file), so the seed stays idempotent.
    """

    from datetime import timedelta

    from apps.billing.services import invoices as invoice_service
    from apps.scheduling.models import Appointment, AppointmentType
    from apps.scheduling.services.appointments import create_appointment, find_conflict

    created = {"appointment": 0, "invoice": 0}
    location = Location.all_objects.filter(clinic=clinic).order_by("id").first()
    provider = Provider.all_objects.filter(
        clinic=clinic, display_name="Dr Dev Dentist"
    ).first()
    exam = AppointmentType.all_objects.filter(clinic=clinic, code="exam").first()
    if location is None or provider is None or exam is None:
        return created

    if not Appointment.all_objects.filter(clinic=clinic, patient=patient).exists():
        created_appointment = None
        # Claim a genuinely free slot: start at 10:00 three days out and step
        # day by day (the same conflict rule the staff calendar and the public
        # booking flow apply) until the slot is free. The demo seed books the
        # same provider elsewhere, so a fixed timestamp could collide with an
        # existing demo booking on a long-lived database.
        for day_offset in range(3, 18):
            start = timezone.localtime(timezone.now() + timedelta(days=day_offset))
            start = start.replace(hour=10, minute=0, second=0, microsecond=0)
            end = start + timedelta(minutes=exam.duration_min)
            if (
                find_conflict(
                    clinic,
                    provider_id=provider.pk,
                    operatory_id=None,
                    start_at=start,
                    end_at=end,
                )
                is None
            ):
                created_appointment = create_appointment(
                    clinic,
                    patient=patient,
                    provider=provider,
                    appointment_type=exam,
                    location=location,
                    start_at=start,
                    source=Appointment.Source.STAFF,
                    reason="Exam and consultation",
                    notes="Demo appointment for the patient-portal walkthrough.",
                )
                break
        if created_appointment is not None:
            created["appointment"] = 1

    from apps.billing.models import Invoice

    if not Invoice.all_objects.filter(clinic=clinic, patient=patient).exists():
        invoice_service.create_invoice(
            clinic,
            patient=patient,
            lines=[
                {
                    "description": "Exam and consultation",
                    "procedure_code": "EXAM",
                    "quantity": 1,
                    "unit_price_minor": 90000,
                }
            ],
            status=Invoice.Status.ISSUED,
            notes="Demo invoice for the patient-portal walkthrough.",
        )
        created["invoice"] = 1
    return created


def _seed_clinic_templates(clinic: Clinic) -> int:
    """Create the default consent and message templates of ``clinic``."""

    from apps.messaging.seed import ensure_default_templates
    from apps.plans.seed import ensure_default_consent_templates

    with with_clinic(clinic):
        created = len(ensure_default_consent_templates(clinic))
        created += len(ensure_default_templates(clinic))
    return created


def _seed_appointment_types(clinic: Clinic) -> int:
    """Create the demo appointment types of ``clinic``; return the count."""

    from apps.scheduling.models import AppointmentType

    created = 0
    with with_clinic(clinic):
        for spec in APPOINTMENT_TYPES:
            _type, was_created = AppointmentType.all_objects.get_or_create(
                clinic=clinic,
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "duration_min": spec["duration_min"],
                    "public_bookable": spec["public_bookable"],
                    "color": spec["color"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
    return created


def _seed_working_hours(clinic: Clinic, location: Location) -> int:
    """Create the demo opening hours of ``clinic``; return the count."""

    from datetime import time as time_type

    from apps.scheduling.models import AvailabilityRule

    created = 0
    with with_clinic(clinic):
        for weekday, start, end in DEMO_WORKING_HOURS:
            _rule, was_created = AvailabilityRule.all_objects.get_or_create(
                clinic=clinic,
                location=location,
                kind=AvailabilityRule.Kind.WORKING,
                weekday=weekday,
                defaults={
                    "start_time": time_type(*(int(part) for part in start.split(":"))),
                    "end_time": time_type(*(int(part) for part in end.split(":"))),
                },
            )
            if was_created:
                created += 1
    return created


class Command(BaseCommand):
    help = "Seed idempotent demo clinics, locations, users and memberships."

    @transaction.atomic
    @bypass_rls()
    def handle(self, *args: Any, **options: Any) -> None:
        require_bypass_capable("seed_demo")
        clinics: dict[str, Clinic] = {}
        created_clinics = 0
        created_locations = 0
        created_working_hours = 0
        created_appointment_types = 0

        for spec in CLINICS:
            clinic, created = Clinic.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "timezone": spec["timezone"],
                    "currency": spec["currency"],
                    "country": spec["country"],
                },
            )
            clinics[clinic.slug] = clinic
            if created:
                created_clinics += 1
            location = Location.all_objects.filter(
                clinic=clinic, name=default_location_name()
            ).first()
            if location is None:
                location = create_default_location(clinic)
                created_locations += 1
            created_working_hours += _seed_working_hours(clinic, location)
            created_appointment_types += _seed_appointment_types(clinic)
            _seed_clinic_templates(clinic)

        created_users: list[User] = []
        for spec in USERS:
            user = User.objects.filter(email=spec["email"]).first()
            if user is None:
                user = User.objects.create_user(
                    email=spec["email"],
                    password=DEMO_PASSWORD,
                    full_name=spec["full_name"],
                )
                user.password_changed_at = timezone.now()
                user.save(update_fields=["password_changed_at"])
                created_users.append(user)

            clinic = clinics[spec["clinic"]]
            Membership.all_objects.get_or_create(
                clinic=clinic,
                user=user,
                defaults={
                    "role": spec["role"],
                    "status": Membership.Status.ACTIVE,
                    "accepted_at": timezone.now(),
                },
            )

        # The owner also belongs to the second clinic, proving header-based
        # multi-clinic switching in the demo.
        owner = User.objects.get(email="owner@enamel.demo")
        north = clinics["enamel-north"]
        Membership.all_objects.get_or_create(
            clinic=north,
            user=owner,
            defaults={
                "role": Membership.Role.OWNER,
                "status": Membership.Status.ACTIVE,
                "accepted_at": timezone.now(),
            },
        )

        created_providers = 0
        for spec in PROVIDERS:
            clinic = clinics[spec["clinic"]]
            user = User.objects.get(email=spec["email"])
            membership = Membership.all_objects.get(clinic=clinic, user=user)
            _, created = Provider.all_objects.get_or_create(
                clinic=clinic,
                membership=membership,
                defaults={
                    "display_name": spec["display_name"],
                    "specialty": spec["specialty"],
                    "color": spec["color"],
                    "is_bookable": True,
                },
            )
            if created:
                created_providers += 1

        # The patient-portal demo account, its appointment and its invoice
        # (documented contact: patient@enamel.demo / +919800000001 in
        # enamel-central). The visitor's one-time code is revealed in the API
        # response only when the deployment runs with DEMO_MODE.
        central = clinics["enamel-central"]
        central_location = Location.all_objects.filter(clinic=central).order_by("id").first()
        created_portal_patient = 0
        if central_location is None:
            raise RuntimeError("seed_demo: the default location of enamel-central is missing")
        created_portal_patient = _seed_demo_portal_patient(central, central_location)
        portal_patient = None
        from apps.patients.models import Patient

        portal_patient = Patient.all_objects.filter(
            clinic=central, email=DEMO_PORTAL_PATIENT["email"]
        ).first()
        created_portal_bookings = _seed_demo_portal_bookings(central, portal_patient) if portal_patient else {"appointment": 0, "invoice": 0}

        self.stdout.write(
            f"Clinics created: {created_clinics}; locations created: {created_locations}; "
            f"Providers created: {created_providers}; appointment types: {created_appointment_types}; working hours: {created_working_hours}."
        )
        self.stdout.write(
            f"Demo portal patient created: {created_portal_patient}; "
            f"demo appointment created: {created_portal_bookings['appointment']}; "
            f"demo invoice created: {created_portal_bookings['invoice']}."
        )
        self.stdout.write(
            "Demo portal contact: patient@enamel.demo or +919800000001 "
            f"(clinic {DEMO_PORTAL_PATIENT['clinic']}); see docs/USER_GUIDE.md."
        )
        if created_users:
            self.stdout.write("Created demo users (password shown once):")
            for user in created_users:
                self.stdout.write(f"  {user.email} / {DEMO_PASSWORD}")
        else:
            self.stdout.write("No new demo users created.")
