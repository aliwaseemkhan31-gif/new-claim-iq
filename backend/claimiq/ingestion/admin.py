"""Django admin for processing jobs.

Operator-facing, read-mostly. Job state is written by the pipeline, so the
admin exposes it for diagnosis rather than editing — hand-editing a state
machine mid-run is a reliable way to corrupt it.
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from claimiq.core.domain.errors import ClaimIQError
from claimiq.ingestion.models import ProcessingJob, StageLog
from claimiq.ingestion.services import runner


class StageLogInline(admin.TabularInline):
    model = StageLog
    extra = 0
    can_delete = False
    readonly_fields = ("stage", "attempt", "status", "duration_ms", "error_code", "metrics", "created_at")
    fields = readonly_fields
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "document_title",
        "kind",
        "status",
        "current_stage",
        "progress_bar",
        "created_at",
    )
    list_filter = ("status", "kind", "current_stage", "error_code")
    search_fields = ("id", "document_version__document__title", "celery_task_id")
    readonly_fields = (
        "document_version",
        "kind",
        "status",
        "current_stage",
        "progress_percent",
        "state",
        "error_code",
        "error_message",
        "celery_task_id",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
    inlines = [StageLogInline]
    actions = ["action_retry", "action_cancel"]
    date_hierarchy = "created_at"

    @admin.display(description="Document")
    def document_title(self, obj: ProcessingJob) -> str:
        return obj.document_version.document.title

    @admin.display(description="Progress")
    def progress_bar(self, obj: ProcessingJob) -> str:
        return format_html(
            '<div style="background:#eee;width:100px;height:10px;border-radius:5px">'
            '<div style="background:#4a7;width:{}px;height:10px;border-radius:5px"></div>'
            "</div> {}%",
            obj.progress_percent,
            obj.progress_percent,
        )

    @admin.action(description="Retry failed jobs from their failed stage")
    def action_retry(self, request, queryset) -> None:
        retried = 0
        for job in queryset:
            try:
                runner.retry_job(job)
            except ClaimIQError as exc:
                self.message_user(request, f"{job.id}: {exc.message}", messages.WARNING)
                continue
            from claimiq.ingestion.tasks import process_document

            process_document.apply_async(args=[str(job.id)], queue="ingestion")
            retried += 1
        if retried:
            self.message_user(request, f"{retried} job(s) requeued.", messages.SUCCESS)

    @admin.action(description="Request cancellation")
    def action_cancel(self, request, queryset) -> None:
        cancelled = 0
        for job in queryset:
            try:
                runner.request_cancellation(job)
            except ClaimIQError as exc:
                self.message_user(request, f"{job.id}: {exc.message}", messages.WARNING)
                continue
            cancelled += 1
        if cancelled:
            self.message_user(
                request,
                f"{cancelled} job(s) will stop at the next safe point.",
                messages.SUCCESS,
            )

    def has_add_permission(self, request) -> bool:
        return False
