"""``manage.py ingest_knowledge`` (T125).

Ingest every text/markdown fixture under ``--path`` for one clinic. Ingestion is
idempotent by ``(clinic, sha256)``, so running the command twice creates no
duplicate chunks -- the second run reports every file as unchanged.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.clinics.models import Clinic
from apps.core.rls import bypass_rls

from ...rag.ingest import TEXT_SUFFIXES, ingest_file

FAQ_MARKER = "faq"


def _kind_for(stem: str) -> str:
    return "faq" if FAQ_MARKER in stem.lower() else "policy"


def _title_for(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip().title() or "Untitled document"


class Command(BaseCommand):
    help = "Ingest text/markdown knowledge files for a clinic (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--clinic", required=True, help="Clinic UUID.")
        parser.add_argument(
            "--path",
            required=True,
            help="A file or a directory of .md/.txt files (relative to backend/).",
        )

    @bypass_rls()
    def handle(self, *args, **options):
        clinic = Clinic.objects.filter(pk=options["clinic"]).first()
        if clinic is None:
            raise CommandError(f"Clinic '{options['clinic']}' was not found.")

        path = Path(options["path"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if not path.exists():
            raise CommandError(f"Path '{path}' does not exist.")

        if path.is_dir():
            files = sorted(
                candidate
                for candidate in path.iterdir()
                if candidate.is_file() and candidate.suffix.lower() in TEXT_SUFFIXES
            )
        else:
            files = [path]

        if not files:
            raise CommandError(f"No .md/.txt files found under '{path}'.")

        created = 0
        unchanged = 0
        chunk_total = 0
        for file_path in files:
            result = ingest_file(
                clinic,
                title=_title_for(file_path.stem),
                filename=file_path.name,
                content=file_path.read_bytes(),
                source=file_path.name,
                kind=_kind_for(file_path.stem),
            )
            chunk_total += result.chunk_count
            if result.created:
                created += 1
            else:
                unchanged += 1
            self.stdout.write(
                f"{'ingested' if result.created else 'unchanged'}: "
                f"{file_path.name} ({result.chunk_count} chunks)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. clinic={clinic.id} files={len(files)} "
                f"created={created} unchanged={unchanged} chunks={chunk_total}"
            )
        )
