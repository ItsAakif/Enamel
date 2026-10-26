"""``apply_patient_retention`` -- report or purge retention-eligible patients.

Dry run by default; the destructive path requires the explicit ``--apply`` flag
and writes one ``patient.purged`` audit row per patient. Audit rows themselves
are never deleted.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.clinics.models import Clinic
from apps.core.rls import bypass_rls, require_bypass_capable
from apps.patients.services.retention import apply_retention


class Command(BaseCommand):
    help = "Report (or with --apply, purge) patients past their clinical retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clinic",
            required=True,
            help="Clinic UUID to evaluate.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report candidates only (default).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Purge the reported patients. Requires the explicit flag.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Evaluate at most N patients.",
        )

    @bypass_rls()
    def handle(self, *args, **options):
        require_bypass_capable("apply_patient_retention")
        if options["apply"] and options["dry_run"]:
            raise CommandError("--dry-run and --apply are mutually exclusive.")
        try:
            clinic_id = uuid.UUID(str(options["clinic"]))
        except (TypeError, ValueError):
            raise CommandError("--clinic must be a UUID.") from None

        clinic = Clinic.objects.filter(pk=clinic_id).first()
        if clinic is None:
            raise CommandError("Clinic not found.")

        result = apply_retention(
            clinic,
            dry_run=not options["apply"],
            limit=options["limit"],
        )

        self.stdout.write(
            f"clinic {result['clinic_id']} retention {result['retention']}"
        )
        for candidate in result["candidates"]:
            self.stdout.write(
                f"  {candidate['number']} {candidate['full_name']} "
                f"last_activity={candidate['last_activity_at']} status={candidate['status']}"
            )
        if result["dry_run"]:
            self.stdout.write(
                f"dry run: {len(result['candidates'])} patient(s) eligible for purge"
            )
        else:
            self.stdout.write(f"purged {result['purged']} patient(s)")
