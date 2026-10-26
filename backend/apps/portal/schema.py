"""OpenAPI documentation for the portal cookie scheme (T121).

drf-spectacular has no built-in extension for a custom authentication class, so
without this the generated schema emits a warning for every portal view. This
extension documents ``enamel_portal`` as an apiKey-in-cookie scheme, distinct
from the staff ``enamel_session`` scheme.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class PatientSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.portal.authentication.PatientSessionAuthentication"
    name = "portalCookieAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        from .services.identity import cookie_name

        return {
            "type": "apiKey",
            "in": "cookie",
            "name": cookie_name(),
            "description": (
                "Patient portal session cookie. Separate from the staff "
                "enamel_session cookie; neither authenticates the other surface."
            ),
        }


__all__ = ["PatientSessionAuthenticationScheme"]
