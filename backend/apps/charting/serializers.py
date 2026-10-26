"""Serializers for the odontogram API (T108).

Write serializers are explicit ``Serializer`` subclasses, never
``ModelSerializer``: the accepted input is exactly the documented field list, so
a crafted payload cannot mass-assign ``clinic``, ``patient`` or ``version``.
Fields are optional *without* a default so the batch service can tell "not
provided" (keep the current value) from "provided as empty" (clear it).

The response serializers exist for the OpenAPI document; the actual payloads
are built by the service layer from model instances.
"""

from __future__ import annotations

from rest_framework import serializers

from . import fdi, validators


class ChartBatchChangeSerializer(serializers.Serializer):
    """One change in a batch save."""

    tooth = serializers.IntegerField()
    condition_code = serializers.CharField(max_length=64)
    status = serializers.ChoiceField(choices=validators.STATUS_CHOICES, required=False)
    surfaces = serializers.ListField(
        child=serializers.CharField(max_length=1),
        required=False,
    )
    severity = serializers.IntegerField(required=False, allow_null=True)
    material = serializers.CharField(max_length=64, required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    version = serializers.IntegerField(required=False, allow_null=True)
    archive = serializers.BooleanField(required=False)


class ChartBatchRequestSerializer(serializers.Serializer):
    changes = ChartBatchChangeSerializer(many=True, allow_empty=False)
    idempotency_key = serializers.CharField(max_length=255)
    dentition = serializers.ChoiceField(choices=fdi.CHART_DENTITION_CHOICES, required=False)
    force = serializers.BooleanField(required=False, default=False)


class ChartStateTransitionSerializer(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=validators.TRANSITION_TARGETS)
    plan_item_id = serializers.UUIDField(required=False, allow_null=True)


class ToothInfoSerializer(serializers.Serializer):
    code = serializers.IntegerField()
    quadrant = serializers.IntegerField()
    arch = serializers.CharField()
    laterality = serializers.CharField()
    type = serializers.CharField()
    dentition = serializers.CharField()
    is_anterior = serializers.BooleanField()
    label = serializers.CharField()


class ToothStateSerializer(serializers.Serializer):
    id = serializers.CharField()
    tooth = serializers.IntegerField()
    condition_code = serializers.CharField()
    status = serializers.CharField()
    surfaces = serializers.ListField(child=serializers.CharField())
    severity = serializers.IntegerField(allow_null=True)
    material = serializers.CharField()
    note = serializers.CharField()
    version = serializers.IntegerField()
    plan_item_id = serializers.CharField(allow_null=True)


class ChartToothSerializer(serializers.Serializer):
    tooth = serializers.IntegerField()
    info = ToothInfoSerializer()
    states = ToothStateSerializer(many=True)


class ConditionLegendSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()
    category = serializers.CharField()
    default_status = serializers.CharField()
    surfaces_required = serializers.BooleanField()
    sort_order = serializers.IntegerField()


class ChartCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    by_status = serializers.DictField(child=serializers.IntegerField())
    by_condition = serializers.DictField(child=serializers.IntegerField())
    teeth_with_states = serializers.IntegerField()


class ChartResponseSerializer(serializers.Serializer):
    dentition = serializers.CharField()
    dentition_source = serializers.CharField()
    teeth = ChartToothSerializer(many=True)
    counts = ChartCountsSerializer()
    legend = ConditionLegendSerializer(many=True)


class ChartBatchAppliedSerializer(serializers.Serializer):
    tooth = serializers.IntegerField()
    condition_code = serializers.CharField()
    action = serializers.CharField()
    id = serializers.CharField(allow_null=True)
    version = serializers.IntegerField(allow_null=True)
    status = serializers.CharField(allow_null=True)


class ChartBatchResponseSerializer(serializers.Serializer):
    applied = ChartBatchAppliedSerializer(many=True)
    states = ToothStateSerializer(many=True)
    dentition = serializers.CharField()
    counts = ChartCountsSerializer()
    replayed = serializers.BooleanField()


class ChartHistoryEventSerializer(serializers.Serializer):
    id = serializers.CharField()
    tooth = serializers.IntegerField(allow_null=True)
    event_type = serializers.CharField()
    condition_code = serializers.CharField(allow_null=True)
    before = serializers.DictField(allow_null=True)
    after = serializers.DictField(allow_null=True)
    actor = serializers.CharField(allow_null=True)
    occurred_at = serializers.DateTimeField()
    version = serializers.IntegerField()
    batch_id = serializers.CharField(allow_null=True)


class ChartHistoryPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ChartHistoryEventSerializer(many=True)


__all__ = [
    "ChartBatchAppliedSerializer",
    "ChartBatchChangeSerializer",
    "ChartBatchRequestSerializer",
    "ChartBatchResponseSerializer",
    "ChartCountsSerializer",
    "ChartHistoryEventSerializer",
    "ChartHistoryPageSerializer",
    "ChartResponseSerializer",
    "ChartStateTransitionSerializer",
    "ChartToothSerializer",
    "ConditionLegendSerializer",
    "ToothInfoSerializer",
    "ToothStateSerializer",
]
