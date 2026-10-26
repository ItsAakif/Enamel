"""``manage.py audit_verify`` - recompute the audit hash chain.

The command walks the chain in insert order, recomputes each row's digest from
its stored fields and reports the first inconsistency. Exit code 0 means the
chain is intact; 1 means a row was modified, deleted, or reordered.

    python manage.py audit_verify --clinic <uuid>
    python manage.py audit_verify            # platform (clinic-less) chain
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.audit import PLATFORM_CHAIN, verify_chain
from apps.core.rls import bypass_rls, require_bypass_capable


class Command(BaseCommand):
    help = "Verify the hash chain of the audit log for one clinic."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--clinic",
            dest="clinic",
            default=None,
            help="Clinic UUID whose chain should be verified (omit for platform events).",
        )

    @bypass_rls()
    def handle(self, *args: Any, **options: Any) -> None:
        require_bypass_capable("audit_verify")
        raw = options.get("clinic")
        clinic_id = None
        if raw:
            try:
                clinic_id = uuid.UUID(str(raw))
            except (TypeError, ValueError):
                raise CommandError("--clinic must be a UUID.") from None

        ok, count, broken_id = verify_chain(clinic_id)
        label = str(clinic_id) if clinic_id else PLATFORM_CHAIN

        if ok:
            self.stdout.write(f"chain ok: {count} events")
            return

        self.stderr.write(
            f"chain broken for {label}: first invalid event id={broken_id} "
            f"(verified {count} events before it)"
        )
        # SystemExit (not CommandError) keeps the exit code at 1 without adding
        # a second, redundant "CommandError:" banner to stderr.
        raise SystemExit(1)
