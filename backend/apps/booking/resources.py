"""Response projections for the public booking API (T112).

Every projection here is deliberately PHI-free: no patient id, name, phone,
email or appointment reason. The clinic payload exposes only public booking
configuration, active locations, ``public_bookable`` appointment types and
bookable providers by display name.
"""

from __future__ import annotations

from typing import Any

from .services.policy import policy_summary


def public_location_resource(location: Any) -> dict[str, Any]:
    return {
        "id": str(location.pk),
        "name": location.name,
        "address_json": location.address_json or {},
        "phone": location.phone,
        "is_active": location.is_active,
    }


def public_appointment_type_resource(appointment_type: Any) -> dict[str, Any]:
    return {
        "id": str(appointment_type.pk),
        "code": appointment_type.code,
        "name": appointment_type.name,
        "duration_min": appointment_type.duration_min,
        "requires_operatory": appointment_type.requires_operatory,
        "color": appointment_type.color,
    }


def public_provider_resource(provider: Any) -> dict[str, Any]:
    # Display name only: never a membership, email, phone or user id.
    return {
        "id": str(provider.pk),
        "display_name": provider.display_name,
        "specialty": provider.specialty,
        "color": provider.color,
    }


def public_clinic_resource(
    clinic: Any,
    policy: Any,
    *,
    locations: list[Any],
    appointment_types: list[Any],
    providers: list[Any],
) -> dict[str, Any]:
    return {
        "id": str(clinic.pk),
        "name": clinic.name,
        "slug": clinic.slug,
        "timezone": clinic.timezone,
        "currency": clinic.currency,
        "locale": clinic.locale,
        "country": clinic.country,
        "phone": clinic.phone,
        "address_json": clinic.address_json or {},
        "locations": [public_location_resource(row) for row in locations],
        "appointment_types": [
            public_appointment_type_resource(row) for row in appointment_types
        ],
        "providers": [public_provider_resource(row) for row in providers],
        "policy": policy_summary(policy),
    }


__all__ = [
    "public_appointment_type_resource",
    "public_clinic_resource",
    "public_location_resource",
    "public_provider_resource",
]
