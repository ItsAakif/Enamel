"""DICOMweb-subset endpoints (T127): QIDO-RS studies/series and WADO-RS instance.

Only the read paths a viewer needs are implemented. Responses use the DICOM JSON
model shape (``{"<tag>": {"vr": ..., "Value": [...]}}``) for QIDO and raw
``application/dicom`` bytes for WADO, so an external PACS can be added later
behind the same URLs.

QIDO never returns a patient name: the ``PatientID`` element carries the
internal patient UUID (an opaque identifier), not a clinical identifier.
"""

from __future__ import annotations

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError

from .. import storage
from ..models import Study
from ..serializers import DicomWebSeriesSerializer, DicomWebStudySerializer
from ..services import access
from .common import ImagingScopedView


def _value(vr: str, values: list) -> dict:
    return {"vr": vr, "Value": values}


class QidoStudyListView(ImagingScopedView):
    """QIDO-RS: search studies (subset)."""

    allowed_query_params = {"patient", "modality"}

    @extend_schema(responses=DicomWebStudySerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Study.all_objects.filter(clinic_id=clinic.id)
        patient = (request.query_params.get("patient") or "").strip()
        if patient:
            queryset = queryset.filter(patient_id=patient)
        modality = (request.query_params.get("modality") or "").strip()
        if modality:
            queryset = queryset.filter(series__modality__iexact=modality).distinct()

        results = []
        for study in queryset.prefetch_related("series__instances").order_by("id"):
            series_list = list(study.series.all())
            modalities = sorted({s.modality for s in series_list if s.modality})
            instance_count = sum(s.instance_count for s in series_list)
            results.append(
                {
                    "0020000D": _value("UI", [study.study_instance_uid]),
                    "00100020": _value("LO", [str(study.patient_id)]),
                    "00080020": _value(
                        "DA",
                        [study.study_date.strftime("%Y%m%d")] if study.study_date else [],
                    ),
                    "00080061": _value("CS", modalities),
                    "00201206": _value("IS", [len(series_list)]),
                    "00201208": _value("IS", [instance_count]),
                }
            )

        record_audit(
            "imaging.dicomweb_studies_viewed",
            entity_type="imaging.study",
            entity_id="",
            after={"count": len(results)},
            actor=request.user,
            request=request,
        )
        return Response(results)


class QidoSeriesListView(ImagingScopedView):
    """QIDO-RS: list the series of one study (subset)."""

    @extend_schema(responses=DicomWebSeriesSerializer(many=True))
    def get(self, request, study_uid):
        clinic = self.clinic(request)
        study = Study.all_objects.filter(
            clinic_id=clinic.id, study_instance_uid=study_uid
        ).first()
        if study is None:
            raise ApiError("not_found", "Not found.", status_code=404)

        results = []
        for series in study.series.all().order_by("created_at", "id"):
            results.append(
                {
                    "0020000E": _value("UI", [series.series_instance_uid]),
                    "0020000D": _value("UI", [study.study_instance_uid]),
                    "00080060": _value("CS", [series.modality] if series.modality else []),
                    "00180015": _value("CS", [series.body_part] if series.body_part else []),
                    "00201209": _value("IS", [series.instance_count]),
                }
            )

        record_audit(
            "imaging.dicomweb_series_viewed",
            entity=study,
            after={"count": len(results)},
            actor=request.user,
            request=request,
        )
        return Response(results)


class WadoInstanceView(ImagingScopedView):
    """WADO-RS: retrieve one instance as ``application/dicom`` bytes."""

    @extend_schema(responses={(200, "application/dicom"): bytes})
    def get(self, request, sop_uid):
        clinic = self.clinic(request)
        instance = access.instance_for_sop_uid(clinic, sop_uid)
        data = storage.read_bytes(instance.storage_key)

        record_audit(
            "imaging.instance_downloaded",
            entity=instance,
            after={
                "sop_instance_uid": instance.sop_instance_uid,
                "sha256": instance.sha256,
                "size_bytes": instance.size_bytes,
            },
            actor=request.user,
            request=request,
        )

        response = HttpResponse(data, content_type="application/dicom")
        response["Content-Disposition"] = f'attachment; filename="{sop_uid}.dcm"'
        response["X-Content-SHA256"] = instance.sha256
        return response


__all__ = ["QidoSeriesListView", "QidoStudyListView", "WadoInstanceView"]
