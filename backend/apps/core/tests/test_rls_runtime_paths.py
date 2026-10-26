"""The runtime paths that the role switch made visible (ADR-013, T137).

Two paths run under the shipped runtime roles and were therefore never proven
before the switch:

* the **scheduled sweepers** (``booking.tasks.expire_holds`` and
  ``scheduling.tasks.expire_waitlist_offers``) run as ``enamel_worker``, which
  holds **no** bypass. They must scope their work per clinic instead of
  privileged, and an expired hold must not keep occupying its slot;
* the **invitation accept** path resolves its row through a SELECT-scoped
  capability policy, and PostgreSQL applies the UPDATE policies to a locking
  clause. The path must therefore resolve by capability first and lock inside
  the clinic context.

Every proof in this module runs under the **real runtime role**
(``enamel_worker`` / ``enamel_app``) and asserts the role it ran under.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.core.tenancy import with_clinic

pytestmark = [pytest.mark.django_db, pytest.mark.rls]

APP_ROLE = "enamel_app"
PORTAL_ROLE = "enamel_portal"
WORKER_ROLE = "enamel_worker"

#: The boundary proofs need a connection that may ``SET ROLE`` to the runtime
#: roles; the shared fixture reconnects it with the owner's credentials when
#: the current connection may not.
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: E402,F401


@contextmanager
def as_role(role: str):
    """Run the block as ``role`` and assert that the switch took effect."""

    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        current = cursor.fetchone()[0]
    assert current == role, f"the proof did not run as {role} (it ran as {current})"
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
        except Exception:  # pragma: no cover - transaction already aborted
            pass


def _slot(days: int = 7, hour: int = 9, minute: int = 0):
    """A UTC instant inside the clinic's working window (09:00 local)."""

    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    kolkata = ZoneInfo("Asia/Kolkata")
    target = timezone.localdate() + timedelta(days=days)
    naive = datetime.combine(target, datetime.min.time().replace(hour=hour, minute=minute))
    return naive.replace(tzinfo=kolkata).astimezone(UTC)


def _bookable_clinic(clinic_factory, user_factory, membership_factory, slug: str):
    """A clinic with a location, a provider, a public type and opening hours."""

    from datetime import time

    from apps.clinics.models import Location, Provider
    from apps.scheduling.models import AppointmentType, AvailabilityRule

    clinic = clinic_factory(slug=slug)
    location = Location.all_objects.create(clinic=clinic, name="Main")
    for weekday in range(7):
        AvailabilityRule.all_objects.create(
            clinic=clinic,
            location=location,
            kind=AvailabilityRule.Kind.WORKING,
            weekday=weekday,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
    user = user_factory(f"staff-{slug}@enamel.test")
    membership = membership_factory(clinic, user, role="dentist")
    provider = Provider.all_objects.create(
        clinic=clinic,
        membership=membership,
        display_name="Dr Sweep",
        specialty="General",
        color="#123456",
        is_bookable=True,
    )
    appointment_type = AppointmentType.all_objects.create(
        clinic=clinic,
        code="checkup",
        name="Checkup",
        duration_min=30,
        public_bookable=True,
    )
    return {
        "clinic": clinic,
        "location": location,
        "provider": provider,
        "appointment_type": appointment_type,
        "user": user,
    }


def _hold_for(seed, slot):
    """A held appointment with a hold row (the public-booking state)."""

    from apps.booking.models import ReservationHold
    from apps.booking.services.holds import create_hold
    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number

    patient = Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Held",
        last_name="Patient",
        date_of_birth="1990-01-01",
        phone_e164="+919876500003",
    )
    hold = create_hold(
        seed["clinic"],
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        location=seed["location"],
        patient=patient,
        slot_start=slot,
        phone_e164=patient.phone_e164,
    )
    return hold, ReservationHold


# --- F-01: the sweepers under the worker role --------------------------------


def test_the_hold_sweeper_releases_an_expired_hold_under_the_worker_role(
    clinic_factory, user_factory, membership_factory
):
    """The sweeper works as ``enamel_worker`` - it holds no bypass."""

    from apps.booking.services.slots import public_slots
    from apps.booking.tasks import expire_holds

    seed = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-hold"
    )
    slot = _slot()
    hold, _holds = _hold_for(seed, slot)

    # The hold is alive: it occupies the slot.
    day = slot.astimezone(timezone.get_current_timezone()).date()
    with with_clinic(seed["clinic"]):
        blocked = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert all(item["start"] != slot for item in blocked["slots"])

    # It expires.
    with with_clinic(seed["clinic"]):
        hold.expires_at = timezone.now() - timedelta(minutes=1)
        hold.save(update_fields=["expires_at"])
        from apps.scheduling.models import Appointment

        Appointment.all_objects.filter(pk=hold.appointment_id).update(
            hold_expires_at=timezone.now() - timedelta(minutes=1)
        )

    with as_role(WORKER_ROLE):
        result = expire_holds()

    assert result["released"] >= 1
    assert result["expired_holds"] >= 1

    from apps.scheduling.models import Appointment

    with with_clinic(seed["clinic"]):
        hold.refresh_from_db()
        assert hold.status == "expired"
        appt = Appointment.all_objects.get(pk=hold.appointment_id)
        assert appt.status == "cancelled", appt.status
        # The expired hold does not occupy the slot any more.
        freed = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert any(item["start"] == slot for item in freed["slots"])


def test_an_unexpired_hold_still_blocks_the_slot(
    clinic_factory, user_factory, membership_factory
):
    """The availability rule only releases a hold whose expiry has passed."""

    from apps.booking.services.slots import public_slots

    seed = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-live"
    )
    slot = _slot()
    _hold_for(seed, slot)

    day = slot.astimezone(timezone.get_current_timezone()).date()
    with with_clinic(seed["clinic"]):
        blocked = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert all(item["start"] != slot for item in blocked["slots"])


def test_the_sweeper_leaves_a_hold_of_another_clinic_untouched(
    clinic_factory, user_factory, membership_factory
):
    """The sweeper is scoped: it releases the holds of every clinic it visits."""

    from apps.booking.tasks import expire_holds

    first = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-first"
    )
    second = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-second"
    )
    first_hold, _ = _hold_for(first, _slot(days=7))
    second_hold, _ = _hold_for(second, _slot(days=7))

    with with_clinic(first["clinic"]):
        first_hold.expires_at = timezone.now() - timedelta(minutes=1)
        first_hold.save(update_fields=["expires_at"])
    with with_clinic(second["clinic"]):
        # The second clinic's hold is still alive.
        second_hold.expires_at = timezone.now() + timedelta(hours=1)
        second_hold.save(update_fields=["expires_at"])
    from apps.scheduling.models import Appointment

    with with_clinic(first["clinic"]):
        Appointment.all_objects.filter(pk=first_hold.appointment_id).update(
            hold_expires_at=timezone.now() - timedelta(minutes=1)
        )

    with as_role(WORKER_ROLE):
        result = expire_holds()

    assert result["released"] >= 1
    with with_clinic(first["clinic"]):
        first_hold.refresh_from_db()
        assert first_hold.status == "expired"
    with with_clinic(second["clinic"]):
        second_hold.refresh_from_db()
        assert second_hold.status == "active"


def _waitlist_offer_for(seed, slot, *, expires_at):
    """A waitlist offer whose slot is held and whose TTL has elapsed."""

    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number
    from apps.scheduling.models import Appointment, WaitlistEntry, WaitlistOffer

    clinic = seed["clinic"]
    patient = Patient.all_objects.create(
        clinic=clinic,
        number=allocate_patient_number(clinic),
        first_name="Waiting",
        last_name="Patient",
        date_of_birth="1991-02-03",
        phone_e164="+919876500004",
    )
    entry = WaitlistEntry.all_objects.create(
        clinic=clinic,
        patient=patient,
        appointment_type=seed["appointment_type"],
        provider_pref=seed["provider"],
        window_start=slot,
        window_end=slot + timedelta(hours=1),
        status=WaitlistEntry.Status.OFFERED,
    )
    held = Appointment.all_objects.create(
        clinic=clinic,
        location=seed["location"],
        patient=patient,
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        start_at=slot,
        end_at=slot + timedelta(minutes=30),
        status="held",
        source="staff",
        hold_expires_at=expires_at,
    )
    offer = WaitlistOffer.all_objects.create(
        clinic=clinic,
        entry=entry,
        start_at=slot,
        end_at=slot + timedelta(minutes=30),
        provider=seed["provider"],
        token_hash="",
        expires_at=expires_at,
        status=WaitlistOffer.Status.OFFERED,
        held_appointment=held,
    )
    return offer, held, entry


def test_the_waitlist_sweeper_expires_offers_under_the_worker_role(
    clinic_factory, user_factory, membership_factory
):
    """The waitlist sweeper really expires an offer under ``enamel_worker``."""

    from apps.scheduling.models import WaitlistEntry, WaitlistOffer
    from apps.scheduling.tasks import expire_waitlist_offers

    seed = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-waitlist"
    )
    slot = _slot()
    elapsed = timezone.now() - timedelta(minutes=1)
    offer, held, entry = _waitlist_offer_for(seed, slot, expires_at=elapsed)

    result = None
    with as_role(WORKER_ROLE):
        result = expire_waitlist_offers()

    assert result["clinics"] >= 1
    assert result["expired"] >= 1
    assert result["errors"] == 0

    with with_clinic(seed["clinic"]):
        offer.refresh_from_db()
        assert offer.status == WaitlistOffer.Status.EXPIRED
        # The slot the offer held is released with it.
        held.refresh_from_db()
        assert held.status == "cancelled"
        # The entry returns to the pool.
        entry.refresh_from_db()
        assert entry.status == WaitlistEntry.Status.ACTIVE


def test_the_sweeper_isolates_a_failing_clinic(
    clinic_factory, user_factory, membership_factory, monkeypatch
):
    """One broken clinic must not stop the sweep of the others (N-07)."""

    import apps.booking.tasks as booking_tasks
    from apps.booking.tasks import expire_holds

    broken = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-broken"
    )
    healthy = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "sweep-healthy"
    )
    broken_hold, _ = _hold_for(broken, _slot(days=7))
    healthy_hold, _ = _hold_for(healthy, _slot(days=8))
    for seed, hold in ((broken, broken_hold), (healthy, healthy_hold)):
        with with_clinic(seed["clinic"]):
            hold.expires_at = timezone.now() - timedelta(minutes=1)
            hold.save(update_fields=["expires_at"])
            from apps.scheduling.models import Appointment

            Appointment.all_objects.filter(pk=hold.appointment_id).update(
                hold_expires_at=timezone.now() - timedelta(minutes=1)
            )

    original = booking_tasks.expire_holds_service

    def failing(*, now=None, clinic=None):
        if clinic is not None and clinic.id == broken["clinic"].id:
            raise RuntimeError("the clinic data is inconsistent")
        return original(now=now, clinic=clinic)

    monkeypatch.setattr(booking_tasks, "expire_holds_service", failing)

    result = None
    with as_role(WORKER_ROLE):
        result = expire_holds()

    # The failure is recorded, and the healthy clinic was still swept.
    assert result["errors"] == 1
    assert result["clinics"] >= 2
    assert result["expired_holds"] >= 1
    with with_clinic(healthy["clinic"]):
        healthy_hold.refresh_from_db()
        assert healthy_hold.status == "expired"
    with with_clinic(broken["clinic"]):
        broken_hold.refresh_from_db()
        assert broken_hold.status == "active"


# --- N-02: the availability and the booking agree about an elapsed hold -------


def test_an_elapsed_hold_does_not_conflict_under_the_worker_role(
    clinic_factory, user_factory, membership_factory
):
    """The booking rule is the rule the availability uses (N-02)."""

    from apps.scheduling.services.appointments import create_appointment, find_conflict

    seed = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "conflict-elapsed"
    )
    slot = _slot()
    hold, _holds = _hold_for(seed, slot)
    with with_clinic(seed["clinic"]):
        hold.expires_at = timezone.now() - timedelta(minutes=1)
        hold.save(update_fields=["expires_at"])
        from apps.scheduling.models import Appointment

        Appointment.all_objects.filter(pk=hold.appointment_id).update(
            hold_expires_at=timezone.now() - timedelta(minutes=1)
        )

    with as_role(WORKER_ROLE):
        with with_clinic(seed["clinic"]):
            conflict = find_conflict(
                seed["clinic"],
                provider_id=seed["provider"].pk,
                operatory_id=None,
                start_at=slot,
                end_at=slot + timedelta(minutes=30),
            )
            assert conflict is None, conflict

            # The slot can be booked again: the elapsed hold is not a conflict.
            booked = create_appointment(
                seed["clinic"],
                patient=hold.appointment.patient,
                provider=seed["provider"],
                appointment_type=seed["appointment_type"],
                location=seed["location"],
                start_at=slot,
            )
    assert booked.status == "scheduled"


def test_a_live_hold_still_conflicts_under_the_worker_role(
    clinic_factory, user_factory, membership_factory
):
    """A hold that is still alive keeps blocking its slot (N-02)."""

    from apps.core.errors import ApiError
    from apps.scheduling.services.appointments import create_appointment, find_conflict

    seed = _bookable_clinic(
        clinic_factory, user_factory, membership_factory, "conflict-live"
    )
    slot = _slot()
    hold, _holds = _hold_for(seed, slot)

    with as_role(WORKER_ROLE):
        with with_clinic(seed["clinic"]):
            conflict = find_conflict(
                seed["clinic"],
                provider_id=seed["provider"].pk,
                operatory_id=None,
                start_at=slot,
                end_at=slot + timedelta(minutes=30),
            )
            assert conflict is not None
            assert conflict.status == "held"

            with pytest.raises(ApiError) as excinfo:
                create_appointment(
                    seed["clinic"],
                    patient=hold.appointment.patient,
                    provider=seed["provider"],
                    appointment_type=seed["appointment_type"],
                    location=seed["location"],
                    start_at=slot,
                )
    assert excinfo.value.code == "slot_conflict"
    assert excinfo.value.status_code == 409


# --- F-02: the invitation accept under the runtime role ----------------------


def _invitation_for(clinic, invited_email: str, owner, role: str = "dentist"):
    import secrets

    from apps.accounts.services.password import hash_token
    from apps.clinics.models import Invitation

    raw_token = secrets.token_urlsafe(24)
    invitation = Invitation.objects.create(
        clinic=clinic,
        email=invited_email,
        role=role,
        token_hash=hash_token(raw_token),
        expires_at=timezone.now() + timedelta(days=3),
        created_by=owner,
    )
    return invitation, raw_token


def test_the_invitation_accept_works_under_the_runtime_role(
    api_client, clinic_factory, user_factory, membership_factory
):
    """A valid token creates the membership under ``enamel_app``."""

    from apps.clinics.models import Invitation, Membership

    clinic = clinic_factory(slug="invite-runtime")
    owner = user_factory("invite-owner@enamel.test")
    membership_factory(clinic, owner, role="owner")
    invited = user_factory("invited-dentist@enamel.test")
    invitation, raw_token = _invitation_for(clinic, invited.email, owner)

    api_client.force_login(invited)
    with as_role(APP_ROLE):
        response = api_client.post(
            f"/api/v1/invitations/{raw_token}/accept/",
            {},
            format="json",
        )

    assert response.status_code in (200, 201), response.content
    with with_clinic(clinic):
        assert Membership.all_objects.filter(
            clinic_id=clinic.id, user_id=invited.id
        ).exists()
        refreshed = Invitation.objects.get(pk=invitation.pk)
        assert refreshed.accepted_at is not None


def test_the_invitation_accept_refuses_a_wrong_email_under_the_runtime_role(
    api_client, clinic_factory, user_factory, membership_factory
):
    """The 403 of a wrong email survives the role switch."""

    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="invite-wrong-email")
    owner = user_factory("invite-owner2@enamel.test")
    membership_factory(clinic, owner, role="owner")
    other = user_factory("someone-else@enamel.test")
    # The invitation is addressed to a third address, so accepting it as
    # ``other`` is the email mismatch.
    _invitation, raw_token = _invitation_for(
        clinic, "addressed-to-someone-else@enamel.test", owner
    )

    api_client.force_login(other)
    with as_role(APP_ROLE):
        response = api_client.post(
            f"/api/v1/invitations/{raw_token}/accept/",
            {},
            format="json",
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invitation_email_mismatch"
    with with_clinic(clinic):
        assert not Membership.all_objects.filter(
            clinic_id=clinic.id, user_id=other.id
        ).exists()


def test_the_invitation_accept_refuses_a_guessed_token_under_the_runtime_role(
    api_client, clinic_factory, user_factory, membership_factory
):
    """A guessed or tampered token creates nothing."""

    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="invite-guessed")
    owner = user_factory("invite-owner3@enamel.test")
    membership_factory(clinic, owner, role="owner")
    invited = user_factory("invited-guess@enamel.test")
    _invitation, raw_token = _invitation_for(clinic, invited.email, owner)
    tampered = raw_token[:-1] + ("x" if raw_token[-1] != "x" else "y")

    api_client.force_login(invited)
    with as_role(APP_ROLE):
        response = api_client.post(
            f"/api/v1/invitations/{tampered}/accept/",
            {},
            format="json",
        )

    assert response.status_code == 404
    with with_clinic(clinic):
        assert not Membership.all_objects.filter(
            clinic_id=clinic.id, user_id=invited.id
        ).exists()


def test_the_audit_helper_refuses_another_clinics_chain(
    clinic_factory, user_factory, membership_factory
):
    """The audit-chain helper only reads the caller's own clinic (F-05)."""

    from django.db import DatabaseError, transaction

    from apps.core.audit import previous_hash

    clinic = clinic_factory(slug="audit-helper-own")
    other = clinic_factory(slug="audit-helper-other")
    user = user_factory("audit-helper@enamel.test")
    membership_factory(clinic, user, role="owner")

    # The staff runtime may read the chain of the clinic it is working in...
    with as_role(APP_ROLE):
        with with_clinic(clinic):
            assert previous_hash(clinic.id) == ""

    # ...and the platform chain, which is cross-clinic by nature.
    with as_role(APP_ROLE):
        with connection.cursor() as cursor:
            cursor.execute("SELECT enamel_audit_prev_hash(NULL)")
            assert cursor.fetchone()[0] == ""

    # A patient principal may not ask for another clinic's chain. The
    # expectation wraps the savepoint, so the refused statement rolls the
    # savepoint back instead of poisoning the transaction.
    with as_role(PORTAL_ROLE):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT enamel_audit_prev_hash(%s)", [str(other.id)]
                    )

    # A patient principal may not ask for the platform chain either.
    with as_role(PORTAL_ROLE):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT enamel_audit_prev_hash(NULL)")

    # Nor may a staff principal of another clinic.
    with as_role(APP_ROLE):
        with with_clinic(other):
            with pytest.raises(DatabaseError):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT enamel_audit_prev_hash(%s)", [str(clinic.id)]
                        )

    # The legitimate caller of the same helper still gets its own chain.
    with as_role(APP_ROLE):
        with with_clinic(clinic):
            assert previous_hash(clinic.id) == ""


def test_the_audit_helper_is_not_executable_by_the_public(
    clinic_factory, user_factory, membership_factory
):
    """``PUBLIC`` holds no execute privilege on the audit-chain helper."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'enamel_audit_prev_hash(uuid)', 'EXECUTE')"
        )
        assert cursor.fetchone()[0] is False


def test_the_invitation_accept_refuses_a_replay_under_the_runtime_role(
    api_client, clinic_factory, user_factory, membership_factory
):
    """A second accept of the same token is refused, not duplicated."""

    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="invite-replay")
    owner = user_factory("invite-owner4@enamel.test")
    membership_factory(clinic, owner, role="owner")
    invited = user_factory("invited-replay@enamel.test")
    _invitation, raw_token = _invitation_for(clinic, invited.email, owner)

    api_client.force_login(invited)
    with as_role(APP_ROLE):
        first = api_client.post(
            f"/api/v1/invitations/{raw_token}/accept/",
            {},
            format="json",
        )
        second = api_client.post(
            f"/api/v1/invitations/{raw_token}/accept/",
            {},
            format="json",
        )

    assert first.status_code in (200, 201), first.content
    assert second.status_code in (409, 404), second.content
    with with_clinic(clinic):
        assert (
            Membership.all_objects.filter(
                clinic_id=clinic.id, user_id=invited.id
            ).count()
            == 1
        )
