"""API pagination conventions (ARCHITECTURE.md section 7).

The envelope is ``{"count", "next", "previous", "results"}``. ``page_size``
defaults to 25 and is capped at 100. An invalid page number is a 400
``invalid_page`` rather than DRF's default 404, because the path exists and the
query is wrong.

DRF's link builder replaces only the ``page`` parameter, so ``ordering``,
``search`` and filter parameters are preserved on ``next``/``previous``.
"""

from __future__ import annotations

import logging

from rest_framework import exceptions as drf_exceptions
from rest_framework.pagination import CursorPagination, PageNumberPagination

from .errors import CODE_INVALID_PAGE, ApiError

logger = logging.getLogger("enamel.request")

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# Query parameters understood by the API contract. Domain endpoints extend this
# through ``allowed_query_params`` on their view class for endpoint-specific
# filters. Logging is DEBUG-only and never includes values.
COMMON_QUERY_PARAMS = frozenset(
    {
        "page",
        "page_size",
        "ordering",
        "search",
        "format",
        "status",
        "role",
        "location",
        "provider",
        "patient",
        "date",
        "start",
        "end",
        "start_at",
        "end_at",
        "from",
        "to",
        "clinic",
        "type",
        "source",
        "kind",
        "entity_type",
        "entity_id",
        "query",
        "q",
        "limit",
        "cursor",
    }
)


def log_unknown_query_params(request, view=None) -> None:
    """Log unknown query parameter names once, once per request, DEBUG only.

    Values are never logged (they can contain PHI). The middleware calls this
    for every API request; direct callers must pass ``view`` to expose its
    ``allowed_query_params``.
    """

    from django.conf import settings

    if not settings.DEBUG:
        return

    allowed = set(COMMON_QUERY_PARAMS)
    allowed.update(getattr(view, "allowed_query_params", ()) or ())
    try:
        names = set(request.query_params.keys())
    except AttributeError:
        names = set(getattr(request, "GET", {}).keys())

    unknown = sorted(names - allowed)
    if not unknown:
        return

    logger.debug(
        "unknown_query_params",
        extra={
            "request_id": getattr(request, "request_id", None),
            "path": getattr(request, "path", None),
            "unknown_params": unknown,
        },
    )


class StandardPagination(PageNumberPagination):
    """Project-wide page-number pagination."""

    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def paginate_queryset(self, queryset, request, view=None):
        try:
            page = super().paginate_queryset(queryset, request, view=view)
        except drf_exceptions.NotFound as exc:
            raise ApiError(
                CODE_INVALID_PAGE,
                "The requested page is not valid.",
                status_code=400,
                field_errors={"page": ["enter a valid page number"]},
                details={"page": self._requested_page(request)},
            ) from exc

        log_unknown_query_params(request, view)
        return page

    @staticmethod
    def _requested_page(request) -> str | None:
        try:
            value = request.query_params.get("page")
        except AttributeError:
            value = getattr(request, "GET", {}).get("page")
        return str(value) if value is not None else None


class StandardPaginationViewMixin:
    # Marker mixin for APIView list endpoints that use ``StandardPagination``.
    #
    # drf-spectacular resolves the pagination envelope from
    # ``view.pagination_class`` alone: a plain ``APIView`` subclass does not
    # inherit DRF's ``DEFAULT_PAGINATION_CLASS`` the way ``GenericAPIView`` does,
    # so a view that calls ``get_paginated_response`` must expose the class
    # explicitly or the generated schema describes a bare array while the runtime
    # returns ``{count, next, previous, results}``. List endpoints that
    # intentionally return a bare array opt out with ``pagination_class = None``.
    #
    # (Deliberately a comment, not a docstring: drf-spectacular falls back to a
    # view's class docstring for the operation description, so a docstring here
    # would leak into every inheriting endpoint.)

    pagination_class = StandardPagination


class AuditCursorPagination(CursorPagination):
    """Newest-first cursor pagination for append-only streams.

    Audit rows are immutable and ordered by a monotonic bigint, so cursor
    pagination gives a stable window even while new rows are appended (a page
    number would shift under concurrent inserts). Ordering is by ``-id``;
    ``created_at`` and ``id`` always agree because both come from one insert.
    """

    page_size = DEFAULT_PAGE_SIZE
    max_page_size = MAX_PAGE_SIZE
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
    ordering = ("-id",)

    def paginate_queryset(self, queryset, request, view=None):
        log_unknown_query_params(request, view)
        try:
            return super().paginate_queryset(queryset, request, view=view)
        except drf_exceptions.NotFound as exc:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"cursor": ["enter a valid cursor"]},
            ) from exc
