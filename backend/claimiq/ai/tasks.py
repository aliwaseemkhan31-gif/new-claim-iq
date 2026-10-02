"""Celery tasks for the AI app. Routed to the ``ai`` queue.

Thin by design: the work lives in the service layer, so it runs identically
from a worker, a management command, or a development thread.
"""
from __future__ import annotations

from celery import shared_task

from claimiq.core.logging import bind_context, get_logger

logger = get_logger("ai.tasks")


@shared_task(bind=True, name="claimiq.ai.tasks.run_model_benchmark", acks_late=False)
def run_model_benchmark(self, run_id: str) -> dict:
    """Measure this machine and the models on it.

    ``acks_late=False``, unlike ingestion: a benchmark redelivered after a
    worker crash would re-run every generation from the start, which on a
    processor-only installation is half an hour of the runtime being busy for
    a result nobody is waiting for any more. A run left in ``running`` by a
    dead worker is visible in the UI and can be started again by hand.
    """
    from claimiq.ai.models import ModelBenchmarkRun
    from claimiq.ai.services.benchmarking import run_benchmark

    bind_context(benchmark_run_id=run_id, task_name=self.name)
    try:
        run = run_benchmark(run_id)
    except ModelBenchmarkRun.DoesNotExist:
        logger.warning("benchmark.missing", extra={"run_id": run_id})
        return {"status": "missing", "run_id": run_id}
    return {"status": run.status, "run_id": run_id}
