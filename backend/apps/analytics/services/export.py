"""Durable report exports (T133).

An export is materialised **once**, at request time, and stored on the job:

* the row cap (``settings.ANALYTICS_EXPORT_ROW_CAP``, default 10,000) is applied
  before anything is persisted, and ``truncated`` says so;
* every export writes an audit event (``analytics.export_requested``) in the
  same transaction as the job row;
* the download link is a signed, short-lived token bound to the job id and the
  clinic, so a leaked URL cannot be retargeted at another tenant's export.

Columns are declared per report and are the same list the CSV header is built
from, so a table header and its export can never drift apart.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Callable
from datetime import date as date_type
from typing import Any

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError

from ..models import ExportJob
from . import appointments, recall, revenue, waitlist

EXPORT_SALT = "enamel.analytics.export"
DEFAULT_ROW_CAP = 10_000
DEFAULT_TTL_SECONDS = 300


def row_cap() -> int:
    return int(getattr(settings, "ANALYTICS_EXPORT_ROW_CAP", DEFAULT_ROW_CAP))


def ttl_seconds() -> int:
    return int(getattr(settings, "ANALYTICS_EXPORT_TTL_SECONDS", DEFAULT_TTL_SECONDS))


# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

METRIC_COLUMNS = [
    "metric",
    "label",
    "unit",
    "value",
    "numerator_value",
    "denominator_value",
    "currency",
]


def _metric_rows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in payloads:
        rows.append(
            {
                "metric": item["key"],
                "label": item["label"],
                "unit": item["unit"],
                "value": item["value"],
                "numerator_value": item["numerator_value"],
                "denominator_value": item["denominator_value"],
                "currency": item.get("currency"),
            }
        )
    return rows


def overview_rows(clinic, from_date, to_date, location=None):
    payloads: list[dict[str, Any]] = []
    for key in (
        "appointments.total",
        "appointments.completion_rate",
        "appointments.cancellation_rate",
        "appointments.no_show_rate",
        "appointments.utilisation",
        "patients.new",
        "patients.returning",
        "revenue.invoiced_minor",
        "revenue.collected_minor",
        "revenue.outstanding_minor",
        "recall.due",
        "recall.completed",
        "reminders.sent",
        "reminders.failed",
        "waitlist.offers",
        "waitlist.claims",
    ):
        value = _compute(key, clinic, from_date, to_date, location)
        payloads.extend(value if isinstance(value, list) else [value])
    return METRIC_COLUMNS, _metric_rows(payloads)


def appointments_rows(clinic, from_date, to_date, location=None):
    columns = ["date", "total", "completed", "cancelled", "no_show"]
    rows = [
        {name: point[name] for name in columns}
        for point in appointments.series(clinic, from_date, to_date, location)
    ]
    return columns, rows


def revenue_rows(clinic, from_date, to_date, location=None):
    columns = ["date", "invoiced_minor", "collected_minor"]
    rows = [
        {name: point[name] for name in columns}
        for point in revenue.series(clinic, from_date, to_date, location)
    ]
    return columns, rows


def recall_rows(clinic, from_date, to_date, location=None):
    payloads = [
        recall.due(clinic, from_date, to_date),
        recall.completed(clinic, from_date, to_date),
        recall.completion_rate(clinic, from_date, to_date),
        recall.reminders_sent(clinic, from_date, to_date),
        recall.reminders_delivered(clinic, from_date, to_date),
        recall.reminders_failed(clinic, from_date, to_date),
        recall.delivery_rate(clinic, from_date, to_date),
    ]
    return METRIC_COLUMNS, _metric_rows(payloads)


def waitlist_rows(clinic, from_date, to_date, location=None):
    payloads = [
        waitlist.offers(clinic, from_date, to_date),
        waitlist.claims(clinic, from_date, to_date),
        waitlist.expired(clinic, from_date, to_date),
        waitlist.claim_rate(clinic, from_date, to_date),
    ]
    return METRIC_COLUMNS, _metric_rows(payloads)


BUILDERS: dict[str, Callable[..., tuple[list[str], list[dict[str, Any]]]]] = {
    ExportJob.Report.OVERVIEW: overview_rows,
    ExportJob.Report.APPOINTMENTS: appointments_rows,
    ExportJob.Report.REVENUE: revenue_rows,
    ExportJob.Report.RECALL: recall_rows,
    ExportJob.Report.WAITLIST: waitlist_rows,
}


def _compute(key, clinic, from_date, to_date, location):
    from ..registry import compute

    return compute(key, clinic, from_date, to_date, location)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def render_json(columns: list[str], rows: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"columns": columns, "rows": rows},
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


# --------------------------------------------------------------------------- #
# Job lifecycle
# --------------------------------------------------------------------------- #


def reference_for() -> str:
    return f"EXP-{uuid.uuid4().hex[:10].upper()}"


def create_export(
    clinic,
    *,
    report: str,
    export_format: str,
    from_date: date_type,
    to_date: date_type,
    location=None,
    actor=None,
    request=None,
) -> ExportJob:
    """Materialise an export, persist it and audit it in one transaction."""

    if report not in BUILDERS:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"report": [f"must be one of {sorted(BUILDERS)}"]},
        )
    if export_format not in (ExportJob.Format.CSV, ExportJob.Format.JSON):
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"format": ["must be csv or json"]},
        )
    if to_date < from_date:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"from": ["must not be after `to`"]},
        )

    columns, rows = BUILDERS[report](clinic, from_date, to_date, location)
    cap = row_cap()
    truncated = len(rows) > cap
    rows = rows[:cap]

    with transaction.atomic():
        job = ExportJob.all_objects.create(
            clinic=clinic,
            report=report,
            format=export_format,
            date_from=from_date,
            date_to=to_date,
            location=location,
            status=ExportJob.Status.COMPLETED,
            reference=reference_for(),
            payload_json=rows,
            columns_json=columns,
            row_count=len(rows),
            truncated=truncated,
            requested_by=actor,
            completed_at=timezone.now(),
        )
        record_audit(
            "analytics.export_requested",
            entity=job,
            after={
                "report": report,
                "format": export_format,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "row_count": len(rows),
                "truncated": truncated,
                "location_id": str(getattr(location, "id", location))
                if location is not None
                else None,
            },
            actor=actor,
            request=request,
        )
    return job


def issue_download_token(job: ExportJob) -> str:
    return signing.dumps(
        {"job": str(job.pk), "clinic": str(job.clinic_id)},
        salt=EXPORT_SALT,
        compress=True,
    )


def verify_download_token(token: str, *, job: ExportJob) -> bool:
    if not token:
        return False
    try:
        payload = signing.loads(token, salt=EXPORT_SALT, max_age=ttl_seconds())
    except signing.SignatureExpired:
        return False
    except signing.BadSignature:
        return False
    except Exception:  # pragma: no cover - defensive: any malformed token
        return False
    return (
        str(payload.get("job")) == str(job.pk)
        and str(payload.get("clinic")) == str(job.clinic_id)
    )


def download_path(job: ExportJob) -> str:
    return f"/api/v1/analytics/exports/{job.pk}/download/"


def download_url(job: ExportJob) -> str:
    return f"{download_path(job)}?token={issue_download_token(job)}"


def render_job(job: ExportJob) -> tuple[str, str]:
    """Return ``(content_type, body)`` for a completed job."""

    columns = [str(column) for column in (job.columns_json or [])]
    rows = list(job.payload_json or [])
    if job.format == ExportJob.Format.CSV:
        return "text/csv", render_csv(columns, rows)
    return "application/json", render_json(columns, rows)


__all__ = [
    "BUILDERS",
    "DEFAULT_ROW_CAP",
    "DEFAULT_TTL_SECONDS",
    "EXPORT_SALT",
    "appointments_rows",
    "create_export",
    "download_path",
    "download_url",
    "issue_download_token",
    "overview_rows",
    "recall_rows",
    "reference_for",
    "render_csv",
    "render_job",
    "render_json",
    "revenue_rows",
    "row_cap",
    "ttl_seconds",
    "verify_download_token",
    "waitlist_rows",
]
