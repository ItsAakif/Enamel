"""Take the time-boxed holds out of the exclusion constraints (N-02).

The public availability ignores a hold whose ``hold_expires_at`` has passed -
the slot is back in the pool - while the booking path and the GiST exclusion
constraints still counted it. For the window between the expiry and the
sweeper's cancellation (up to one minute) the slot was therefore advertised
and then refused.

The database cannot express the rule: a GiST predicate may not use
``now()``, so the constraint cannot tell a live hold from an elapsed one. The
two rules must agree, so the constraint now covers the same statuses the
availability treats as slot-holding, minus the holds
(``scheduling.status.EXCLUSION_STATUSES``), and the **application** decides
for the holds:

* a **live** hold still conflicts - ``find_conflict`` applies the expiry rule
  and the check-then-insert is serialised by the provider lock;
* an **elapsed** hold does not conflict - the slot is bookable again, exactly
  as the availability says.

The reverse restores the previous constraints (holds included).
"""

from __future__ import annotations

import django.contrib.postgres.constraints
from django.db import migrations, models
from django.db.models import F, Func, Q

TSTZ_RANGE = Func(F("start_at"), F("end_at"), function="TSTZRANGE")

#: The slot-holding statuses **without** the time-boxed holds.
EXCLUSION_STATUSES = [
    "checked_in",
    "confirmed",
    "in_progress",
    "requested",
    "scheduled",
]

#: The slot-holding statuses of the previous inventory (holds included).
PREVIOUS_STATUSES = sorted(EXCLUSION_STATUSES + ["held"])


def _expressions(key: str) -> list:
    return [
        (key, "="),
        (TSTZ_RANGE, "&&"),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0003_waitlist"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="appointment",
            name="excl_appointment_provider_overlap",
        ),
        migrations.RemoveConstraint(
            model_name="appointment",
            name="excl_appointment_operatory_overlap",
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                name="excl_appointment_provider_overlap",
                expressions=_expressions("provider"),
                condition=models.Q(status__in=EXCLUSION_STATUSES),
            ),
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                name="excl_appointment_operatory_overlap",
                expressions=_expressions("operatory"),
                condition=models.Q(
                    operatory__isnull=False,
                    status__in=EXCLUSION_STATUSES,
                ),
            ),
        ),
    ]
