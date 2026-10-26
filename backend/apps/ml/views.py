"""ML inference API views (T131).

URL layout (mounted under ``/api/v1/``):

* ``GET  /ml/no-show/models/``  -- registry rows visible to the clinic
* ``POST /ml/no-show/predict/`` -- ``{appointment_id, features?}`` ->
  ``{probability, threshold, decision, model_version, factors, synthetic_data}``

Guarantees:

* a foreign appointment is a 404 (tenant manager), never a 400;
* an incomplete feature vector is a 422 that names every missing feature -- the
  API never silently imputes;
* a retired model is unreachable for new predictions, but the ``Prediction``
  rows it already produced keep their ``model_version`` string and stay
  readable;
* the score is disclosed as an experimental, synthetic-trained, assistive
  signal. It is not a clinical judgement.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.scheduling.models import Appointment
from ml.no_show.config import (
    DECISION_HIGH_RISK,
    DECISION_LOW_RISK,
    SYNTHETIC_DISCLOSURE,
)
from ml.no_show.features import encode_record
from ml.no_show.registry import (
    ArtifactVersionMismatch,
    active_model,
    load_artifact,
    predict_probabilities,
    round_probability,
)

from .features import extract_features
from .models import NoShowModel, Prediction
from .serializers import (
    NoShowModelSerializer,
    PredictionSerializer,
    PredictRequestSerializer,
    PredictResponseSerializer,
)

FACTORS_METHOD = "global_permutation_importance"
FACTORS_LIMIT = 5


def _load_model_artifact(model: NoShowModel):
    """Load the registry artifact, tolerating a host-specific stored path.

    ``artifact_path`` is recorded by the training script. A model trained on a
    workstation and served from a container therefore carries an absolute path
    that does not exist in the container. Fall back to the same filename under
    ``ML_ARTIFACT_DIR`` (the directory the image ships the artifact in) before
    giving up. The artifact's own scikit-learn version check still applies.
    """

    recorded = str(model.artifact_path or "").strip()
    if not recorded:
        raise OSError("the model row has no artifact path")
    candidates = [Path(recorded)]
    filename = re.split(r"[\\/]", recorded)[-1]
    fallback = Path(str(getattr(settings, "ML_ARTIFACT_DIR", ""))) / filename
    if filename and fallback not in candidates:
        candidates.append(fallback)
    for candidate in candidates:
        if candidate.is_file():
            return load_artifact(candidate)
    raise OSError("artifact not found")


class NoStoreMixin:
    """Predictions reference patients and must never be cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class MlScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        # No-show scoring is a scheduling aid, so it rides the schedule-read
        # permission; there is no separate ``ml.*`` permission in the registry.
        return ("scheduling.view",)

    def clinic(self, request):
        clinic = getattr(request, "clinic", None)
        if clinic is None:
            scope_error = getattr(request, "clinic_scope_error", None)
            if scope_error is not None:
                raise scope_error
            raise ApiError(
                "permission_denied",
                "You do not have permission to perform this action.",
                status_code=403,
            )
        return clinic


class ModelListView(MlScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=NoShowModelSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = NoShowModel.objects.filter(
            Q(clinic=clinic) | Q(clinic__isnull=True)
        ).order_by("-trained_at", "-id")
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            NoShowModelSerializer(page, many=True).data
        )


def _factors(model: NoShowModel, features: dict) -> list[dict]:
    """Top global importances paired with this appointment's values.

    These are the model's *global* permutation importances, not a
    per-prediction attribution. The response says so via ``factors_method``
    rather than presenting a surrogate as a causal explanation.
    """

    importance = (model.metrics_json or {}).get("feature_importance") or []
    factors = []
    for item in importance[:FACTORS_LIMIT]:
        name = item.get("feature")
        if name is None:
            continue
        factors.append(
            {
                "feature": name,
                "value": features.get(name),
                "importance": item.get("importance"),
            }
        )
    return factors


class PredictView(MlScopedView):
    @extend_schema(
        request=PredictRequestSerializer,
        responses={200: PredictResponseSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = PredictRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment_id = serializer.validated_data["appointment_id"]

        appointment = (
            Appointment.objects.select_related(
                "patient", "provider", "appointment_type", "location"
            )
            .filter(pk=appointment_id)
            .first()
        )
        if appointment is None:
            raise ApiError("not_found", "Not found.", status_code=404)

        model = active_model(clinic=clinic)
        if model is None:
            raise ApiError(
                "conflict",
                "No active no-show model is available.",
                status_code=409,
            )

        overrides = serializer.validated_data.get("features") or None
        features, missing = extract_features(appointment, overrides=overrides)
        if missing:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=422,
                field_errors={name: ["required for prediction"] for name in missing},
                details={"missing_features": missing},
            )

        try:
            artifact = _load_model_artifact(model)
        except ArtifactVersionMismatch as exc:
            raise ApiError(
                "provider_unavailable",
                "The active model cannot be loaded.",
                status_code=503,
                details={"reason": str(exc)},
            ) from exc
        except (OSError, ValueError) as exc:
            raise ApiError(
                "provider_unavailable",
                "The active model cannot be loaded.",
                status_code=503,
            ) from exc

        matrix = np.array([encode_record(features)], dtype=float)
        probability = round_probability(
            float(predict_probabilities(artifact, matrix)[0])
        )
        threshold = float(model.threshold)
        decision = (
            DECISION_HIGH_RISK if probability >= threshold else DECISION_LOW_RISK
        )
        factors = _factors(model, features)

        with transaction.atomic():
            prediction = Prediction.objects.create(
                clinic=clinic,
                appointment=appointment,
                patient=appointment.patient,
                model_version=model.version,
                probability=probability,
                threshold=threshold,
                decision=decision,
                factors_json=factors,
                is_synthetic=model.is_synthetic,
            )

        payload = {
            "probability": probability,
            "threshold": threshold,
            "decision": decision,
            "model_version": model.version,
            "factors": factors,
            "factors_method": FACTORS_METHOD,
            "synthetic_data": bool(model.is_synthetic),
            "disclosure": SYNTHETIC_DISCLOSURE,
            "prediction_id": prediction.pk,
        }
        return Response(payload, status=status.HTTP_200_OK)


class PredictionListView(MlScopedView):
    """Recent predictions for the clinic (keeps retired-model history visible)."""

    allowed_query_params = {"page", "page_size", "model_version", "appointment"}

    @extend_schema(responses=PredictionSerializer(many=True))
    def get(self, request):
        self.clinic(request)
        queryset = Prediction.objects.all().order_by("-created_at", "-id")
        model_version = (request.query_params.get("model_version") or "").strip()
        if model_version:
            queryset = queryset.filter(model_version=model_version)
        appointment = (request.query_params.get("appointment") or "").strip()
        if appointment:
            queryset = queryset.filter(appointment_id=appointment)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(PredictionSerializer(page, many=True).data)


__all__ = ["ModelListView", "PredictView", "PredictionListView"]
