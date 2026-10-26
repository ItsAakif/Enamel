"""Middleware ordering: CORS preflights are logged and get a request id."""

from __future__ import annotations

import io
import json
import logging

from config.logging import JsonFormatter


def test_cors_preflight_gets_request_id_and_log_line(client):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("enamel.request")
    logger.addHandler(handler)
    try:
        response = client.options(
            "/api/v1/auth/login/",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
                "X-Request-Id": "req_preflight-1",
            },
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert response["X-Request-Id"] == "req_preflight-1"

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["logger"] == "enamel.request"
    assert lines[0]["request_id"] == "req_preflight-1"
    assert lines[0]["method"] == "OPTIONS"
    assert lines[0]["path"] == "/api/v1/auth/login/"
    assert lines[0]["status"] == 200
