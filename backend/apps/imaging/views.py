"""Imaging API views (T127, T129).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /imaging/studies/`` -- list / multipart upload
* ``GET /imaging/studies/{id}/`` -- study + series + instance metadata
* ``GET /imaging/instances/{id}/preview/`` -- JPEG preview
* ``POST|GET /imaging/studies/{id}/inference/`` -- run / list inference jobs
* ``GET /imaging/dicomweb/...`` -- QIDO/WADO subset (see ``api/dicomweb.py``)

Every lookup runs through the tenant manager plus an explicit clinic filter, so a
foreign study/series/instance/preview is an indistinguishable 404.
"""

from __future__ import annotations

from datetime import date

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination
from apps.patients.models import Patient

from .api.common import ImagingScopedView
from .models import InferenceJob, Study
from .serializers import (
    InferenceJobCreateResponseSerializer,
    InferenceJobSerializer,
    StudyDetailSerializer,
    StudySerializer,
    StudyUploadSerializer,
)
from .services import access, inference, ingest

CV_DISCLOSURE = (
    "Experimental assistive signal only. Findings are suggestions, not a "
    "diagnosis, and are never written to the chart automatically."
)


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={field: ["expected an ISO-8601 date (YYYY-MM-DD)"]},
        ) from exc


class StudyListCreateView(ImagingScopedView):
    allowed_query_params = {"page", "page_size", "patient", "modality", "from", "to"}

    # GET returns the StandardPagination envelope; the batch-upload POST returns
    # a bare array (the schema class only applies the envelope to GET).
    pagination_class = StandardPagination

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("imaging.view",)
        return ("imaging.upload",)

    @extend_schema(responses=StudySerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Study.all_objects.filter(clinic_id=clinic.id).order_by(
            "-study_date", "-created_at", "id"
        )

        patient = (request.query_params.get("patient") or "").strip()
        if patient:
            queryset = queryset.filter(patient_id=patient)

        modality = (request.query_params.get("modality") or "").strip()
        if modality:
            queryset = queryset.filter(modality__iexact=modality)

        start = (request.query_params.get("from") or "").strip()
        end = (request.query_params.get("to") or "").strip()
        if bool(start) != bool(end):
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"from": ["both 'from' and 'to' are required"]},
            )
        if start and end:
            queryset = queryset.filter(
                study_date__gte=_parse_date(start, "from"),
                study_date__lte=_parse_date(end, "to"),
            )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(StudySerializer(page, many=True).data)

    @extend_schema(
        request=StudyUploadSerializer,
        responses={201: StudySerializer(many=True)},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = StudyUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.all_objects.filter(clinic_id=clinic.id, pk=data["patient"]).first()
        if patient is None:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"patient": ["patient not found in this clinic"]},
            )

        files = [(upload.name or "upload.dcm", upload.read()) for upload in data["files"]]
        result = ingest.upload_studies(
            clinic=clinic,
            patient=patient,
            files=files,
            actor=request.user,
            request=request,
        )
        return Response(
            StudySerializer(result.studies, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class StudyDetailView(ImagingScopedView):
    @extend_schema(responses=StudyDetailSerializer)
    def get(self, request, study_id):
        clinic = self.clinic(request)
        study = (
            Study.all_objects.filter(clinic_id=clinic.id, pk=study_id)
            .prefetch_related("series__instances")
            .first()
        )
        if study is None:
            raise ApiError("not_found", "Not found.", status_code=404)

        record_audit(
            "imaging.study_viewed",
            entity=study,
            after={"study_instance_uid": study.study_instance_uid},
            actor=request.user,
            request=request,
        )
        return Response(StudyDetailSerializer(study).data)


class InstancePreviewView(ImagingScopedView):
    @extend_schema(responses={(200, "image/jpeg"): bytes})
    def get(self, request, instance_id):
        clinic = self.clinic(request)
        instance = access.instance_for_clinic(clinic, instance_id)
        data = access.preview_bytes(instance)

        record_audit(
            "imaging.instance_previewed",
            entity=instance,
            after={"sop_instance_uid": instance.sop_instance_uid},
            actor=request.user,
            request=request,
        )
        response = HttpResponse(data, content_type="image/jpeg")
        response["Content-Disposition"] = f'inline; filename="{instance.sop_instance_uid}.jpg"'
        return response


class StudyInferenceView(ImagingScopedView):
    allowed_query_params = {"page", "page_size"}

    pagination_class = StandardPagination

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("imaging.view",)
        return ("imaging.upload",)

    def _study(self, request, study_id) -> Study:
        return access.study_for_clinic(self.clinic(request), study_id)

    @extend_schema(responses=InferenceJobSerializer(many=True))
    def get(self, request, study_id):
        study = self._study(request, study_id)
        queryset = InferenceJob.all_objects.filter(
            clinic_id=study.clinic_id, study=study
        ).order_by("-created_at", "-id")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            InferenceJobSerializer(page, many=True).data
        )

    @extend_schema(
        request=None,
        responses={202: InferenceJobCreateResponseSerializer},
    )
    def post(self, request, study_id):
        study = self._study(request, study_id)
        job = inference.create_inference_job(study, actor=request.user, request=request)
        payload = {
            "job_id": job.id,
            "study": study.id,
            "status": job.status,
            "model_name": job.model_name,
            "model_version": job.model_version,
            "disclosure": CV_DISCLOSURE,
        }
        return Response(payload, status=status.HTTP_202_ACCEPTED)


__all__ = [
    "InstancePreviewView",
    "StudyDetailView",
    "StudyInferenceView",
    "StudyListCreateView",
]
