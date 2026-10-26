"""Interop serializers (T130).

The connector serializer never exposes ``credentials_enc``: the API reports only
``has_credentials`` (a boolean) so a client can tell whether a live connector is
configured without ever receiving the ciphertext.
"""

from __future__ import annotations

from rest_framework import serializers

from .credentials import encrypt_credentials
from .models import (
    CONNECTOR_KIND_CHOICES,
    CONNECTOR_MODE_CHOICES,
    ConnectorConfig,
    SyncConflict,
    SyncRun,
)


class ConnectorConfigSerializer(serializers.ModelSerializer):
    """Read projection of a connector; credentials are never serialised."""

    has_credentials = serializers.SerializerMethodField()

    class Meta:
        model = ConnectorConfig
        fields = [
            "id",
            "kind",
            "name",
            "mode",
            "base_url",
            "is_active",
            "has_credentials",
            "settings_json",
            "last_sync_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_has_credentials(self, obj: ConnectorConfig) -> bool:
        return bool(str(obj.credentials_enc or "").strip())


class ConnectorConfigCreateSerializer(serializers.Serializer):
    """Write input for a connector; ``credentials`` is encrypted on save."""

    kind = serializers.ChoiceField(choices=CONNECTOR_KIND_CHOICES, required=False)
    name = serializers.CharField(max_length=255)
    mode = serializers.ChoiceField(choices=CONNECTOR_MODE_CHOICES, required=False)
    base_url = serializers.CharField(required=False, allow_blank=True, default="")
    credentials = serializers.DictField(required=False, write_only=True)
    is_active = serializers.BooleanField(required=False, default=True)
    settings_json = serializers.DictField(required=False, default=dict)

    def create(self, validated_data):
        credentials = validated_data.pop("credentials", None) or {}
        connector = ConnectorConfig(
            **validated_data,
            credentials_enc=encrypt_credentials(credentials),
        )
        connector.save()
        return connector


class SyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncRun
        fields = [
            "id",
            "connector",
            "direction",
            "status",
            "started_at",
            "finished_at",
            "counts_json",
            "error_code",
            "cursor",
        ]
        read_only_fields = fields


class SyncConflictSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncConflict
        fields = [
            "id",
            "connector",
            "entity_type",
            "external_id",
            "internal_id",
            "reason",
            "status",
            "created_at",
        ]
        read_only_fields = fields


__all__ = [
    "ConnectorConfigCreateSerializer",
    "ConnectorConfigSerializer",
    "SyncConflictSerializer",
    "SyncRunSerializer",
]
