"""Durable job handlers for the imaging app (T129).

``imaging.inference`` runs one assistive-analysis job. The handler is registered
at app-ready time so the beat scan and the eager test path resolve it by kind.
"""

from __future__ import annotations

from typing import Any

from apps.core.jobs import durable_task

from .services.inference import JOB_KIND, JOB_QUEUE, run_inference_job


@durable_task(JOB_KIND, queue=JOB_QUEUE)
def handle_inference(payload: dict[str, Any]) -> dict[str, Any]:
    return run_inference_job(payload)


__all__ = ["handle_inference"]
