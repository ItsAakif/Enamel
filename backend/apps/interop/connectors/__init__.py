"""PMS connector implementations (T130)."""

from __future__ import annotations

from .base import (
    Connector,
    ConnectorError,
    ConnectorNotConfigured,
    ConnectorPartialWrite,
    ConnectorServerError,
    ConnectorTimeout,
    ExternalAppointment,
    ExternalPatient,
    PermanentConnectorError,
    TransientConnectorError,
    get_connector,
    validate_base_url,
)
from .open_dental import OpenDentalConnector
from .simulator import SimulatedOpenDental

__all__ = [
    "Connector",
    "ConnectorError",
    "ConnectorNotConfigured",
    "ConnectorPartialWrite",
    "ConnectorServerError",
    "ConnectorTimeout",
    "ExternalAppointment",
    "ExternalPatient",
    "OpenDentalConnector",
    "PermanentConnectorError",
    "SimulatedOpenDental",
    "TransientConnectorError",
    "get_connector",
    "validate_base_url",
]
