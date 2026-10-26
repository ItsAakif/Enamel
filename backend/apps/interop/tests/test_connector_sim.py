"""Connector simulator, SSRF guard and live-mode gating (T130)."""

from __future__ import annotations

import pytest

from apps.core.errors import ApiError
from apps.interop.connectors.base import (
    ConnectorPartialWrite,
    ConnectorServerError,
    ConnectorTimeout,
    validate_base_url,
)
from apps.interop.connectors.simulator import SimulatedOpenDental

pytestmark = pytest.mark.django_db


def _public(host: str):
    return ["8.8.8.8"]


# --- SSRF guard -------------------------------------------------------------


def test_ssrf_rejects_plain_http():
    with pytest.raises(ApiError) as exc:
        validate_base_url(
            "http://pms.example.com", allowlist=("pms.example.com",), resolver=_public
        )
    assert exc.value.code == "connector_base_url_blocked"


def test_ssrf_rejects_host_outside_allowlist():
    with pytest.raises(ApiError) as exc:
        validate_base_url(
            "https://evil.example.com", allowlist=("pms.example.com",), resolver=_public
        )
    assert exc.value.details["reason"] == "host_not_allowlisted"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "fd00::1"],
)
def test_ssrf_rejects_private_link_local_and_metadata(address):
    with pytest.raises(ApiError) as exc:
        validate_base_url(
            "https://pms.example.com",
            allowlist=("pms.example.com",),
            resolver=lambda host, address=address: [address],
        )
    assert exc.value.details["reason"] == "forbidden_address"


def test_ssrf_allows_public_https_host():
    host = validate_base_url(
        "https://pms.example.com/api", allowlist=("pms.example.com",), resolver=_public
    )
    assert host == "pms.example.com"


# --- simulator fault injection ----------------------------------------------


def _simulator(**faults):
    return SimulatedOpenDental(
        patients=[{"external_id": "E1", "first_name": "Ada", "last_name": "L"}],
        appointments=[],
        settings={"faults": faults},
    )


def test_simulator_injects_timeout_then_recovers():
    sim = _simulator(pull_patients={"timeout": 1})
    with pytest.raises(ConnectorTimeout):
        sim.pull_patients()
    records = sim.pull_patients()
    assert [r.external_id for r in records] == ["E1"]


def test_simulator_injects_500():
    sim = _simulator(pull_patients={"error_500": 1})
    with pytest.raises(ConnectorServerError):
        sim.pull_patients()
    assert len(sim.pull_patients()) == 1


def test_simulator_duplicates_patients():
    sim = _simulator(duplicate_patients=True)
    records = sim.pull_patients()
    assert [r.external_id for r in records] == ["E1", "E1"]


def test_simulator_partial_write_is_permanent():
    sim = _simulator(partial_write=True)
    with pytest.raises(ConnectorPartialWrite):
        sim.pull_appointments()
    with pytest.raises(ConnectorPartialWrite):
        sim.pull_appointments()


# --- live-mode gating -------------------------------------------------------


def test_create_live_connector_without_credentials_is_409(owner):
    response = owner.post(
        "/api/v1/connectors/",
        {"name": "Live OD", "mode": "live", "base_url": "https://pms.example.com"},
    )
    assert response.status_code == 409, response.content
    assert response.json()["error"]["code"] == "connector_not_configured"


def test_create_live_connector_with_flag_disabled_is_409(owner, settings):
    settings.CONNECTOR_ALLOW_LIVE = False
    response = owner.post(
        "/api/v1/connectors/",
        {
            "name": "Live OD",
            "mode": "live",
            "base_url": "https://pms.example.com",
            "credentials": {"api_key": "secret"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "connector_not_configured"


def test_create_live_connector_with_ssrf_host_is_blocked(owner, settings):
    settings.CONNECTOR_ALLOW_LIVE = True
    settings.CONNECTOR_ALLOWED_HOSTS = ["pms.example.com"]
    response = owner.post(
        "/api/v1/connectors/",
        {
            "name": "Live OD",
            "mode": "live",
            "base_url": "https://evil.example.com",
            "credentials": {"api_key": "secret"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "connector_base_url_blocked"


# --- API shape --------------------------------------------------------------


def test_connector_response_includes_mode_and_hides_credentials(owner):
    created = owner.post(
        "/api/v1/connectors/",
        {
            "name": "Sim OD",
            "mode": "simulator",
            "settings_json": {"patients": [{"external_id": "E1", "first_name": "A"}]},
        },
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["mode"] == "simulator"
    assert body["has_credentials"] is False
    assert "credentials_enc" not in body
    assert "credentials" not in body


def test_sync_response_labels_simulator_mode(owner, connector_factory):
    connector = connector_factory(
        owner.clinic,
        settings_json={"patients": [{"external_id": "E1", "first_name": "Ada", "last_name": "L"}]},
    )
    response = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["mode"] == "simulator"
    assert body["run"]["status"] == "succeeded"
    # Never labelled live.
    assert "live" not in {body["mode"]}


def test_connector_create_requires_integrations_manage(assistant):
    response = assistant.post("/api/v1/connectors/", {"name": "Nope", "mode": "simulator"})
    assert response.status_code == 403
