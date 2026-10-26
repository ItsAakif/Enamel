"""Live Open Dental connector (T130).

This is the network implementation of the :class:`~apps.interop.connectors.base.Connector`
protocol. It is *never* exercised in the test suite (``pytest-socket`` blocks
outbound traffic) and is deliberately thin: the SSRF guard and the retry policy
live in the shared layers, and the response parsing is isolated in ``_parse_*``
helpers so a fixture-based contract test could drive it later.

Every request re-validates the base URL (https, allowlist, DNS re-resolution) so
a DNS-rebinding host cannot pass an initial check and be reused.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .base import (
    ConnectorServerError,
    ConnectorTimeout,
    ExternalAppointment,
    ExternalPatient,
    validate_base_url,
)

DEFAULT_TIMEOUT_SECONDS = 15.0


class OpenDentalConnector:
    """HTTP connector against a live Open Dental API."""

    mode = "live"

    def __init__(
        self,
        *,
        base_url: str,
        credentials: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        allowlist: tuple[str, ...] | None = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.credentials = dict(credentials or {})
        self.timeout = float(timeout)
        self.allowlist = allowlist

    @classmethod
    def from_config(cls, config: Any) -> OpenDentalConnector:
        from ..credentials import decrypt_credentials

        return cls(
            base_url=getattr(config, "base_url", ""),
            credentials=decrypt_credentials(getattr(config, "credentials_enc", "")),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        api_key = str(self.credentials.get("api_key", "") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        developer_key = str(self.credentials.get("developer_key", "") or "")
        if developer_key:
            headers["X-Developer-Key"] = developer_key
        return headers

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # Re-resolve and re-check on every call (DNS rebinding defence).
        validate_base_url(self.base_url, allowlist=self.allowlist)
        url = f"{self.base_url}{path}"
        try:
            response = httpx.get(
                url,
                params=params or {},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ConnectorTimeout("Open Dental request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ConnectorServerError("Open Dental request failed.") from exc
        if response.status_code >= 500:
            raise ConnectorServerError(f"Open Dental returned {response.status_code}.")
        if response.status_code >= 400:
            raise ConnectorServerError(f"Open Dental rejected the request ({response.status_code}).")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorServerError("Open Dental returned a non-JSON body.") from exc

    def pull_patients(self, since: datetime | None = None) -> list[ExternalPatient]:
        params = {"since": since.isoformat()} if since is not None else {}
        payload = self._get("/patients", params)
        rows = payload.get("patients") or payload.get("results") or []
        return [self._parse_patient(row) for row in rows]

    def pull_appointments(self, since: datetime | None = None) -> list[ExternalAppointment]:
        params = {"since": since.isoformat()} if since is not None else {}
        payload = self._get("/appointments", params)
        rows = payload.get("appointments") or payload.get("results") or []
        return [self._parse_appointment(row) for row in rows]

    def push_appointment(self, appointment: Any) -> ExternalAppointment:
        raise ConnectorServerError(
            "Live appointment push is not enabled until a connector is verified "
            "against a real Open Dental API (ARCHITECTURE.md section 17)."
        )

    def push_status(self, appointment: Any, status: str) -> None:
        raise ConnectorServerError(
            "Live status push is not enabled until a connector is verified "
            "against a real Open Dental API (ARCHITECTURE.md section 17)."
        )

    @staticmethod
    def _parse_patient(row: dict[str, Any]) -> ExternalPatient:
        return ExternalPatient(
            external_id=str(row.get("id") or row.get("PatNum") or ""),
            first_name=str(row.get("first_name") or row.get("FName") or ""),
            last_name=str(row.get("last_name") or row.get("LName") or ""),
            date_of_birth=row.get("birth_date") or row.get("Birthdate"),
            phone=str(row.get("phone") or row.get("WirelessPhone") or ""),
            email=str(row.get("email") or row.get("Email") or ""),
            raw=dict(row),
        )

    @staticmethod
    def _parse_appointment(row: dict[str, Any]) -> ExternalAppointment:
        start = row.get("start_at") or row.get("AptDateTime")
        end = row.get("end_at")
        return ExternalAppointment(
            external_id=str(row.get("id") or row.get("AptNum") or ""),
            patient_external_id=str(row.get("patient_id") or row.get("PatNum") or ""),
            start_at=_coerce_dt(start),
            end_at=_coerce_dt(end),
            status=str(row.get("status") or row.get("AptStatus") or "scheduled"),
            provider_name=str(row.get("provider_name") or ""),
            appointment_type_code=str(row.get("appointment_type_code") or ""),
            raw=dict(row),
        )


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "OpenDentalConnector"]
