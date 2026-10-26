"""ML API serializers (T131).

The model list never exposes the artifact filesystem path (a server detail).
Every response that carries a score also carries ``synthetic_data`` and the
disclosure string, so a client cannot render a number without the caveat.
"""

from __future__ import annotations

from rest_framework import serializers

from ml.no_show.config import SYNTHETIC_DISCLOSURE

from .models import NoShowModel, Prediction


class NoShowModelSerializer(serializers.ModelSerializer):
    synthetic_data = serializers.BooleanField(source="is_synthetic", read_only=True)
    metrics_summary = serializers.SerializerMethodField()

    class Meta:
        model = NoShowModel
        fields = [
            "id",
            "clinic",
            "version",
            "algorithm",
            "status",
            "threshold",
            "dataset_hash",
            "metrics_hash",
            "sklearn_version",
            "features",
            "synthetic_data",
            "trained_at",
            "metrics_summary",
        ]

    def get_metrics_summary(self, obj: NoShowModel) -> dict:
        metrics = obj.metrics_json or {}
        candidate = metrics.get("candidate") or {}
        baseline = (metrics.get("baseline") or {}).get("logistic_regression") or {}
        dataset = metrics.get("dataset") or {}
        gate = metrics.get("activation_gate") or {}
        return {
            "candidate_roc_auc": candidate.get("roc_auc"),
            "candidate_brier": candidate.get("brier"),
            "baseline_roc_auc": baseline.get("roc_auc"),
            "baseline_brier": baseline.get("brier"),
            "activation_decision": gate.get("decision"),
            "calibration_error": metrics.get("calibration_error"),
            "dataset_n": dataset.get("n"),
            "dataset_no_show_rate": dataset.get("no_show_rate"),
            "disclosure": metrics.get("disclosure") or SYNTHETIC_DISCLOSURE,
        }


class PredictRequestSerializer(serializers.Serializer):
    appointment_id = serializers.UUIDField()
    #: Values the clinic has not persisted yet. Anything neither derived nor
    #: supplied is reported as missing (422), never imputed.
    features = serializers.DictField(required=False, allow_null=True)


class PredictionSerializer(serializers.ModelSerializer):
    synthetic_data = serializers.BooleanField(source="is_synthetic", read_only=True)

    class Meta:
        model = Prediction
        fields = [
            "id",
            "clinic",
            "appointment",
            "patient",
            "model_version",
            "probability",
            "threshold",
            "decision",
            "factors_json",
            "synthetic_data",
            "created_at",
        ]


class PredictResponseSerializer(serializers.Serializer):
    probability = serializers.FloatField()
    threshold = serializers.FloatField()
    decision = serializers.CharField()
    model_version = serializers.CharField()
    factors = serializers.ListField(child=serializers.DictField())
    factors_method = serializers.CharField()
    synthetic_data = serializers.BooleanField()
    disclosure = serializers.CharField()
    prediction_id = serializers.UUIDField()


__all__ = [
    "NoShowModelSerializer",
    "PredictRequestSerializer",
    "PredictResponseSerializer",
    "PredictionSerializer",
]
