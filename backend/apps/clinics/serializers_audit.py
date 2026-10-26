"""Serializers for the clinic audit log endpoint."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    """Read-only projection of one audit row.

    ``ip_hash`` is a salted digest, not an address, but it is still omitted:
    the endpoint's job is to show *what changed*, and a stable per-IP hash is a
    tracking identifier. ``hash``/``prev_hash`` are included so a client can
    spot-check the chain without a second endpoint.
    """

    actor_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "actor_id",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "request_id",
            "prev_hash",
            "hash",
            "created_at",
        ]
        read_only_fields = fields
