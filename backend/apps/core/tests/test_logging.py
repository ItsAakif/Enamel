"""Request id propagation, JSON log shape, and PHI-leak regressions."""

from __future__ import annotations

import io
import json
import logging
import logging.config
import re
from contextlib import contextmanager

import pytest
from django.db import connection

from config.logging import JsonFormatter, build_logging_config

pytestmark = pytest.mark.django_db

GENERATED_ID_RE = re.compile(r"^req_[0-9a-f]{32}$")


def _capture_request_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("enamel.request")
    logger.addHandler(handler)
    return logger, handler, stream


def _snapshot_logging():
    loggers = {}
    for name, logger in logging.Logger.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            loggers[name] = (
                logger.level,
                list(logger.handlers),
                logger.propagate,
                logger.disabled,
            )
    root = logging.getLogger()
    return loggers, (root.level, list(root.handlers), root.propagate)


def _restore_logging(snapshot):
    loggers, root_state = snapshot
    for name, (level, handlers, propagate, disabled) in loggers.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.disabled = disabled
    root = logging.getLogger()
    root.setLevel(root_state[0])
    root.handlers[:] = root_state[1]
    root.propagate = root_state[2]


@contextmanager
def _configured_logging(level="INFO"):
    """Apply the project logging config, capturing its stdout handler."""

    config = build_logging_config(level=level)
    stream = io.StringIO()
    config["handlers"]["stdout"]["stream"] = stream
    snapshot = _snapshot_logging()
    try:
        logging.config.dictConfig(config)
        yield stream
    finally:
        _restore_logging(snapshot)


def test_request_id_header_is_echoed(client):
    response = client.get("/healthz", headers={"X-Request-Id": "req_client-supplied-123"})

    assert response.status_code == 200
    assert response["X-Request-Id"] == "req_client-supplied-123"


def test_request_id_is_generated_when_absent(client):
    response = client.get("/healthz")

    assert GENERATED_ID_RE.fullmatch(response["X-Request-Id"])


def test_request_id_is_generated_when_header_is_invalid(client):
    response = client.get("/healthz", headers={"X-Request-Id": "bad id with spaces"})

    assert GENERATED_ID_RE.fullmatch(response["X-Request-Id"])


def test_json_request_log_shape(client):
    logger, handler, stream = _capture_request_logs()
    try:
        client.get("/healthz?patient=Jane+Doe", headers={"X-Request-Id": "req_log-shape-1"})
    finally:
        logger.removeHandler(handler)

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "expected a request log line"
    payload = json.loads(lines[-1])

    assert payload["level"] == "INFO"
    assert payload["logger"] == "enamel.request"
    assert payload["message"] == "http_request"
    assert payload["request_id"] == "req_log-shape-1"
    assert payload["method"] == "GET"
    assert payload["path"] == "/healthz"
    assert payload["status"] == 200
    assert isinstance(payload["duration_ms"], (int, float))
    assert payload["ts"].endswith("Z")

    # The query string is neither in the structured fields nor in the raw line.
    assert "patient" not in payload
    assert "Jane" not in stream.getvalue()


def test_debug_setting_does_not_enable_leak_prone_loggers():
    config = build_logging_config(level="INFO")

    assert config["root"]["level"] == "INFO"
    assert config["loggers"]["django"]["level"] == "INFO"
    assert config["loggers"]["django.db.backends"]["level"] == "INFO"
    assert config["loggers"]["django.server"]["level"] == "WARNING"
    assert config["loggers"]["enamel.request"]["level"] == "INFO"

    # Even an explicit verbose level keeps the two leak-prone loggers pinned.
    verbose = build_logging_config(level="DEBUG")
    assert verbose["loggers"]["django.db.backends"]["level"] == "INFO"
    assert verbose["loggers"]["django.server"]["level"] == "WARNING"


def test_logging_never_emits_query_strings_or_sql_parameters(client):
    """Regression for D2: dev logging must not write PHI to stdout.

    ``django.server`` logs the raw request line (query string included) and
    ``django.db.backends`` logs SQL plus bound parameters at DEBUG. With the
    default INFO configuration neither value may reach the log stream.
    """

    with _configured_logging("INFO") as stream:
        # Simulate the runserver access line for a PHI-bearing URL.
        logging.getLogger("django.server").info(
            '"GET /healthz?patient=JaneDoe HTTP/1.1" 200 12'
        )

        # A real query with a sensitive bound parameter, with Django's debug
        # cursor enabled (the same wrapper DEBUG=True installs).
        connection.force_debug_cursor = True
        try:
            connection.queries_log.clear()
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s", ["JaneDoe"])
                cursor.fetchone()
            recorded = list(connection.queries)
        finally:
            connection.force_debug_cursor = False
            connection.queries_log.clear()

        client.get("/healthz?patient=JaneDoe")

    # The debug wrapper did record the SQL (so the query really was traced);
    # not appearing in the log is therefore a property of the configuration.
    assert recorded, "expected the debug cursor to record the query"
    assert "JaneDoe" in recorded[0]["sql"]

    output = stream.getvalue()
    assert "enamel.request" in output or '"logger":"enamel.request"' in output
    assert "JaneDoe" not in output
    assert "patient" not in output
