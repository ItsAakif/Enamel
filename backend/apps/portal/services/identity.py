"""Portal session lifecycle (T121).

A session is an opaque 32-byte urlsafe token stored only as its SHA-256. The
raw token lives in the ``enamel_portal`` cookie; the server resolves it by hash
with no clinic context, then re-checks the owning clinic and the patient on every
request. Resolution fails closed on a revoked or expired session.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.audit import hash_ip
from apps.patients.models import Patient

from ..models import PatientSession

SESSION_KEY_BYTES = 32
DEFAULT_TTL_HOURS = 12


def cookie_name() -> str:
    return str(getattr(settings, "PORTAL_SESSION_COOKIE_NAME", "enamel_portal"))


def session_ttl_hours() -> int:
    return int(getattr(settings, "PORTAL_SESSION_TTL_HOURS", DEFAULT_TTL_HOURS))


def generate_key() -> str:
    return secrets.token_urlsafe(SESSION_KEY_BYTES)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(str(raw_key or "").encode("utf-8")).hexdigest()


def _client_ip(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("REMOTE_ADDR") or "")


def _user_agent(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("HTTP_USER_AGENT") or "")[:255]


def create_session(
    patient: Patient,
    *,
    request: Any = None,
    now: Any = None,
    ttl_hours: int | None = None,
) -> tuple[PatientSession, str]:
    """Create a portal session and return ``(session, raw_key)``."""

    moment = now or timezone.now()
    hours = session_ttl_hours() if ttl_hours is None else int(ttl_hours)
    raw_key = generate_key()
    session = PatientSession.objects.create(
        patient=patient,
        clinic_id=patient.clinic_id,
        key_hash=hash_key(raw_key),
        expires_at=moment + timedelta(hours=hours),
        last_seen_at=moment,
        ip_hash=hash_ip(_client_ip(request)),
        user_agent=_user_agent(request),
    )
    return session, raw_key


def resolve_session(raw_key: str, *, now: Any = None, touch: bool = True) -> PatientSession | None:
    """Resolve a raw cookie value to a live session, or None."""

    key = str(raw_key or "").strip()
    if not key:
        return None
    session = PatientSession.objects.filter(key_hash=hash_key(key)).first()
    if session is None or session.revoked_at is not None:
        return None
    moment = now or timezone.now()
    if session.expires_at <= moment:
        return None
    if touch:
        PatientSession.objects.filter(pk=session.pk).update(last_seen_at=moment)
    return session


def revoke_session(session: PatientSession, *, now: Any = None) -> PatientSession:
    moment = now or timezone.now()
    session.revoked_at = moment
    session.save(update_fields=["revoked_at"])
    return session


def revoke_all_for_patient(patient: Patient, *, now: Any = None) -> int:
    moment = now or timezone.now()
    return PatientSession.objects.filter(patient=patient, revoked_at__isnull=True).update(
        revoked_at=moment
    )


__all__ = [
    "DEFAULT_TTL_HOURS",
    "SESSION_KEY_BYTES",
    "cookie_name",
    "create_session",
    "generate_key",
    "hash_key",
    "resolve_session",
    "revoke_all_for_patient",
    "revoke_session",
    "session_ttl_hours",
]
