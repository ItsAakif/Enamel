"""Admin registrations for core models.

``AuditEvent`` is registered read-only: an operator may inspect the log in
Django admin but has no add/change/delete path there, matching the model's
append-only guard and ARCHITECTURE.md section 10.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "clinic", "actor", "action", "entity_type", "entity_id")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "action")
    ordering = ("-id",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # Read-only: Django renders every field as readonly when this is False.
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
