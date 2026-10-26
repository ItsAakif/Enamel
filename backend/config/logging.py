"""Structured JSON logging for Enamel.

All application logs are emitted to stdout as one JSON object per line. Request
bodies, headers (cookies/authorization) and PHI are never logged; the request
middleware only records method, path, status, duration and the request id.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Fields that middleware/tasks may attach with ``extra=``. Only these are copied
# into the JSON payload, which keeps arbitrary/PHI-bearing values out of logs.
_ALLOWED_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "route",
    "status",
    "duration_ms",
    "event",
    "outcome",
    "task",
    "service",
    "clinic_id",
    "user_id",
)


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in _ALLOWED_EXTRA_FIELDS:
            if field in record.__dict__:
                payload[field] = record.__dict__[field]

        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None

        return json.dumps(payload, default=str, ensure_ascii=True, separators=(",", ":"))


def build_logging_config(*, level: str = "INFO") -> dict[str, Any]:
    """Return a ``logging.config.dictConfig``-compatible mapping (ASCII, stdout).

    ``DEBUG=True`` deliberately does not raise the log level: Django logs SQL
    text *and bound parameters* on ``django.db.backends`` at DEBUG, and
    ``runserver`` logs the raw request line (including the query string) on
    ``django.server`` at INFO. Both would write PHI-bearing values to stdout.
    Operators who really want verbose framework logs must opt in explicitly via
    ``LOG_LEVEL=DEBUG``; even then the two leak-prone loggers stay pinned.
    """

    resolved_level = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "config.logging.JsonFormatter"},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "json",
            },
        },
        "root": {
            "handlers": ["stdout"],
            "level": resolved_level,
        },
        "loggers": {
            "django": {"handlers": ["stdout"], "level": resolved_level, "propagate": False},
            # SQL statements and their bound parameters are logged at DEBUG.
            "django.db.backends": {
                "handlers": ["stdout"],
                "level": "INFO",
                "propagate": False,
            },
            # runserver logs the request line (with the query string) at INFO.
            "django.server": {
                "handlers": ["stdout"],
                "level": "WARNING",
                "propagate": False,
            },
            "enamel": {"handlers": ["stdout"], "level": resolved_level, "propagate": False},
            "enamel.request": {
                "handlers": ["stdout"],
                "level": resolved_level,
                "propagate": False,
            },
        },
    }
