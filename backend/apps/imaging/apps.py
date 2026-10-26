from django.apps import AppConfig


class ImagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imaging"
    verbose_name = "Imaging"

    def ready(self) -> None:
        # Register the ``imaging.inference`` durable handler for every entry
        # point (the beat scan resolves handlers by kind).
        from . import tasks  # noqa: F401
