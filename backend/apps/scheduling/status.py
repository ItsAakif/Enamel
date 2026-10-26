"""Appointment status machine (T110).

The statuses, the set of *active* (slot-holding) statuses and the legal
transition graph live here rather than in ``models`` so the service layer, the
action views and the tests all read one definition. ``models`` imports the
choice list from this module; nothing else may invent a status string.

Active statuses
---------------
``requested``, ``held``, ``scheduled``, ``confirmed``, ``checked_in`` and
``in_progress`` all reserve the provider/operatory slot, so they are the statuses
covered by the PostgreSQL GiST exclusion constraints. ``completed``,
``cancelled`` and ``no_show`` release the slot.

Transitions
-----------
A transition is legal only when the target appears in ``TRANSITIONS`` for the
current status. Anything else is a 409 ``invalid_transition`` and must write
neither an ``AppointmentEvent`` nor an audit row.
"""

from __future__ import annotations

STATUS_REQUESTED = "requested"
STATUS_HELD = "held"
STATUS_SCHEDULED = "scheduled"
STATUS_CONFIRMED = "confirmed"
STATUS_CHECKED_IN = "checked_in"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_NO_SHOW = "no_show"

STATUS_CHOICES: list[tuple[str, str]] = [
    (STATUS_REQUESTED, "Requested"),
    (STATUS_HELD, "Held"),
    (STATUS_SCHEDULED, "Scheduled"),
    (STATUS_CONFIRMED, "Confirmed"),
    (STATUS_CHECKED_IN, "Checked in"),
    (STATUS_IN_PROGRESS, "In progress"),
    (STATUS_COMPLETED, "Completed"),
    (STATUS_CANCELLED, "Cancelled"),
    (STATUS_NO_SHOW, "No show"),
]

ALL_STATUSES: tuple[str, ...] = tuple(value for value, _ in STATUS_CHOICES)

#: Statuses that occupy the slot and therefore participate in the exclusion
#: constraints. Must stay in sync with ``Appointment.Meta.constraints``.
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_REQUESTED,
        STATUS_HELD,
        STATUS_SCHEDULED,
        STATUS_CONFIRMED,
        STATUS_CHECKED_IN,
        STATUS_IN_PROGRESS,
    }
)

#: The statuses the **exclusion constraints** cover.
#:
#: A ``held`` appointment is deliberately *not* one of them. A hold is
#: time-boxed (``hold_expires_at``), and a GiST predicate cannot be
#: time-aware, so the database would keep refusing a slot whose hold has
#: already elapsed - while the public availability had already offered that
#: slot again. The two rules must agree, so the constraint leaves the holds to
#: the application rule (the same rule the availability uses): a **live** hold
#: conflicts, an **elapsed** hold does not. The check-then-insert is
#: serialised by the provider lock (``_lock_provider``), so the constraint is
#: not what protects a live hold.
EXCLUSION_STATUSES: frozenset[str] = ACTIVE_STATUSES - {STATUS_HELD}

#: Statuses that release the slot for good.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_CANCELLED, STATUS_NO_SHOW}
)

TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_REQUESTED: frozenset(
        {STATUS_HELD, STATUS_SCHEDULED, STATUS_CONFIRMED, STATUS_CANCELLED, STATUS_NO_SHOW}
    ),
    STATUS_HELD: frozenset(
        {STATUS_SCHEDULED, STATUS_CONFIRMED, STATUS_CANCELLED, STATUS_NO_SHOW}
    ),
    STATUS_SCHEDULED: frozenset(
        {
            STATUS_CONFIRMED,
            STATUS_CHECKED_IN,
            STATUS_IN_PROGRESS,
            STATUS_CANCELLED,
            STATUS_NO_SHOW,
        }
    ),
    STATUS_CONFIRMED: frozenset(
        {
            STATUS_CHECKED_IN,
            STATUS_IN_PROGRESS,
            STATUS_CANCELLED,
            STATUS_NO_SHOW,
        }
    ),
    STATUS_CHECKED_IN: frozenset(
        {STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_CANCELLED, STATUS_NO_SHOW}
    ),
    STATUS_IN_PROGRESS: frozenset({STATUS_COMPLETED, STATUS_CANCELLED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_CANCELLED: frozenset(),
    STATUS_NO_SHOW: frozenset(),
}

#: Action sub-resource -> target status (``POST /appointments/{id}/confirm/``).
ACTION_TARGETS: dict[str, str] = {
    "confirm": STATUS_CONFIRMED,
    "check-in": STATUS_CHECKED_IN,
    "start": STATUS_IN_PROGRESS,
    "complete": STATUS_COMPLETED,
    "cancel": STATUS_CANCELLED,
    "no-show": STATUS_NO_SHOW,
}

#: Default hold lifetime for ``status=held`` (the public booking hold is 10 min).
DEFAULT_HOLD_MINUTES = 10


def can_transition(current: str, target: str) -> bool:
    """Return ``True`` when ``current -> target`` is a legal transition."""

    return target in TRANSITIONS.get(current, frozenset())


def is_active(status: str) -> bool:
    return status in ACTIVE_STATUSES


__all__ = [
    "ACTION_TARGETS",
    "ACTIVE_STATUSES",
    "ALL_STATUSES",
    "DEFAULT_HOLD_MINUTES",
    "EXCLUSION_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_CHECKED_IN",
    "STATUS_CHOICES",
    "STATUS_COMPLETED",
    "STATUS_CONFIRMED",
    "STATUS_HELD",
    "STATUS_IN_PROGRESS",
    "STATUS_NO_SHOW",
    "STATUS_REQUESTED",
    "STATUS_SCHEDULED",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "can_transition",
    "is_active",
]
