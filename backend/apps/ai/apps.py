from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"
    verbose_name = "AI assistant"

    def ready(self) -> None:
        # Import the durable job handlers so ``ai.escalation`` is registered for
        # every entry point (the beat scan resolves handlers by kind).
        from . import tasks  # noqa: F401
