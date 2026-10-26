"""Celery beat tasks for messaging (T114).

``recall_recompute`` is the daily sweep named in ARCHITECTURE.md section 11: it
materialises recall due dates from each patient's last completed encounter and
returns the count of due recalls. Deferred/retried sends are ordinary
``JobRequest`` rows, so the shared ``core.tasks.scan_due_actions`` dispatches
them; no separate messaging scanner is needed.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from apps.core.rls import bypass_rls

from .services.recall import recall_due

TASK_RECALL_RECOMPUTE = "messaging.tasks.recall_recompute"


@shared_task(name=TASK_RECALL_RECOMPUTE)
def recall_recompute() -> dict[str, Any]:
    """Daily: refresh recall due dates for every clinic."""

    from apps.clinics.models import Clinic

    clinics = 0
    due = 0
    # Cross-clinic sweep: read the clinic list and candidate rows under the
    # maintenance bypass, while each recall_due() call activates its own clinic.
    with bypass_rls():
        for clinic in Clinic.objects.all().iterator():
            clinics += 1
            due += len(recall_due(clinic))
    return {"clinics": clinics, "due": due}


__all__ = ["TASK_RECALL_RECOMPUTE", "recall_recompute"]
