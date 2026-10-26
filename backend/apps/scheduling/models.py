"""Scheduling persistence models (T110).

The scheduling app is the single source of truth for what is bookable and what
is booked. Four ideas shape the schema:

1. **Rules are wall-clock, appointments are UTC.** ``AvailabilityRule`` stores a
   local ``time`` plus a weekday (and an IANA timezone); expansion happens in the
   clinic zone and converts to UTC. ``Appointment.start_at``/``end_at`` are
   ``TIMESTAMPTZ``.
2. **Double-booking is a database problem.** Two ``ExclusionConstraint`` rows
   (added in migration ``0002``) make overlapping active appointments for the
   same provider -- and, when an operatory is set, for the same operatory --
   impossible even under concurrency. The service layer maps the resulting
   ``IntegrityError`` to a 409 ``slot_conflict``.
3. **Holds expire.** ``status=held`` carries ``hold_expires_at``; the expiry job
   cancels stale holds so the slot returns to the pool.
4. **History is append-only.** ``AppointmentEvent`` refuses ``save()`` on an
   existing row and refuses ``delete()`` outright, so the only legal write is the
   insert performed by the service layer.

``Patient.primary_provider`` deliberately still points at
``clinics.Membership``: this task adds ``clinics.Provider`` but does not migrate
the patient record (that is a later, data-carrying change). ``Appointment`` and
``AvailabilityRule`` use the real ``Provider`` FK.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models
from django.db.models import F, Func, Q
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel

from . import status as statuses

# ``TSTZRANGE(start_at, end_at)`` uses the default ``[)`` bounds, so an
# appointment ending at 10:00 and the next starting at 10:00 do not overlap.
TSTZ_RANGE = Func(F("start_at"), F("end_at"), function="TSTZRANGE")


class AppointmentHistoryImmutableError(RuntimeError):
    """Raised when code attempts to update or delete an ``AppointmentEvent``.

    The event table is append-only: the only legal write is the insert made by
    the scheduling service layer. A DB-level trigger lands with the T135
    hardening pass; this exception is the application-level guard, mirroring
    ``core.errors.AuditImmutableError`` and ``charting.ChartHistoryImmutableError``.
    """


class AppointmentType(ClinicScopedModel):
    """A bookable service: duration plus the buffers around it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    duration_min = models.PositiveSmallIntegerField(default=30)
    buffer_before_min = models.PositiveSmallIntegerField(default=0)
    buffer_after_min = models.PositiveSmallIntegerField(default=0)
    requires_operatory = models.BooleanField(default=False)
    public_bookable = models.BooleanField(default=False)
    # Plain JSON list of membership role strings (never a Postgres array).
    eligible_roles = models.JSONField(default=list, blank=True)
    color = models.CharField(max_length=16, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "scheduling_appointmenttype"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "code"],
                name="uniq_appointment_type_clinic_code",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "is_active"], name="appt_type_clinic_active_idx"),
        ]
        verbose_name = "Appointment type"
        verbose_name_plural = "Appointment types"

    def __str__(self) -> str:
        return f"{self.code} ({self.duration_min}m)"


class Operatory(ClinicScopedModel):
    """A chair/room at a location; only required by some appointment types."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.CASCADE,
        related_name="operatories",
    )
    name = models.CharField(max_length=128)
    equipment_json = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "scheduling_operatory"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "location", "name"],
                name="uniq_operatory_location_name",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "location"], name="operatory_clinic_loc_idx"),
        ]
        verbose_name = "Operatory"
        verbose_name_plural = "Operatories"

    def __str__(self) -> str:
        return f"{self.name} @ {self.location_id}"


class AvailabilityRule(ClinicScopedModel):
    """A recurring weekly window (working) or a hole in one (break/block).

    ``provider`` is nullable: a rule without a provider is a clinic-wide default
    that applies to a provider only when that provider has no rule of its own for
    the same weekday/location. ``valid_from``/``valid_to`` bound the rule in
    clinic-local dates (inclusive).
    """

    class Kind(models.TextChoices):
        WORKING = "working", "Working"
        BREAK = "break", "Break"
        BLOCK = "block", "Block"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    provider = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="availability_rules",
    )
    location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.CASCADE,
        related_name="availability_rules",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.WORKING)
    # Python weekday convention: Monday=0 ... Sunday=6.
    weekday = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "scheduling_availabilityrule"
        ordering = ["weekday", "start_time", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "weekday", "kind"],
                name="avail_rule_clinic_day_kind_idx",
            ),
            models.Index(
                fields=["clinic", "location", "provider"],
                name="avail_rule_clinic_loc_prov_idx",
            ),
        ]
        verbose_name = "Availability rule"
        verbose_name_plural = "Availability rules"

    def __str__(self) -> str:
        return f"{self.kind} wd{self.weekday} {self.start_time}-{self.end_time}"


class ScheduleException(ClinicScopedModel):
    """A one-off closure or shortening of a day (holiday, leave, training)."""

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "Holiday"
        LEAVE = "leave", "Leave"
        TRAINING = "training", "Training"
        CLOSURE = "closure", "Closure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    provider = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="schedule_exceptions",
    )
    location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.CASCADE,
        related_name="schedule_exceptions",
    )
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    all_day = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.HOLIDAY)

    class Meta:
        db_table = "scheduling_scheduleexception"
        ordering = ["date", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "date"],
                name="schedule_exc_clinic_date_idx",
            ),
        ]
        verbose_name = "Schedule exception"
        verbose_name_plural = "Schedule exceptions"

    def __str__(self) -> str:
        return f"{self.kind} {self.date}"


class Appointment(ClinicScopedModel):
    """One booked (or held) slot for one patient with one provider."""

    class Source(models.TextChoices):
        STAFF = "staff", "Staff"
        ONLINE = "online", "Online"
        AI = "ai", "AI assisted"
        PHONE = "phone", "Phone"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    provider = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    operatory = models.ForeignKey(
        Operatory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
    )
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=statuses.STATUS_CHOICES,
        default=statuses.STATUS_SCHEDULED,
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.STAFF,
    )
    reason = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    cancellation_reason = models.CharField(max_length=255, blank=True, default="")
    rescheduled_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rescheduled_to",
    )
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    idempotency_key = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "scheduling_appointment"
        ordering = ["start_at", "id"]
        indexes = [
            models.Index(fields=["clinic", "start_at"], name="appt_clinic_start_idx"),
            models.Index(fields=["provider", "start_at"], name="appt_provider_start_idx"),
            models.Index(fields=["patient", "start_at"], name="appt_patient_start_idx"),
        ]
        constraints = [
            # One appointment per idempotency key inside a clinic. Blank keys
            # fall outside the constraint, so non-idempotent creates are free.
            models.UniqueConstraint(
                fields=["clinic", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="uniq_appointment_clinic_idem",
            ),
            # A provider can never be in two active appointments at once. This is
            # the DB-level guard behind the 409 slot_conflict mapping. The
            # condition leaves the time-boxed holds out (see
            # ``statuses.EXCLUSION_STATUSES``): a hold that has elapsed must not
            # keep blocking its slot.
            ExclusionConstraint(
                name="excl_appointment_provider_overlap",
                expressions=[
                    ("provider", RangeOperators.EQUAL),
                    (TSTZ_RANGE, RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=sorted(statuses.EXCLUSION_STATUSES)),
            ),
            # An operatory can never host two active appointments at once. The
            # condition keeps appointments without an operatory out of the range.
            ExclusionConstraint(
                name="excl_appointment_operatory_overlap",
                expressions=[
                    ("operatory", RangeOperators.EQUAL),
                    (TSTZ_RANGE, RangeOperators.OVERLAPS),
                ],
                condition=Q(
                    operatory__isnull=False,
                    status__in=sorted(statuses.EXCLUSION_STATUSES),
                ),
            ),
        ]
        verbose_name = "Appointment"
        verbose_name_plural = "Appointments"

    def __str__(self) -> str:
        return f"{self.patient_id} {self.start_at:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def is_active(self) -> bool:
        return statuses.is_active(self.status)


class AppointmentEvent(ClinicScopedModel):
    """Append-only lifecycle event for one appointment."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        STATUS_CHANGED = "status_changed", "Status changed"
        CANCELLED = "cancelled", "Cancelled"
        RESCHEDULED = "rescheduled", "Rescheduled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "scheduling_appointmentevent"
        ordering = ["at", "id"]
        indexes = [
            models.Index(
                fields=["appointment", "at"],
                name="appt_event_appointment_at_idx",
            ),
            models.Index(fields=["clinic", "at"], name="appt_event_clinic_at_idx"),
        ]
        verbose_name = "Appointment event"
        verbose_name_plural = "Appointment events"

    def __str__(self) -> str:
        return f"{self.event_type} ({self.appointment_id})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # ``_state.adding`` (not ``pk is None``): the pk is a UUID with a
        # default, so a brand-new instance already carries one.
        if not self._state.adding:
            raise AppointmentHistoryImmutableError(
                "AppointmentEvent rows are append-only and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppointmentHistoryImmutableError(
            "AppointmentEvent rows are append-only and cannot be deleted."
        )


class WaitlistEntry(ClinicScopedModel):
    """A patient waiting for a slot of one appointment type inside a window."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        OFFERED = "offered", "Offered"
        CLAIMED = "claimed", "Claimed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
    )
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.PROTECT,
        related_name="waitlist_entries",
    )
    provider_pref = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entries",
    )
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    urgency = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    # A redacted snapshot of the consent decision taken when the entry was made.
    consent_snapshot = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "scheduling_waitlistentry"
        ordering = ["-urgency", "window_start", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "status"],
                name="waitlist_clinic_status_idx",
            ),
            models.Index(
                fields=["clinic", "patient"],
                name="waitlist_clinic_patient_idx",
            ),
        ]
        verbose_name = "Waitlist entry"
        verbose_name_plural = "Waitlist entries"

    def __str__(self) -> str:
        return f"{self.patient_id} {self.appointment_type_id} ({self.status})"


class WaitlistOffer(ClinicScopedModel):
    """A time-boxed offer of one slot to one waitlist entry.

    The slot is blocked by a *held* appointment (``held_appointment``) so two
    offers cannot double-sell it: the application rule (a live hold conflicts,
    an elapsed one does not) rejects the second hold under the provider lock.
    Claiming promotes the hold to ``scheduled`` and records it in
    ``claimed_appointment``; declining or expiring cancels the hold and
    clears the reference.
    """

    class Status(models.TextChoices):
        OFFERED = "offered", "Offered"
        CLAIMED = "claimed", "Claimed"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    entry = models.ForeignKey(
        WaitlistEntry,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    provider = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.PROTECT,
        related_name="waitlist_offers",
    )
    operatory = models.ForeignKey(
        Operatory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_offers",
    )
    # SHA-256 of the opaque offer token (never the token itself).
    token_hash = models.CharField(max_length=64, blank=True, default="")
    score_json = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OFFERED,
    )
    held_appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_offer_hold",
    )
    claimed_appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_offer_claim",
    )

    class Meta:
        db_table = "scheduling_waitlistoffer"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["token_hash"],
                condition=~Q(token_hash=""),
                name="uniq_waitlist_offer_token_hash",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "status", "expires_at"],
                name="waitlist_offer_clinic_exp_idx",
            ),
            models.Index(fields=["entry"], name="waitlist_offer_entry_idx"),
        ]
        verbose_name = "Waitlist offer"
        verbose_name_plural = "Waitlist offers"

    def __str__(self) -> str:
        return f"offer {self.status} @ {self.start_at:%Y-%m-%d %H:%M}"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
APPOINTMENT_STATUS_CHOICES = statuses.STATUS_CHOICES
APPOINTMENT_SOURCE_CHOICES = Appointment.Source.choices
APPOINTMENT_EVENT_TYPE_CHOICES = AppointmentEvent.EventType.choices
AVAILABILITY_RULE_KIND_CHOICES = AvailabilityRule.Kind.choices
SCHEDULE_EXCEPTION_KIND_CHOICES = ScheduleException.Kind.choices
WAITLIST_STATUS_CHOICES = WaitlistEntry.Status.choices
WAITLIST_OFFER_STATUS_CHOICES = WaitlistOffer.Status.choices


__all__ = [
    "APPOINTMENT_EVENT_TYPE_CHOICES",
    "APPOINTMENT_SOURCE_CHOICES",
    "APPOINTMENT_STATUS_CHOICES",
    "AVAILABILITY_RULE_KIND_CHOICES",
    "SCHEDULE_EXCEPTION_KIND_CHOICES",
    "WAITLIST_OFFER_STATUS_CHOICES",
    "WAITLIST_STATUS_CHOICES",
    "Appointment",
    "AppointmentEvent",
    "AppointmentHistoryImmutableError",
    "AppointmentType",
    "AvailabilityRule",
    "Operatory",
    "ScheduleException",
    "WaitlistEntry",
    "WaitlistOffer",
]
