"""GiST exclusion constraints that make double-booking impossible (T110).

Two constraints on ``scheduling_appointment``:

* ``excl_appointment_provider_overlap`` -- one provider can never hold two
  *active* appointments that overlap in ``TSTZRANGE(start_at, end_at)``.
* ``excl_appointment_operatory_overlap`` -- the same for an operatory, and only
  when ``operatory IS NOT NULL`` (types that do not need a chair).

"Active" is the slot-holding status set (``requested``, ``held``, ``scheduled``,
``confirmed``, ``checked_in``, ``in_progress``); ``completed``, ``cancelled`` and
``no_show`` rows fall outside the constraint, which is what lets a reschedule
cancel and re-create inside one transaction and lets a hold expire.

``btree_gist`` (created in ``core`` migration ``0001_extensions``) supplies the
equality operator class the range column needs next to ``gist``. The default
``[)`` bounds mean an appointment ending at 10:00 and one starting at 10:00 do
not overlap. The service layer maps the resulting ``IntegrityError`` to a 409
``slot_conflict``.
"""

from __future__ import annotations

import django.contrib.postgres.constraints
from django.db import migrations, models

ACTIVE_STATUSES = [
    "checked_in",
    "confirmed",
    "held",
    "in_progress",
    "requested",
    "scheduled",
]


class Migration(migrations.Migration):
    dependencies = [
        # ``btree_gist`` (and the other extensions) are created here; the
        # exclusion constraints below need it, so the dependency is explicit
        # rather than relying on INSTALLED_APPS ordering.
        ("core", "0001_extensions"),
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="appointment",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                name="excl_appointment_provider_overlap",
                expressions=[
                    ("provider", "="),
                    (
                        models.Func(
                            models.F("start_at"),
                            models.F("end_at"),
                            function="TSTZRANGE",
                        ),
                        "&&",
                    ),
                ],
                condition=models.Q(status__in=ACTIVE_STATUSES),
            ),
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                name="excl_appointment_operatory_overlap",
                expressions=[
                    ("operatory", "="),
                    (
                        models.Func(
                            models.F("start_at"),
                            models.F("end_at"),
                            function="TSTZRANGE",
                        ),
                        "&&",
                    ),
                ],
                condition=models.Q(
                    operatory__isnull=False,
                    status__in=ACTIVE_STATUSES,
                ),
            ),
        ),
    ]
