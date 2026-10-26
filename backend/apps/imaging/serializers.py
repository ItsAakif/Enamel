"""Imaging API serializers (T127, T129).

Responses never expose storage keys or the on-disk layout: the instance
serializer carries the content hash and pixel metadata, and the preview is a
separate, permission-checked endpoint. Findings are always labelled
``suggested``; the API never presents them as a diagnosis.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import InferenceJob, Instance, Series, Study


class InstanceSerializer(serializers.ModelSerializer):
    has_preview = serializers.SerializerMethodField()

    class Meta:
        model = Instance
        fields = [
            "id",
            "series",
            "sop_instance_uid",
            "instance_number",
            "rows",
            "columns",
            "bits_allocated",
            "photometric",
            "transfer_syntax",
            "sha256",
            "size_bytes",
            "preview_status",
            "has_preview",
            "created_at",
        ]

    def get_has_preview(self, obj: Instance) -> bool:
        return obj.preview_status == Instance.PreviewStatus.READY and bool(obj.preview_key)


class SeriesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Series
        fields = [
            "id",
            "study",
            "series_instance_uid",
            "modality",
            "body_part",
            "description",
            "instance_count",
            "created_at",
        ]


class SeriesDetailSerializer(SeriesSerializer):
    instances = InstanceSerializer(many=True, read_only=True)

    class Meta(SeriesSerializer.Meta):
        fields = [*SeriesSerializer.Meta.fields, "instances"]


class StudySerializer(serializers.ModelSerializer):
    series_count = serializers.SerializerMethodField()
    instance_count = serializers.SerializerMethodField()

    class Meta:
        model = Study
        fields = [
            "id",
            "patient",
            "study_instance_uid",
            "accession",
            "modality",
            "study_date",
            "description",
            "status",
            "source",
            "series_count",
            "instance_count",
            "created_at",
        ]

    def get_series_count(self, obj: Study) -> int:
        return obj.series.count()

    def get_instance_count(self, obj: Study) -> int:
        return Instance.objects.filter(series__study=obj).count()


class StudyDetailSerializer(StudySerializer):
    series = SeriesDetailSerializer(many=True, read_only=True)

    class Meta(StudySerializer.Meta):
        fields = [*StudySerializer.Meta.fields, "series"]


class InferenceJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = InferenceJob
        fields = [
            "id",
            "study",
            "status",
            "model_name",
            "model_version",
            "config_json",
            "findings_json",
            "output_hash",
            "error_code",
            "started_at",
            "finished_at",
            "created_at",
        ]


class DicomWebStudySerializer(serializers.Serializer):
    """QIDO-RS study result (a documented subset of the DICOM JSON model)."""

    StudyInstanceUID = serializers.CharField()
    PatientID = serializers.CharField()
    StudyDate = serializers.CharField(allow_null=True)
    ModalitiesInStudy = serializers.ListField(child=serializers.CharField())
    NumberOfStudyRelatedSeries = serializers.IntegerField()
    NumberOfStudyRelatedInstances = serializers.IntegerField()


class DicomWebSeriesSerializer(serializers.Serializer):
    """QIDO-RS series result (a documented subset of the DICOM JSON model)."""

    SeriesInstanceUID = serializers.CharField()
    StudyInstanceUID = serializers.CharField()
    Modality = serializers.CharField()
    BodyPartExamined = serializers.CharField()
    NumberOfSeriesRelatedInstances = serializers.IntegerField()


class StudyUploadSerializer(serializers.Serializer):
    """Multipart upload body: a patient plus one or more DICOM files."""

    patient = serializers.UUIDField()
    files = serializers.ListField(child=serializers.FileField(), allow_empty=False)
    source = serializers.CharField(required=False, allow_blank=True, default="")


class InferenceJobCreateResponseSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    study = serializers.UUIDField()
    status = serializers.CharField()
    model_name = serializers.CharField()
    model_version = serializers.CharField()
    disclosure = serializers.CharField()


__all__ = [
    "DicomWebSeriesSerializer",
    "DicomWebStudySerializer",
    "InferenceJobCreateResponseSerializer",
    "InferenceJobSerializer",
    "InstanceSerializer",
    "SeriesDetailSerializer",
    "SeriesSerializer",
    "StudyDetailSerializer",
    "StudySerializer",
    "StudyUploadSerializer",
]
