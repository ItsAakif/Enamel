"""Request id assignment and structured request logging.

Accepts a caller-supplied ``X-Request-Id`` (safely bounded) or generates a
UUIDv7-based id, stores it on ``request.request_id``, echoes it on the response
and emits one JSON log line per request. Request bodies, query strings and
headers are never logged.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from .errors import CODE_SESSION_EXPIRED, ApiError, error_response
from .rls import clear_rls_context, set_rls_context
from .tenancy import ClinicScope, reset_current_scope, set_current_scope

logger = logging.getLogger("enamel.request")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
REQUEST_ID_HEADER = "X-Request-Id"


def _request_id_from(request: HttpRequest) -> str:
    candidate = request.headers.get(REQUEST_ID_HEADER, "")
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return f"req_{uuid.uuid7().hex}"


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _request_id_from(request)
        request.request_id = request_id

        started = time.perf_counter()
        try:
            response = self.get_response(request)
        except Exception:
            logger.warning(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "outcome": "error",
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "outcome": "ok" if response.status_code < 500 else "error",
            },
        )
        return response


class SessionIdleMiddleware:
    """Enforce the 30 minute idle and 12 hour absolute staff session limits.

    Django's session cookie already carries the absolute age, but
    ``SESSION_SAVE_EVERY_REQUEST`` would otherwise slide it forward on every
    request. The login timestamp is therefore stored in the session and
    checked here. ``session_epoch`` revocation (password reset, role change,
    membership suspension) is enforced in the same place.

    API requests that fail either check receive a JSON ``session_expired``
    error; non-API requests are flushed and continue anonymously.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            # An expired database session is dropped before authentication, so
            # the user is anonymous while the browser still presents the cookie.
            # Report that state as an expired session instead of a generic 401.
            cookie = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
            if (
                cookie
                and request.session.session_key is None
                and request.path.startswith("/api/")
            ):
                return error_response(
                    request,
                    CODE_SESSION_EXPIRED,
                    "Your session has expired. Sign in again.",
                    status_code=401,
                )
            return self.get_response(request)

        session = request.session
        now = timezone.now().timestamp()
        idle_timeout = float(getattr(settings, "SESSION_IDLE_TIMEOUT", 1800))
        absolute_timeout = float(getattr(settings, "SESSION_ABSOLUTE_TIMEOUT", 43200))

        started = session.get("_session_started_at")
        if started is None:
            started = now
            session["_session_started_at"] = now

        expired = False
        if now - float(started) > absolute_timeout:
            expired = True
        else:
            last_activity = session.get("_last_activity")
            if last_activity is not None and now - float(last_activity) > idle_timeout:
                expired = True

        epoch = session.get("_session_epoch")
        if epoch is None:
            session["_session_epoch"] = user.session_epoch
        elif int(epoch) != int(user.session_epoch):
            expired = True

        if expired:
            session.flush()
            if request.path.startswith("/api/"):
                return error_response(
                    request,
                    CODE_SESSION_EXPIRED,
                    "Your session has expired. Sign in again.",
                    status_code=401,
                )
            from django.contrib.auth.models import AnonymousUser

            request.user = AnonymousUser()
            return self.get_response(request)

        session["_last_activity"] = now
        return self.get_response(request)


def log_unknown_query_params(request: HttpRequest) -> None:
    """Log endpoint query parameters not declared by the API contract.

    DEBUG only, once per request, names only (never values). Resolution is
    best-effort: an unroutable path keeps its normal 404 handling.
    """

    if not settings.DEBUG or not request.path.startswith("/api/"):
        return
    if request.method not in ("GET", "HEAD"):
        return

    from django.urls import Resolver404, resolve

    view = None
    try:
        match = resolve(request.path_info)
    except Resolver404:
        pass
    else:
        view = getattr(getattr(match, "func", None), "view_class", None)

    from .pagination import log_unknown_query_params as log_unknown

    log_unknown(request, view)


class UnknownQueryParamsMiddleware:
    """Best-effort DEBUG-only query parameter audit for API requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        log_unknown_query_params(request)
        return self.get_response(request)


class ClinicScopeMiddleware:
    """Resolve ``request.clinic`` / ``request.membership`` for the request.

    Resolution never raises to the client on its own: the error is stored on
    the request and raised by ``HasClinicPermission`` when the endpoint
    actually needs a clinic. This keeps ``/auth/me/`` usable while a stale
    header is still rejected by every clinic endpoint.

    The scope is resolved **inside** the request transaction and after
    ``app.current_principal_id`` has been set (T137). Both matter once the
    application runs as a non-superuser role: the membership lookup happens
    with no clinic context, so it can only be resolved by the principal key.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.clinic = None
        request.membership = None
        request.clinic_required = False
        request.clinic_scope_error = None

        # The liveness probe must answer even when the database is down, so
        # it never opens a transaction.
        if request.path_info.rstrip("/") == "/healthz":
            return self.get_response(request)

        user = getattr(request, "user", None)
        authenticated = user is not None and getattr(user, "is_authenticated", False)

        # Anonymous requests have no clinic context to apply. Public
        # booking/portal services enter their own ``with_clinic`` scope, so
        # skipping the transaction here keeps unauthenticated endpoints
        # (and their 401/404 responses) independent of database access.
        if not authenticated:
            return self.get_response(request)

        user_id = getattr(user, "pk", "") or ""

        with transaction.atomic():
            # The principal GUC is set before the clinic is resolved: a
            # principal with no clinic yet (login, invitation accept) still
            # has to be able to read its own membership rows.
            set_rls_context(None, user_id, principal_id=user_id or None)
            token = None
            try:
                scope: ClinicScope | None = None
                try:
                    scope = ClinicScope.from_request(request)
                except ApiError as exc:
                    request.clinic_scope_error = exc
                else:
                    from django.apps import apps

                    Membership = apps.get_model("clinics", "Membership")
                    if (
                        scope is None
                        and Membership.all_objects.filter(
                            user=user, status="active"
                        ).count()
                        > 1
                    ):
                        request.clinic_required = True

                if scope is not None:
                    request.clinic = scope.clinic
                    request.membership = scope.membership
                    set_rls_context(scope.clinic_id)

                token = set_current_scope(scope)
                return self.get_response(request)
            finally:
                if token is not None:
                    reset_current_scope(token)
                # The connection leaves the request clean: whatever a service
                # set (the invitation capability, the webhook capability, the
                # portal GUCs) is reset, so a pooled connection never carries a
                # context into the next request.
                clear_rls_context()
