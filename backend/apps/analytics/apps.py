from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    verbose_name = "Analytics and reports"

    def ready(self) -> None:
        # Populate the metric registry (data + function pairs) for every entry
        # point, not just the first request that happens to import a service.
        from .services import register_all

        register_all()
