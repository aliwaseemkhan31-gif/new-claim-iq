"""Celery tasks for claim analysis. Routed to the ``ai`` queue.

Thin by design: the engine lives in the service layer, so it runs identically
from a worker, a management command, or a development thread.
"""
from __future__ import annotations

from celery import shared_task

from claimiq.core.logging import bind_context, get_logger

logger = get_logger("analysis.tasks")


@shared_task(bind=True, name="claimiq.analysis.tasks.run_claim_analysis", acks_late=True)
def run_claim_analysis(self, analysis_id: str) -> dict:
    """Run every pending strand of an analysis.

    Safe to redeliver: completed strands are skipped and a strand left running
    by a crashed worker restarts, so ``acks_late`` cannot duplicate findings.
    """
    from claimiq.analysis.models import ClaimAnalysis
    from claimiq.analysis.services.claim_analysis import run_analysis

    bind_context(analysis_id=analysis_id, task_name=self.name)
    try:
        analysis = run_analysis(analysis_id)
    except ClaimAnalysis.DoesNotExist:
        logger.warning("analysis.missing", extra={"analysis_id": analysis_id})
        return {"status": "missing", "analysis_id": analysis_id}
    return {"status": analysis.status, "analysis_id": analysis_id}
