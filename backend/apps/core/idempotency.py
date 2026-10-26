"""Idempotency for unsafe creates.

Usage::

    class ClinicCreateView(IdempotencyMixin, APIView):
        ...

The mixin activates only for unsafe create methods (POST by default) when the
caller sends an ``Idempotency-Key`` header. A PostgreSQL transaction-scoped
advisory lock serialises concurrent requests with the same
``(user, clinic, key)`` tuple, so exactly one request can perform side effects.
The second request either replays the stored response or receives a 409
``idempotency_mismatch`` when its canonical body hash differs.

Records expire after 24 hours. Expired rows for the requested key are deleted
before the lookup. Only the response that was already returned to the same user
is stored; request bodies and PHI are never persisted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from django.db import connection, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter
from rest_framework.response import Response

from .errors import CODE_IDEMPOTENCY_MISMATCH, CODE_VALIDATION, ApiError
from .models import IdempotencyRecord

IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_HEADER = "Idempotency-Key"

# Reusable OpenAPI parameter for views that opt into the contract. Keeping it
# here means every future money/booking/claim create documents the header the
# same way.
IDEMPOTENCY_KEY_PARAMETER = OpenApiParameter(
    name=IDEMPOTENCY_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Client-generated key that makes an unsafe create safe to retry. "
        "The stored response is replayed for 24 hours; reusing the key with a "
        "different body returns 409."
    ),
)


def canonical_request_hash(request) -> str:
    """Return a stable SHA-256 hash of the request body.

    JSON bodies are canonicalised (sorted keys, compact separators) so that key
    order does not cause a spurious mismatch. Non-JSON bodies are hashed as
    received.
    """

    content_type = (getattr(request, "content_type", "") or "").split(";")[0].strip().lower()
    raw = request.body or b""

    if content_type in {"application/json", "application/vnd.api+json"}:
        try:
            payload = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            payload = raw.decode("utf-8", "replace")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return hashlib.sha256(raw).hexdigest()


def _advisory_lock_ids(user_id: Any, clinic_id: Any, key: str) -> tuple[int, int]:
    digest = hashlib.sha256(f"{user_id}:{clinic_id}:{key}".encode()).digest()
    first = int.from_bytes(digest[:4], "big", signed=True)
    second = int.from_bytes(digest[4:8], "big", signed=True)
    return first, second


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def purge_expired(*, now=None) -> int:
    """Delete expired records. Intended for the daily cleanup job/tests.

    The sweep is cross-clinic, so it does not use the maintenance bypass: it
    walks the user identities (the identity table is outside the
    row-level-security scope by design) and deletes each user's expired
    records inside that user's **principal** context, which is the key the
    policy addresses (ADR-013, decision D-2).
    """

    from apps.accounts.models import User

    from .rls import rls_context

    moment = now or timezone.now()
    deleted = 0
    for user in User.objects.all().iterator():
        with rls_context(None, principal_id=user.pk):
            deleted += IdempotencyRecord.objects.filter(
                expires_at__lte=moment
            ).delete()[0]
    return deleted


class IdempotencyMixin:
    """Reusable DRF mixin implementing the ``Idempotency-Key`` contract.

    Opt-in ``idempotency_required = True`` for money/booking/claim creates where
    a missing key is a client error rather than a best-effort improvement.
    """

    idempotent_methods = frozenset({"POST"})
    idempotency_required = False

    def dispatch(self, request, *args, **kwargs):
        try:
            return self._dispatch_idempotent(request, *args, **kwargs)
        except ApiError as exc:
            # Contract violations raised by this mixin happen outside the
            # ``super().dispatch`` call, so DRF has not rendered them yet.
            if not hasattr(self, "headers"):
                self.headers = self.default_response_headers
            response = self.handle_exception(exc)
            return self.finalize_response(request, response, *args, **kwargs)

    def _dispatch_idempotent(self, request, *args, **kwargs):
        key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        user = getattr(request, "user", None)

        if (
            request.method in self.idempotent_methods
            and self.idempotency_required
            and not key
            and user is not None
            and getattr(user, "is_authenticated", False)
        ):
            raise ApiError(
                CODE_VALIDATION,
                f"The {IDEMPOTENCY_HEADER} header is required.",
                status_code=400,
                field_errors={IDEMPOTENCY_HEADER: ["this header is required"]},
            )

        if (
            request.method not in self.idempotent_methods
            or not key
            or user is None
            or not getattr(user, "is_authenticated", False)
        ):
            # Anonymous or non-create requests keep their normal semantics.
            return super().dispatch(request, *args, **kwargs)

        clinic = getattr(request, "clinic", None)
        clinic_id = getattr(clinic, "id", None)
        request_hash = canonical_request_hash(request)

        with transaction.atomic():
            lock_a, lock_b = _advisory_lock_ids(user.pk, clinic_id, key)
            with connection.cursor() as cursor:
                # Transaction-scoped: released automatically on commit/rollback.
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [lock_a, lock_b])

            now = timezone.now()
            expired = IdempotencyRecord.objects.filter(
                user=user,
                clinic_id=clinic_id,
                key=key,
                expires_at__lte=now,
            )
            expired.delete()

            record = (
                IdempotencyRecord.objects.filter(
                    user=user,
                    clinic_id=clinic_id,
                    key=key,
                )
                .order_by("-created_at")
                .first()
            )
            if record is not None:
                if record.request_hash != request_hash:
                    raise ApiError(
                        CODE_IDEMPOTENCY_MISMATCH,
                        "This idempotency key was already used with a different request body.",
                        status_code=409,
                        details={"key": key},
                    )
                if record.response_body is None:
                    response = Response(status=record.status_code)
                else:
                    response = Response(record.response_body, status=record.status_code)
                response["Idempotency-Replayed"] = "true"
                # We are bypassing the normal ``super().dispatch`` return path,
                # so content negotiation/finalisation must be applied here.
                # ``APIView.dispatch`` normally initialises ``self.headers``.
                if not hasattr(self, "headers"):
                    self.headers = self.default_response_headers
                return self.finalize_response(request, response, *args, **kwargs)

            response = super().dispatch(request, *args, **kwargs)

            if 200 <= response.status_code < 300:
                IdempotencyRecord.objects.create(
                    user=user,
                    clinic_id=clinic_id,
                    key=key,
                    method=request.method,
                    path=request.path[:512],
                    request_hash=request_hash,
                    status_code=response.status_code,
                    response_body=_json_safe(getattr(response, "data", None)),
                    expires_at=now + IDEMPOTENCY_TTL,
                )
            return response
