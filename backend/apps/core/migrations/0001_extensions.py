"""Create PostgreSQL extensions required by later migrations.

``btree_gist`` enables exclusion constraints on scheduling ranges,
``pgcrypto`` provides digest/gen_random_uuid helpers, and ``vector`` backs the
RAG knowledge store. Reverse is intentionally a no-op: dropping extensions
could destroy data owned by other schemas.
"""

from django.db import migrations

EXTENSIONS = ("btree_gist", "pgcrypto", "vector")

CREATE_SQL = [f"CREATE EXTENSION IF NOT EXISTS {name}" for name in EXTENSIONS]


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=CREATE_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
