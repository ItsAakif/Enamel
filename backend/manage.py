#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

# Commands that legitimately cross clinic boundaries (schema work, seeding,
# audit verification, retention, fixture loading, maintenance dumps). The
# app-ready hook turns this into ``SET app.bypass_rls = on`` on every database
# connection. Request and Celery entry points never set it.
_RLS_BYPASS_COMMANDS = frozenset(
    {
        "apply_patient_retention",
        "audit_verify",
        "dumpdata",
        "flush",
        "ingest_knowledge",
        "loaddata",
        "makemigrations",
        "migrate",
        "seed_demo",
        "seed_rcm_fixtures",
        "showmigrations",
        "sqlmigrate",
        "sqlsequencereset",
    }
)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in _RLS_BYPASS_COMMANDS:
        os.environ.setdefault("ENAMEL_RLS_BYPASS", "1")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ImportError(
            "Couldn't import Django. Are you sure it is installed and available "
            "on your PYTHONPATH environment variable? Did you forget to activate "
            "the project virtualenv?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
