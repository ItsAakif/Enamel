"""The published appointment types are public information (ADR-013).

The public booking surface resolves a bookable appointment type by its id
before it knows the clinic: the payload carries the type, the slot and the
patient, and the type is the only pointer to the clinic. The type is read
without a clinic context, and the tenant policy (``clinic_isolation``) hides
every row in that case.

A type that is **active and published for booking** is public information:
it is part of the public clinic page (``GET /public/clinics/{slug}/``) and
nothing else. This policy makes exactly those rows readable without a clinic
context, for the staff runtime only. Everything else about a type (the
clinic's own types, the inactive ones) stays clinic-scoped.

The reverse drops the policy.
"""

from django.db import migrations

POLICY_NAME = "public_booking_type_read"

CREATE_SQL = (
    f'CREATE POLICY "{POLICY_NAME}" ON scheduling_appointmenttype '
    f"FOR SELECT TO enamel_app USING (is_active AND public_bookable)"
)

DROP_SQL = f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON scheduling_appointmenttype'


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_principal_scoped_rls"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, reverse_sql=DROP_SQL),
    ]
