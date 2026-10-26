"""Code-defined permission registry and fixed role map.

The strings and the role matrix are copied verbatim from
``ARCHITECTURE.md`` section 5. The matrix uses F(ull), E(dit), R(ead) and
A(pprove); the registry has one string per capability, so any non-``-`` cell is
a grant and approval workflows (where they exist) decide the extra step. The
role map is intentionally immutable: per-membership overrides may only add
permissions, never remove them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Registry version. Bump when a permission is renamed or removed.
PERMISSION_REGISTRY_VERSION = 1

PERMISSIONS: dict[str, str] = {
    "clinic.manage": "Manage clinic settings and locations",
    "clinic.view": "View clinic settings and locations",
    "members.manage": "Manage members, invitations and roles",
    "audit.view": "View and export the audit log",
    "patients.view": "View patients",
    "patients.edit": "Create and edit patients",
    "patients.delete": "Delete or archive patients",
    "patients.export": "Export patient data",
    "clinical.history.view": "View medical history and allergies",
    "clinical.history.edit": "Edit medical history and allergies",
    "encounters.edit": "Create and edit encounters and clinical notes",
    "charting.view": "View the odontogram",
    "charting.edit": "Edit the odontogram",
    "scheduling.view": "View the schedule",
    "scheduling.manage": "Create, edit and cancel appointments",
    "scheduling.types.manage": "Manage appointment types and availability rules",
    "booking.settings": "Manage public booking settings",
    "waitlist.manage": "Manage waitlist entries and offers",
    "messaging.view": "View reminders and message templates",
    "messaging.manage": "Edit reminders and message templates",
    "recall.manage": "Manage recall rules",
    "plans.view": "View treatment plans",
    "plans.edit": "Create and edit treatment plans",
    "plans.approve": "Present and accept treatment plans on behalf",
    "consent.templates": "Manage consent templates",
    "billing.view": "View invoices and payments",
    "billing.manage": "Create invoices and record payments",
    "billing.refund": "Approve refunds",
    "billing.settings": "Manage payment settings",
    "portal.manage": "Administer portal patients",
    "ai.use": "Use the AI assistant",
    "ai.configure": "Configure AI providers and prompts",
    "imaging.view": "View imaging studies",
    "imaging.upload": "Upload imaging studies",
    "imaging.findings.accept": "Accept imaging findings",
    "marketing.view": "View marketing campaigns",
    "marketing.manage": "Create and edit marketing campaigns",
    "marketing.approve": "Approve marketing campaigns",
    "rcm.view": "View RCM and claims",
    "rcm.manage": "Manage RCM and claims",
    "analytics.view": "View analytics and reports",
    "integrations.manage": "Manage integrations and connectors",
}

ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

ROLE_OWNER = "owner"
ROLE_DENTIST = "dentist"
ROLE_RECEPTIONIST = "receptionist"
ROLE_ASSISTANT = "assistant"
ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_DENTIST, ROLE_RECEPTIONIST, ROLE_ASSISTANT)

_DENTIST: frozenset[str] = frozenset(
    {
        "clinic.view",
        "patients.view",
        "patients.edit",
        "patients.delete",
        "patients.export",
        "clinical.history.view",
        "clinical.history.edit",
        "encounters.edit",
        "charting.view",
        "charting.edit",
        "scheduling.view",
        "scheduling.manage",
        "scheduling.types.manage",
        "waitlist.manage",
        "messaging.view",
        "recall.manage",
        "plans.view",
        "plans.edit",
        "plans.approve",
        "consent.templates",
        "billing.view",
        "portal.manage",
        "ai.use",
        "imaging.view",
        "imaging.upload",
        "imaging.findings.accept",
        "rcm.view",
        "analytics.view",
    }
)

_RECEPTIONIST: frozenset[str] = frozenset(
    {
        "clinic.view",
        "patients.view",
        "patients.edit",
        "clinical.history.view",
        "clinical.history.edit",
        "charting.view",
        "scheduling.view",
        "scheduling.manage",
        "scheduling.types.manage",
        "booking.settings",
        "waitlist.manage",
        "messaging.view",
        "messaging.manage",
        "recall.manage",
        "plans.view",
        "billing.view",
        "billing.manage",
        "billing.refund",
        "portal.manage",
        "ai.use",
        "imaging.view",
        "imaging.upload",
        "marketing.view",
        "marketing.approve",
        "rcm.view",
        "rcm.manage",
        "analytics.view",
    }
)

_ASSISTANT: frozenset[str] = frozenset(
    {
        "clinic.view",
        "patients.view",
        "clinical.history.view",
        "charting.view",
        "charting.edit",
        "scheduling.view",
        "plans.view",
        "ai.use",
        "imaging.view",
        "imaging.upload",
    }
)

ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    ROLE_OWNER: ALL_PERMISSIONS,
    ROLE_DENTIST: _DENTIST,
    ROLE_RECEPTIONIST: _RECEPTIONIST,
    ROLE_ASSISTANT: _ASSISTANT,
}


def permissions_for(role: str, overrides: Iterable[str] | None = None) -> frozenset[str]:
    """Return the effective permission set for a role plus overrides.

    Unknown roles are denied everything. Overrides can only broaden the set;
    unknown permission strings are rejected by the serializer before they
    reach this function.
    """

    base = ROLE_PERMISSIONS.get(role, frozenset())
    if not overrides:
        return frozenset(base)
    return frozenset(base | {str(item) for item in overrides})


def validate_overrides(overrides: Iterable[str]) -> list[str]:
    """Validate grant-only overrides and return a sorted, de-duplicated list."""

    values = sorted({str(item) for item in overrides})
    unknown = [item for item in values if item not in ALL_PERMISSIONS]
    if unknown:
        raise ValueError(f"Unknown permission(s): {', '.join(unknown)}")
    return values


__all__ = [
    "ALL_PERMISSIONS",
    "PERMISSIONS",
    "PERMISSION_REGISTRY_VERSION",
    "ROLES",
    "ROLE_ASSISTANT",
    "ROLE_DENTIST",
    "ROLE_OWNER",
    "ROLE_PERMISSIONS",
    "ROLE_RECEPTIONIST",
    "permissions_for",
    "validate_overrides",
]
