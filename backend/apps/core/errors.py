"""RFC 9457-shaped API errors with stable machine codes.

Every API error is rendered by one handler so negative paths look the same and
never include tenant entity data. The envelope is::

    {"error": {"code", "message", "field_errors", "request_id", "details"}}

``request_id`` comes from ``apps.core.middleware.RequestIdMiddleware``.

Unhandled exceptions are also converted here. A 500 response is always the
generic ``internal_error`` envelope: exception messages and tracebacks are
never serialised to the client.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("enamel.api")

# Stable API error codes (ARCHITECTURE.md section 7 and T101/T102 additions).
#
# This module is the single source of truth for the code list: the OpenAPI
# ``CodeEnum`` component is derived from ``ERROR_CODES`` (see
# ``apps.core.schema``), and ``apps.core.tests.test_error_contract`` proves
# that every code the runtime raises is listed here.
CODE_VALIDATION = "validation_error"
CODE_UNAUTHENTICATED = "unauthenticated"
CODE_SESSION_EXPIRED = "session_expired"
CODE_PERMISSION_DENIED = "permission_denied"
CODE_NOT_FOUND = "not_found"
CODE_CONFLICT = "conflict"
CODE_SLOT_CONFLICT = "slot_conflict"
CODE_VERSION_CONFLICT = "version_conflict"
CODE_PAYLOAD_TOO_LARGE = "payload_too_large"
CODE_THROTTLED = "throttled"
CODE_PROVIDER_UNAVAILABLE = "provider_unavailable"
CODE_INTERNAL = "internal_error"
CODE_IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
CODE_INVALID_PAGE = "invalid_page"
CODE_INVALID_TRANSITION = "invalid_transition"
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
CODE_UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
CODE_INVALID_CREDENTIALS = "invalid_credentials"
CODE_MFA_REQUIRED = "mfa_required"
CODE_INVALID_OTP = "invalid_otp"
CODE_CLINIC_REQUIRED = "clinic_required"
CODE_LAST_OWNER = "last_owner"
CODE_INVITATION_EXPIRED = "invitation_expired"
CODE_INVITATION_USED = "invitation_used"
CODE_INVITATION_REVOKED = "invitation_revoked"
CODE_INVITATION_EMAIL_MISMATCH = "invitation_email_mismatch"
CODE_OVERPAYMENT = "overpayment"
CODE_REFUND_EXCEEDS_CAPTURED = "refund_exceeds_captured"
CODE_INVALID_SIGNATURE = "invalid_signature"
CODE_INVALID_TOKEN = "invalid_token"
CODE_EXPIRED = "expired"
CODE_PASSWORD_REUSE = "password_reuse"
CODE_MFA_ALREADY_ENABLED = "mfa_already_enabled"
CODE_MFA_NOT_STARTED = "mfa_not_started"
CODE_ALREADY_MEMBER = "already_member"
CODE_POLICY_CUTOFF = "policy_cutoff"
CODE_PATIENT_BOOKING_LIMIT = "patient_booking_limit"
CODE_HOLD_EXPIRED = "hold_expired"
CODE_OFFER_UNAVAILABLE = "offer_unavailable"
CODE_INVALID_CODE = "invalid_code"
CODE_DUPLICATE_CANDIDATES = "duplicate_candidates"
CODE_PATIENT_ARCHIVED = "patient_archived"
CODE_PLAN_ITEM_MISSING = "plan_item_missing"
CODE_INVALID_DICOM = "invalid_dicom"
CODE_CONNECTOR_INACTIVE = "connector_inactive"
CODE_CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
CODE_CONNECTOR_BASE_URL_BLOCKED = "connector_base_url_blocked"
CODE_INVALID_ARGUMENTS = "invalid_arguments"
CODE_CONFIRMATION_REQUIRED = "confirmation_required"
CODE_CONFIRMATION_INVALID = "confirmation_invalid"
CODE_IDENTITY_REQUIRED = "identity_required"
CODE_UNKNOWN_TOOL = "unknown_tool"
CODE_NO_PROVIDER = "no_provider"
CODE_NO_LOCATION = "no_location"
CODE_BUDGET_EXCEEDED = "budget_exceeded"


class AuditImmutableError(RuntimeError):
    """Raised when code attempts to update or delete an audit row.

    The audit log is append-only: the only legal write is the insert performed
    by ``core.audit.record``. The DB-level trigger that makes this true for
    every writer lands in T135; this exception is the application-level guard.
    """

# Public list used by the OpenAPI error schema. This tuple is the single
# source of truth: the ``CodeEnum`` component in the published document is
# derived from it, never duplicated. Keep in sync with the constants above and
# ARCHITECTURE.md section 7.
ERROR_CODES: tuple[str, ...] = (
    # Generic envelope codes (also produced by ``_generic_code``).
    CODE_VALIDATION,
    CODE_UNAUTHENTICATED,
    CODE_SESSION_EXPIRED,
    CODE_PERMISSION_DENIED,
    CODE_NOT_FOUND,
    CODE_CONFLICT,
    CODE_SLOT_CONFLICT,
    CODE_VERSION_CONFLICT,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_THROTTLED,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_INTERNAL,
    CODE_IDEMPOTENCY_MISMATCH,
    CODE_INVALID_PAGE,
    CODE_INVALID_TRANSITION,
    CODE_METHOD_NOT_ALLOWED,
    CODE_UNSUPPORTED_MEDIA_TYPE,
    CODE_CLINIC_REQUIRED,
    # Accounts, sessions and invitations.
    CODE_INVALID_CREDENTIALS,
    CODE_MFA_REQUIRED,
    CODE_MFA_ALREADY_ENABLED,
    CODE_MFA_NOT_STARTED,
    CODE_INVALID_OTP,
    CODE_INVALID_TOKEN,
    CODE_EXPIRED,
    CODE_PASSWORD_REUSE,
    CODE_LAST_OWNER,
    CODE_INVITATION_EXPIRED,
    CODE_INVITATION_USED,
    CODE_INVITATION_REVOKED,
    CODE_INVITATION_EMAIL_MISMATCH,
    CODE_ALREADY_MEMBER,
    # Booking and scheduling.
    CODE_POLICY_CUTOFF,
    CODE_PATIENT_BOOKING_LIMIT,
    CODE_HOLD_EXPIRED,
    CODE_OFFER_UNAVAILABLE,
    CODE_INVALID_CODE,
    # Billing and payments.
    CODE_OVERPAYMENT,
    CODE_REFUND_EXCEEDS_CAPTURED,
    CODE_INVALID_SIGNATURE,
    # Patients, charting and imaging.
    CODE_DUPLICATE_CANDIDATES,
    CODE_PATIENT_ARCHIVED,
    CODE_PLAN_ITEM_MISSING,
    CODE_INVALID_DICOM,
    # Interop connectors.
    CODE_CONNECTOR_INACTIVE,
    CODE_CONNECTOR_NOT_CONFIGURED,
    CODE_CONNECTOR_BASE_URL_BLOCKED,
    # AI assistant and voice tools.
    CODE_INVALID_ARGUMENTS,
    CODE_CONFIRMATION_REQUIRED,
    CODE_CONFIRMATION_INVALID,
    CODE_IDENTITY_REQUIRED,
    CODE_UNKNOWN_TOOL,
    CODE_NO_PROVIDER,
    CODE_NO_LOCATION,
    CODE_BUDGET_EXCEEDED,
)


class ApiError(drf_exceptions.APIException):
    """API exception carrying a stable code and no tenant data."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        field_errors: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.field_errors = field_errors or {}
        self.details = details or {}
        super().__init__(detail=message)


def _plain(value: Any) -> Any:
    """Convert DRF ``ErrorDetail`` trees into plain JSON values."""

    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _field_errors(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        return {str(key): _plain(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return {"non_field_errors": _plain(detail)}
    return {"non_field_errors": [_plain(detail)]}


def error_payload(
    request: HttpRequest | None,
    code: str,
    message: str,
    *,
    field_errors: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = getattr(request, "request_id", None) if request is not None else None
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or {},
            "request_id": request_id,
            "details": details or {},
        }
    }


def error_response(
    request: HttpRequest | None,
    code: str,
    message: str,
    *,
    status_code: int,
    field_errors: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> JsonResponse:
    response = JsonResponse(
        error_payload(
            request,
            code,
            message,
            field_errors=field_errors,
            details=details,
        ),
        status=status_code,
    )
    response["Cache-Control"] = "no-store"
    return response


def api_not_found(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    """``handler404`` for paths that never resolve to a DRF view.

    Without this, an unknown ``/api/`` URL falls through to Django's HTML 404
    and clients cannot rely on one envelope. The path is deliberately not
    echoed back: it can contain identifiers.
    """

    return error_response(
        request,
        CODE_NOT_FOUND,
        "Not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def api_server_error(request: HttpRequest) -> JsonResponse:
    """``handler500`` for failures outside the DRF exception handler."""

    logger.error(
        "api_server_error",
        extra={
            "request_id": getattr(request, "request_id", None),
            "path": getattr(request, "path", None),
        },
    )
    return error_response(
        request,
        CODE_INTERNAL,
        "An internal error occurred.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _generic_code(status_code: int) -> tuple[str, str]:
    mapping = {
        400: (CODE_VALIDATION, "The request could not be processed."),
        401: (CODE_UNAUTHENTICATED, "Authentication is required."),
        403: (CODE_PERMISSION_DENIED, "You do not have permission to perform this action."),
        404: (CODE_NOT_FOUND, "Not found."),
        405: (CODE_METHOD_NOT_ALLOWED, "Method not allowed."),
        409: (CODE_CONFLICT, "The request conflicts with the current state."),
        413: (CODE_PAYLOAD_TOO_LARGE, "The request payload is too large."),
        415: (CODE_UNSUPPORTED_MEDIA_TYPE, "Unsupported media type."),
        429: (CODE_THROTTLED, "Too many requests."),
        500: (CODE_INTERNAL, "An internal error occurred."),
        503: (CODE_PROVIDER_UNAVAILABLE, "A provider is temporarily unavailable."),
    }
    return mapping.get(status_code, (CODE_INTERNAL, "An internal error occurred."))


def _finalise(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    return response


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    """DRF ``EXCEPTION_HANDLER`` entry point.

    Always returns a response. Non-DRF exceptions become the generic 500
    envelope instead of Django's HTML error page, so API clients can rely on a
    single response shape and no internal detail leaks.
    """

    request = context.get("request")

    if isinstance(exc, RequestDataTooBig):
        return _finalise(
            Response(
                error_payload(
                    request,
                    CODE_PAYLOAD_TOO_LARGE,
                    "The request payload is too large.",
                ),
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        )

    if isinstance(exc, ApiError):
        response = Response(
            error_payload(
                request,
                exc.code,
                exc.message,
                field_errors=exc.field_errors,
                details=exc.details,
            ),
            status=exc.status_code,
        )
        if exc.status_code == 429:
            retry_after = (exc.details or {}).get("retry_after")
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                response["Retry-After"] = str(int(math.ceil(retry_after)))
        return _finalise(response)

    response = drf_exception_handler(exc, context)
    if response is None:
        # Do not log the message or traceback: it may contain PHI. The class
        # name is enough to triage while the client gets a stable envelope.
        logger.error(
            "api_unhandled_exception",
            extra={
                "request_id": getattr(request, "request_id", None),
                "path": getattr(request, "path", None),
                "exception": exc.__class__.__name__,
            },
        )
        return _finalise(
            Response(
                error_payload(request, CODE_INTERNAL, "An internal error occurred."),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        )

    status_code = response.status_code

    if isinstance(exc, drf_exceptions.ValidationError):
        code, message = CODE_VALIDATION, "The request could not be processed."
        field_errors = _field_errors(exc.detail)
        details: dict[str, Any] = {}
    elif isinstance(exc, (drf_exceptions.NotAuthenticated, drf_exceptions.AuthenticationFailed)):
        code, message = CODE_UNAUTHENTICATED, "Authentication is required."
        # SessionAuthentication has no WWW-Authenticate header, so DRF would
        # downgrade "not authenticated" to 403. The API contract is 401.
        status_code = 401
        field_errors, details = {}, {}
    elif isinstance(exc, drf_exceptions.PermissionDenied):
        code, message = (
            CODE_PERMISSION_DENIED,
            "You do not have permission to perform this action.",
        )
        field_errors, details = {}, {}
    elif isinstance(exc, drf_exceptions.NotFound):
        code, message = CODE_NOT_FOUND, "Not found."
        field_errors, details = {}, {}
    elif isinstance(exc, drf_exceptions.Throttled):
        code, message = CODE_THROTTLED, "Too many requests. Try again later."
        field_errors = {}
        details = {"retry_after": exc.wait} if getattr(exc, "wait", None) else {}
    else:
        code, message = _generic_code(status_code)
        field_errors = {}
        details = {}

    response.data = error_payload(
        request,
        code,
        message,
        field_errors=field_errors,
        details=details,
    )
    response.status_code = status_code
    return _finalise(response)
