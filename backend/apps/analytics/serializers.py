"""Analytics API serializers (T133).

The metric shape is declared once here so every report response (and the
OpenAPI document) carries ``definition``, ``numerator`` and ``denominator``
alongside the value.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import ExportJob
from .services.export import ttl_seconds


class MetricSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    unit = serializers.CharField()
    definition = serializers.CharField()
    numerator = serializers.CharField()
    denominator = serializers.CharField()
    value = serializers.FloatField()
    numerator_value = serializers.FloatField()
    denominator_value = serializers.FloatField()
    currency = serializers.CharField(required=False, allow_null=True)


class ReportSerializer(serializers.Serializer):
    """Common envelope for every report endpoint."""

    range = serializers.DictField()
    metrics = MetricSerializer(many=True)


class OverviewSerializer(ReportSerializer):
    appointments = serializers.DictField()
    revenue = serializers.DictField()
    definition = serializers.DictField()


class AppointmentsSerializer(ReportSerializer):
    by_status = serializers.DictField()
    patients = serializers.DictField()
    series = serializers.ListField(child=serializers.DictField())
    definition = serializers.DictField()


class RevenueSerializer(ReportSerializer):
    by_currency = serializers.DictField()
    series = serializers.ListField(child=serializers.DictField())
    definition = serializers.DictField()


class RecallSerializer(ReportSerializer):
    by_status = serializers.DictField()
    definition = serializers.DictField()


class WaitlistSerializer(ReportSerializer):
    by_status = serializers.DictField()
    entries_by_status = serializers.DictField()
    definition = serializers.DictField()


class ExportRequestSerializer(serializers.Serializer):
    report = serializers.ChoiceField(choices=ExportJob.Report.choices)
    format = serializers.ChoiceField(choices=ExportJob.Format.choices)
    #: ``{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}`` -- two clinic-local dates.
    #: ``from``/``to`` are Python keywords, so the pair is validated as a dict
    #: rather than as two named fields.
    range = serializers.DictField()
    location = serializers.UUIDField(required=False, allow_null=True)

    def validate_range(self, value: dict) -> dict:
        from datetime import date as date_type

        if not isinstance(value, dict) or "from" not in value or "to" not in value:
            raise serializers.ValidationError(
                {"from": ["`range` must contain `from` and `to`"]}
            )
        try:
            from_date = date_type.fromisoformat(str(value["from"]))
            to_date = date_type.fromisoformat(str(value["to"]))
        except ValueError as exc:
            raise serializers.ValidationError(
                {"from": ["must be an ISO date (YYYY-MM-DD)"]}
            ) from exc
        if to_date < from_date:
            raise serializers.ValidationError({"from": ["must not be after `to`"]})
        return {"from": from_date, "to": to_date}


class ExportJobSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    expires_in = serializers.SerializerMethodField()
    columns = serializers.JSONField(source="columns_json", read_only=True)

    class Meta:
        model = ExportJob
        fields = [
            "id",
            "reference",
            "report",
            "format",
            "status",
            "date_from",
            "date_to",
            "location",
            "row_count",
            "truncated",
            "columns",
            "created_at",
            "completed_at",
            "download_url",
            "expires_in",
        ]

    def get_download_url(self, obj: ExportJob) -> str | None:
        if obj.status != ExportJob.Status.COMPLETED:
            return None
        from .services.export import download_url

        return download_url(obj)

    def get_expires_in(self, obj: ExportJob) -> int:
        return ttl_seconds()


__all__ = [
    "AppointmentsSerializer",
    "ExportJobSerializer",
    "ExportRequestSerializer",
    "MetricSerializer",
    "OverviewSerializer",
    "RecallSerializer",
    "ReportSerializer",
    "RevenueSerializer",
    "WaitlistSerializer",
]
