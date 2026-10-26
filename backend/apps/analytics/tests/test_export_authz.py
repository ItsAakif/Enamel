"""Export durability, authorisation and audit (T133).

Covers: a durable job + reference, CSV columns matching the declared headers,
the row cap, the signed short-lived download URL, a 403 for a role without
``analytics.view`` (and no side effect), and cross-clinic 404s.
"""

from __future__ import annotations

import csv
import io

import pytest

from apps.analytics.models import ExportJob
from apps.analytics.services import export as export_service
from apps.core.models import AuditEvent

from .conftest import MONTH_END, MONTH_START

pytestmark = pytest.mark.django_db

EXPORTS_URL = "/api/v1/analytics/exports/"
REPORTS = ["overview", "appointments", "revenue", "recall", "waitlist"]


def _post_export(client, report, *, export_format="csv"):
    return client.post(
        EXPORTS_URL,
        {
            "report": report,
            "format": export_format,
            "range": {"from": MONTH_START.isoformat(), "to": MONTH_END.isoformat()},
        },
    )


def test_export_creates_a_durable_job_reference_and_audit(owner, seeded_month):
    response = _post_export(owner, "appointments")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["reference"].startswith("EXP-")
    assert body["status"] == "completed"
    assert body["row_count"] == 30  # one row per day in June
    assert body["truncated"] is False
    assert body["download_url"].startswith(
        f"/api/v1/analytics/exports/{body['id']}/download/?token="
    )
    assert body["expires_in"] == 300

    job = ExportJob.all_objects.get(pk=body["id"])
    assert job.reference == body["reference"]
    assert len(job.payload_json) == job.row_count
    assert AuditEvent.objects.filter(
        clinic=owner.clinic, action="analytics.export_requested"
    ).count() == 1


@pytest.mark.parametrize("report", REPORTS)
def test_csv_columns_match_the_declared_table_headers(owner, seeded_month, report):
    created = _post_export(owner, report)
    assert created.status_code == 201, created.json()
    body = created.json()

    download = owner.get(body["download_url"])
    assert download.status_code == 200
    assert download["Content-Type"].startswith("text/csv")

    reader = csv.reader(io.StringIO(download.content.decode("utf-8")))
    header = next(reader)
    assert header == body["columns"]
    assert header == ExportJob.all_objects.get(pk=body["id"]).columns_json


def test_json_export_matches_columns_and_rows(owner, seeded_month):
    created = _post_export(owner, "waitlist", export_format="json")
    assert created.status_code == 201, created.json()
    body = created.json()

    download = owner.get(body["download_url"])
    assert download.status_code == 200
    assert download["Content-Type"].startswith("application/json")

    import json

    payload = json.loads(download.content.decode("utf-8"))
    assert payload["columns"] == body["columns"]
    assert len(payload["rows"]) == body["row_count"]
    assert set(payload["rows"][0]) == set(body["columns"])


def test_download_requires_a_valid_signed_token(owner, seeded_month):
    body = _post_export(owner, "appointments").json()

    no_token = owner.get(f"/api/v1/analytics/exports/{body['id']}/download/")
    assert no_token.status_code == 403

    tampered = owner.get(
        f"/api/v1/analytics/exports/{body['id']}/download/", {"token": "x" + body["download_url"].split("token=")[1]}
    )
    assert tampered.status_code == 403


def test_row_cap_truncates_and_flags_the_export(owner, seeded_month, settings):
    settings.ANALYTICS_EXPORT_ROW_CAP = 5

    response = _post_export(owner, "overview")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["row_count"] == 5
    assert body["truncated"] is True
    assert ExportJob.all_objects.get(pk=body["id"]).truncated is True


def test_unauthorized_export_is_403_and_writes_nothing(assistant, seeded_month):
    response = _post_export(assistant, "appointments")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert ExportJob.all_objects.count() == 0
    assert (
        AuditEvent.objects.filter(action="analytics.export_requested").count() == 0
    )


def test_unauthorized_detail_and_download_are_403(owner, assistant, seeded_month):
    body = _post_export(owner, "appointments").json()

    assert assistant.get(f"/api/v1/analytics/exports/{body['id']}/").status_code == 403
    assert (
        assistant.get(f"/api/v1/analytics/exports/{body['id']}/download/").status_code
        == 403
    )


def test_foreign_export_is_404(owner, seeded_month, staff_factory):
    body = _post_export(owner, "appointments").json()
    other = staff_factory(role="owner", slug="other-export-clinic")

    assert other.get(f"/api/v1/analytics/exports/{body['id']}/").status_code == 404
    assert (
        other.get(f"/api/v1/analytics/exports/{body['id']}/download/").status_code == 404
    )


def test_export_rejects_an_unknown_report_and_format(owner, seeded_month):
    bad_report = owner.post(
        EXPORTS_URL,
        {
            "report": "nope",
            "format": "csv",
            "range": {"from": MONTH_START.isoformat(), "to": MONTH_END.isoformat()},
        },
    )
    assert bad_report.status_code == 400
    assert "report" in bad_report.json()["error"]["field_errors"]

    bad_format = owner.post(
        EXPORTS_URL,
        {
            "report": "overview",
            "format": "pdf",
            "range": {"from": MONTH_START.isoformat(), "to": MONTH_END.isoformat()},
        },
    )
    assert bad_format.status_code == 400
    assert "format" in bad_format.json()["error"]["field_errors"]


def test_export_token_is_bound_to_the_clinic(owner, seeded_month, staff_factory):
    body = _post_export(owner, "appointments").json()
    other = staff_factory(role="owner", slug="token-clinic")
    # A foreign clinic cannot use the owner's token: the job lookup is scoped.
    token = body["download_url"].split("token=")[1]
    response = other.get(
        f"/api/v1/analytics/exports/{body['id']}/download/", {"token": token}
    )
    assert response.status_code == 404


def test_render_job_returns_the_materialised_rows(owner, seeded_month):
    from apps.core.tenancy import with_clinic

    with with_clinic(owner.clinic):
        job = export_service.create_export(
            owner.clinic,
            report="waitlist",
            export_format="json",
            from_date=MONTH_START,
            to_date=MONTH_END,
            actor=owner.user,
        )

    content_type, body = export_service.render_job(job)
    assert content_type.startswith("application/json")
    assert job.columns_json == ["metric", "label", "unit", "value", "numerator_value", "denominator_value", "currency"]
    assert job.row_count == 4
    assert '"columns"' in body and '"rows"' in body
