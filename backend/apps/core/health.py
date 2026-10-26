"""Readiness checks for ``/readyz``.

Every probe returns a short machine-readable status and never includes
credentials, hostnames, or exception messages in the response payload.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import redis as redis_lib
from django.conf import settings
from django.db import connection

REQUIRED_EXTENSIONS = ("btree_gist", "pgcrypto", "vector")
REDIS_TIMEOUT_SECONDS = 2


def check_database() -> bool:
    """Run a trivial query against PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return True


def check_extensions() -> list[str]:
    """Return the required extensions when all of them are installed."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}

    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if missing:
        raise RuntimeError("missing_extensions")
    return list(REQUIRED_EXTENSIONS)


def check_redis() -> bool:
    """Ping Redis with a short timeout."""
    client = redis_lib.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    try:
        return bool(client.ping())
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


def run_readiness_checks() -> tuple[bool, dict[str, Any]]:
    """Execute all dependency probes.

    Returns ``(ok, checks)`` where ``checks`` always contains ``db``, ``redis``
    and ``extensions``. Failures carry the exception type only.
    """
    checks: dict[str, Any] = {}
    ok = True

    probes: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("db", check_database),
        ("redis", check_redis),
        ("extensions", check_extensions),
    )

    for name, probe in probes:
        try:
            result = probe()
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            ok = False
            checks[name] = f"error: {type(exc).__name__}"
        else:
            if result is False:
                ok = False
                checks[name] = "error: probe_failed"
            else:
                checks[name] = result if name == "extensions" else "ok"

    return ok, checks
