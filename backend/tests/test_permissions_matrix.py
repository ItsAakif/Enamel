"""Full role x permission matrix (>= 40 assertions).

The expected grant map is transcribed from ARCHITECTURE.md section 5. A cell
with F/E/R/A is a grant; ``-`` is a deny. The suite runs every role against
every registry string (4 x 42 = 168 cases).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.clinics.models import Membership
from apps.clinics.permissions import (
    ALL_PERMISSIONS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    permissions_for,
)
from apps.core.errors import ApiError
from apps.core.permissions import require_perm

PERMISSION_LIST = [
    "clinic.manage",
    "clinic.view",
    "members.manage",
    "audit.view",
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
    "booking.settings",
    "waitlist.manage",
    "messaging.view",
    "messaging.manage",
    "recall.manage",
    "plans.view",
    "plans.edit",
    "plans.approve",
    "consent.templates",
    "billing.view",
    "billing.manage",
    "billing.refund",
    "billing.settings",
    "portal.manage",
    "ai.use",
    "ai.configure",
    "imaging.view",
    "imaging.upload",
    "imaging.findings.accept",
    "marketing.view",
    "marketing.manage",
    "marketing.approve",
    "rcm.view",
    "rcm.manage",
    "analytics.view",
    "integrations.manage",
]

OWNER = set(PERMISSION_LIST)

DENTIST = {
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

RECEPTIONIST = {
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

ASSISTANT = {
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

EXPECTED = {
    "owner": OWNER,
    "dentist": DENTIST,
    "receptionist": RECEPTIONIST,
    "assistant": ASSISTANT,
}


def make_membership(role: str, *, overrides=None, status="active", user_id=None):
    user = SimpleNamespace(id=user_id or uuid.uuid4(), is_authenticated=True)
    membership = Membership(
        role=role,
        status=status,
        permission_overrides=overrides or [],
        user_id=user.id,
    )
    return user, membership


@pytest.mark.parametrize("role", sorted(EXPECTED))
@pytest.mark.parametrize("permission", PERMISSION_LIST)
def test_role_permission_matrix(role: str, permission: str):
    allowed = permission in EXPECTED[role]
    user, membership = make_membership(role)

    if allowed:
        assert require_perm(user, membership, permission) is True
        assert permission in membership.effective_permissions()
    else:
        with pytest.raises(ApiError) as excinfo:
            require_perm(user, membership, permission)
        assert excinfo.value.code == "permission_denied"
        assert excinfo.value.status_code == 403


def test_registry_matches_architecture_strings():
    assert len(PERMISSION_LIST) == 42
    assert set(PERMISSION_LIST) == ALL_PERMISSIONS
    assert set(PERMISSIONS) == ALL_PERMISSIONS
    assert len(PERMISSIONS) == 42


def test_role_map_matches_expected_matrix():
    assert {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()} == {
        role: set(perms) for role, perms in EXPECTED.items()
    }


def test_every_permission_is_granted_to_at_least_one_role():
    assert all(
        any(permission in EXPECTED[role] for role in EXPECTED)
        for permission in PERMISSION_LIST
    )


def test_at_least_thirty_permissions_have_a_denying_role():
    with_denial = [
        permission
        for permission in PERMISSION_LIST
        if any(permission not in EXPECTED[role] for role in EXPECTED)
    ]
    # Nine capabilities are granted to every staff role by the architecture
    # matrix; the remaining 33 have at least one denying role.
    assert len(with_denial) == 33


def test_overrides_only_grant():
    user, membership = make_membership("assistant", overrides=["patients.edit"])

    assert require_perm(user, membership, "patients.edit") is True
    assert "patients.edit" in membership.effective_permissions()
    with pytest.raises(ApiError):
        require_perm(user, membership, "members.manage")


def test_inactive_membership_denies_everything():
    user, membership = make_membership("owner", status="suspended")

    with pytest.raises(ApiError) as excinfo:
        require_perm(user, membership, "clinic.view")
    assert excinfo.value.code == "permission_denied"


def test_membership_must_belong_to_the_user():
    _, membership = make_membership("owner")
    other_user = SimpleNamespace(id=uuid.uuid4(), is_authenticated=True)

    with pytest.raises(ApiError):
        require_perm(other_user, membership, "clinic.view")


def test_unknown_role_has_no_permissions():
    assert permissions_for("intruder") == frozenset()
    assert permissions_for("assistant", ["clinic.manage"]) == frozenset(
        {*ASSISTANT, "clinic.manage"}
    )
