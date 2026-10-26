"""Partial unique index on the gateway payment reference (ADR-013, step B2).

The payment webhook resolves its payment by ``(gateway, gateway_payment_id)``.
That lookup is a *capability*: it may only ever reach one row. The table
carried a non-unique index on the pair, so the duplicate probe

    SELECT gateway, gateway_payment_id, count(*)
    FROM billing_payment
    WHERE gateway_payment_id <> ''
    GROUP BY 1, 2 HAVING count(*) > 1;

returned no rows on the rehearsal database (2026-10-20), so the index is
created here.

The index is **partial**: a payment without a gateway reference (a cash
payment, a manually recorded one) is not affected, and the empty reference is
not a capability at all.

The reverse drops the index, so the migration is reversible.
"""

from django.db import migrations

INDEX_NAME = "billing_payment_gateway_ref_unique"

CREATE_SQL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
    "ON billing_payment (gateway, gateway_payment_id) "
    "WHERE gateway_payment_id <> ''"
)

DROP_SQL = f"DROP INDEX IF EXISTS {INDEX_NAME}"


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, reverse_sql=DROP_SQL),
    ]
