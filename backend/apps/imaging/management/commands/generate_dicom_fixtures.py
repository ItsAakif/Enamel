"""Write the deterministic DICOM fixture set (T127).

Usage::

    python manage.py generate_dicom_fixtures --out fixtures/dicom

The fixtures are synthetic and generated locally; re-running the command
produces byte-identical files (asserted by the imaging test suite).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.imaging.dicom.fixtures import write_fixtures


class Command(BaseCommand):
    help = "Generate deterministic synthetic DICOM fixtures and their README."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            default="fixtures/dicom",
            help="Output directory (absolute, or relative to the backend directory).",
        )

    def handle(self, *args, **options):
        out = Path(options["out"])
        if not out.is_absolute():
            out = Path(settings.BASE_DIR) / out

        manifest = write_fixtures(out)
        for name in manifest["files"]:
            self.stdout.write(f"wrote {name}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {manifest['count']} DICOM fixtures to {manifest['dir']}"
            )
        )
