"""Smoke tests for health and readiness endpoints."""

from __future__ import annotations

import pytest
from psycopg import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.core import health

pytestmark = pytest.mark.django_db


class _FakeCursor:
    """Minimal cursor double returning a fixed extension list."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return self._rows


class _FakeConnection:
    """Minimal connection double; exercises the real probe code."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def test_healthz_is_ok_without_database_queries(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "enamel-api"}
    assert response["Cache-Control"] == "no-store"


def test_readyz_reports_db_redis_and_extensions(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["db"] == "ok"
    assert payload["checks"]["redis"] == "ok"
    assert payload["checks"]["extensions"] == ["btree_gist", "pgcrypto", "vector"]


def test_readyz_returns_503_when_database_fails(client, monkeypatch):
    def broken() -> bool:
        raise OperationalError("host=127.0.0.1 password=enamel_dev_password")

    monkeypatch.setattr(health, "check_database", broken)

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["checks"]["db"] == "error: OperationalError"
    body = response.content.decode()
    assert "enamel_dev_password" not in body
    assert "127.0.0.1" not in body


def test_readyz_returns_503_when_redis_fails(client, monkeypatch):
    def broken() -> bool:
        raise RedisConnectionError("redis unavailable")

    monkeypatch.setattr(health, "check_redis", broken)

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["checks"]["redis"] == "error: ConnectionError"


def test_check_extensions_raises_when_one_is_missing(monkeypatch):
    monkeypatch.setattr(
        health,
        "connection",
        _FakeConnection([("btree_gist",), ("vector",)]),
    )

    with pytest.raises(RuntimeError, match="missing_extensions"):
        health.check_extensions()


def test_readyz_returns_503_when_extension_missing(client, monkeypatch):
    monkeypatch.setattr(
        health,
        "connection",
        _FakeConnection([("btree_gist",), ("vector",)]),
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["checks"]["extensions"] == "error: RuntimeError"


def test_run_readiness_checks_treats_false_as_failure(monkeypatch):
    monkeypatch.setattr(health, "check_database", lambda: False)
    monkeypatch.setattr(health, "check_redis", lambda: False)
    monkeypatch.setattr(health, "check_extensions", lambda: False)

    ok, checks = health.run_readiness_checks()

    assert ok is False
    assert checks == {
        "db": "error: probe_failed",
        "redis": "error: probe_failed",
        "extensions": "error: probe_failed",
    }


def test_readyz_returns_503_when_probe_returns_false(client, monkeypatch):
    monkeypatch.setattr(health, "check_redis", lambda: False)

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["checks"]["redis"] == "error: probe_failed"
