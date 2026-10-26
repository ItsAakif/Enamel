from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal"
    verbose_name = "Patient portal"

    def ready(self) -> None:
        # Register the OpenAPI extension for the portal cookie scheme so the
        # generated schema documents portal auth instead of warning about it.
        from . import schema  # noqa: F401
