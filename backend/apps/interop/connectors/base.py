"""Connector protocol, transport errors and SSRF guards (T130).

The :class:`Connector` protocol is the only surface the sync services depend on::

    pull_patients(since) -> list[ExternalPatient]
    pull_appointments(since) -> list[ExternalAppointment]
    push_appointment(appointment) -> ExternalAppointment
    push_status(appointment, status) -> None

Two error families drive retry policy:

* :class:`TransientConnectorError` (timeout, HTTP 5xx) -- retried by the sync.
* :class:`PermanentConnectorError` (partial write, unconfigured, malformed) --
  never retried; the run is marked ``partial``/``failed`` with references.

SSRF
----
``validate_base_url`` enforces the controls in ARCHITECTURE.md section 4: https
only, a hostname allowlist, and rejection of loopback/private/link-local/
reserved/metadata addresses. DNS is re-resolved on every call (the live
connector calls the guard per request) so a DNS-rebinding host cannot pass the
first check and fail later. The resolver is injectable so the guard is testable
without network access.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from django.conf import settings

from apps.core.errors import ApiError

CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
SSRF_BLOCKED = "connector_base_url_blocked"


class ConnectorError(Exception):
    """Base class for connector failures."""

    code = "connector_error"

    def __init__(self, message: str = "") -> None:
        self.message = message or self.code
        super().__init__(self.message)


class TransientConnectorError(ConnectorError):
    """A retryable failure (timeout, 5xx)."""

    code = "connector_transient"


class ConnectorTimeout(TransientConnectorError):
    code = "connector_timeout"


class ConnectorServerError(TransientConnectorError):
    code = "connector_server_error"


class PermanentConnectorError(ConnectorError):
    """A non-retryable failure (partial write, malformed payload)."""

    code = "connector_permanent"


class ConnectorPartialWrite(PermanentConnectorError):
    code = "connector_partial_write"


class ConnectorNotConfigured(PermanentConnectorError):
    code = "connector_not_configured"


@dataclass
class ExternalPatient:
    """A patient as reported by the PMS."""

    external_id: str
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str | None = None
    phone: str = ""
    email: str = ""
    #: Optional internal number the record claims to match (conflict detection).
    number: str | None = None
    #: Optional internal UUID the record claims to be (conflict detection).
    internal_id: str | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalAppointment:
    """An appointment as reported by the PMS."""

    external_id: str
    patient_external_id: str
    start_at: datetime
    end_at: datetime
    status: str = "scheduled"
    provider_name: str = ""
    appointment_type_code: str = ""
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """The PMS connector contract implemented by every backend."""

    mode: str

    def pull_patients(self, since: datetime | None = None) -> list[ExternalPatient]: ...

    def pull_appointments(
        self, since: datetime | None = None
    ) -> list[ExternalAppointment]: ...

    def push_appointment(self, appointment: Any) -> ExternalAppointment: ...

    def push_status(self, appointment: Any, status: str) -> None: ...


# --- SSRF -------------------------------------------------------------------

Resolver = Callable[..., Any]


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            addresses.append(str(sockaddr[0]))
    return addresses


def _is_forbidden_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def allowed_hosts() -> tuple[str, ...]:
    configured = getattr(settings, "CONNECTOR_ALLOWED_HOSTS", None) or ()
    return tuple(str(host).strip().lower() for host in configured if str(host).strip())


def validate_base_url(
    url: str,
    *,
    allowlist: tuple[str, ...] | None = None,
    resolver: Resolver | None = None,
) -> str:
    """Validate ``url`` for outbound use and return the normalised host.

    Raises :class:`~apps.core.errors.ApiError` (409 ``connector_base_url_blocked``)
    when the URL is not https, its host is not allowlisted, or it resolves to a
    private/link-local/loopback/metadata address.
    """

    def blocked(reason: str) -> ApiError:
        return ApiError(
            SSRF_BLOCKED,
            "The connector base URL is not permitted.",
            status_code=409,
            details={"reason": reason},
        )

    raw = str(url or "").strip()
    if not raw:
        raise blocked("empty")

    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise blocked("https_required")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise blocked("missing_host")

    hosts = tuple(h.lower() for h in (allowlist if allowlist is not None else allowed_hosts()))
    if not hosts:
        raise blocked("allowlist_empty")
    if host not in hosts:
        raise blocked("host_not_allowlisted")

    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host)
    except OSError as exc:
        raise blocked("dns_failure") from exc
    if not addresses:
        raise blocked("dns_no_address")
    for address in addresses:
        if _is_forbidden_address(str(address)):
            raise blocked("forbidden_address")

    return host


def get_connector(config: Any) -> Connector:
    """Build the connector for ``config``.

    ``mode=simulator`` always succeeds. ``mode=live`` requires stored credentials
    *and* ``CONNECTOR_ALLOW_LIVE=true``; otherwise a 409 ``connector_not_configured``
    is raised. The live connector also validates its base URL (SSRF).
    """

    from ..credentials import has_credentials
    from .open_dental import OpenDentalConnector
    from .simulator import SimulatedOpenDental

    mode = str(getattr(config, "mode", "simulator") or "simulator")
    if mode != "live":
        return SimulatedOpenDental.from_config(config)

    if not has_credentials(config):
        raise ConnectorNotConfigured("Live connectors require stored credentials.")
    if not bool(getattr(settings, "CONNECTOR_ALLOW_LIVE", False)):
        raise ConnectorNotConfigured("Live connectors are disabled (CONNECTOR_ALLOW_LIVE).")

    validate_base_url(getattr(config, "base_url", ""))
    return OpenDentalConnector.from_config(config)


def not_configured_error(message: str = "The connector is not configured for live mode.") -> ApiError:
    return ApiError(CONNECTOR_NOT_CONFIGURED, message, status_code=409)


__all__ = [
    "CONNECTOR_NOT_CONFIGURED",
    "SSRF_BLOCKED",
    "Connector",
    "ConnectorError",
    "ConnectorNotConfigured",
    "ConnectorPartialWrite",
    "ConnectorServerError",
    "ConnectorTimeout",
    "ExternalAppointment",
    "ExternalPatient",
    "PermanentConnectorError",
    "TransientConnectorError",
    "allowed_hosts",
    "get_connector",
    "not_configured_error",
    "validate_base_url",
]
