"""Messaging app configuration (T114).

``ready()`` wires the appointment reminder re-sequencer onto
``scheduling.Appointment`` with a ``post_save`` signal. The import is guarded so
that a deployment without the scheduling app (or a partially loaded registry)
cannot break app startup; the connection uses a ``dispatch_uid`` so a double
``ready()`` never registers the receiver twice.
"""

from __future__ import annotations

from django.apps import AppConfig

SIGNAL_DISPATCH_UID = "messaging.resequence_appointment"


class MessagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.messaging"
    verbose_name = "Messaging"

    def ready(self) -> None:
        try:
            from django.db.models.signals import post_save

            from apps.scheduling.models import Appointment
        except Exception:  # pragma: no cover - scheduling absent / registry not ready
            return

        from .services.sequences import resequence_appointment

        def _on_appointment_saved(sender, instance, **kwargs) -> None:
            resequence_appointment(instance)

        post_save.connect(
            _on_appointment_saved,
            sender=Appointment,
            dispatch_uid=SIGNAL_DISPATCH_UID,
            weak=False,
        )
