"""Portal authentication (T121).

``PatientSessionAuthentication`` reads **only** the ``enamel_portal`` cookie. It
never consults the staff session (``enamel_session``), so a staff cookie cannot
authenticate a portal endpoint; and because it is not in the project's default
``DEFAULT_AUTHENTICATION_CLASSES``, a portal cookie can never authenticate a
``/app`` endpoint either.

On success it stores a :class:`PortalIdentity` on ``request.identity`` and returns
a :class:`PortalPrincipal` as ``request.user``. Views resolve every object
through ``request.identity`` (patient id + clinic id), never from a request body.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from rest_framework.authentication import BaseAuthentication

from apps.core.rls import set_rls_context

from .services import identity as identity_service


@dataclass(frozen=True)
class PortalIdentity:
    """The authenticated patient principal for one request."""

    patient_id: uuid.UUID
    clinic_id: uuid.UUID
    session_id: uuid.UUID


class PortalPrincipal:
    """A minimal DRF user object for a portal request.

    It is deliberately *not* a Django ``User``: the portal principal has no
    memberships, no roles and no staff powers.
    """

    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False

    def __init__(self, identity: PortalIdentity) -> None:
        self.identity = identity
        self.pk = identity.patient_id

    def __str__(self) -> str:
        return f"portal:{self.pk}"


class PatientSessionAuthentication(BaseAuthentication):
    """Authenticate a portal request from the ``enamel_portal`` cookie."""

    def authenticate(self, request: Any):
        raw = request.COOKIES.get(identity_service.cookie_name())
        if not raw:
            return None
        session = identity_service.resolve_session(raw)
        if session is None:
            return None
        identity = PortalIdentity(
            patient_id=session.patient_id,
            clinic_id=session.clinic_id,
            session_id=session.pk,
        )
        # The portal context is completed here (T137): the session row was
        # resolved by the key-hash capability, and from now on the request
        # carries the clinic and the patient, which is what the patient-scoped
        # database policies match on.
        set_rls_context(
            clinic_id=session.clinic_id,
            portal_patient_id=session.patient_id,
        )
        request.identity = identity
        request.portal_session = session
        return (PortalPrincipal(identity), session)

    def authenticate_header(self, request: Any) -> str:
        # Returning a header makes DRF answer 401 (not 403) when unauthenticated.
        return "PortalSession"


__all__ = [
    "PatientSessionAuthentication",
    "PortalIdentity",
    "PortalPrincipal",
]
