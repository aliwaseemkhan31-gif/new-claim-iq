"""Notification handlers.

Each handler reacts to one kind of real event and decides who is told. All are
idempotent through ``dedupe_key``: a redelivered Celery task, a re-run build or
a repeated deadline scan re-reports the same event and creates nothing new.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from claimiq.core.logging import get_logger
from claimiq.notifications.models import Notification, NotificationCategory, NotificationSeverity

logger = get_logger("notifications")

#: A notice deadline this close is "approaching".
DEADLINE_WARNING_DAYS = 7

#: The deadline scan runs at most this often per organization when triggered
#: by reading notifications. Celery beat runs it on its own schedule too.
DEADLINE_SCAN_INTERVAL_SECONDS = 600


def notify(
    *,
    recipients: Iterable[Any],
    organization_id,
    category: str,
    title: str,
    message: str = "",
    severity: str = NotificationSeverity.INFO,
    link: dict | None = None,
    project_id=None,
    dedupe_key: str,
) -> int:
    """Create one notification per distinct recipient. Returns how many were new."""
    created = 0
    seen: set = set()
    for user in recipients:
        if user is None or user.pk in seen or not getattr(user, "is_active", True):
            continue
        seen.add(user.pk)
        try:
            with transaction.atomic():
                _, was_created = Notification.objects.get_or_create(
                    recipient=user,
                    dedupe_key=dedupe_key[:255],
                    defaults={
                        "organization_id": organization_id,
                        "project_id": project_id,
                        "category": category,
                        "severity": severity,
                        "title": title[:255],
                        "message": message,
                        "link": link or {},
                    },
                )
        except IntegrityError:
            # Lost a race with a concurrent handler for the same event.
            was_created = False
        created += int(was_created)
    return created


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def project_members_with(project_id, permission: str) -> list:
    """Active members of a project whose role grants ``permission``."""
    from claimiq.accounts.domain.permissions import resolve_permissions
    from claimiq.projects.models import ProjectMember

    members = ProjectMember.objects.select_related("user").filter(
        project_id=project_id, is_active=True, user__is_active=True
    )
    return [m.user for m in members if permission in resolve_permissions(None, m.role)]


def organization_users_with(organization_id, permission: str) -> list:
    """Active organization members whose organization role grants ``permission``."""
    from claimiq.accounts.domain.permissions import resolve_permissions
    from claimiq.accounts.models import OrganizationMembership

    memberships = OrganizationMembership.objects.select_related("user").filter(
        organization_id=organization_id, is_active=True, user__is_active=True
    )
    return [m.user for m in memberships if permission in resolve_permissions(m.role, None)]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def on_ingestion_finished(job) -> None:
    """Tell the uploader their project document finished, or failed."""
    version = job.document_version
    document = version.document
    if document.project_id is None or version.created_by_id is None:
        return  # reference documents are reported through their knowledge base
    completed = version.processing_status == "completed"
    notify(
        recipients=[version.created_by],
        organization_id=document.project.organization_id,
        project_id=document.project_id,
        category=(
            NotificationCategory.DOCUMENT_PROCESSED if completed else NotificationCategory.DOCUMENT_FAILED
        ),
        severity=NotificationSeverity.SUCCESS if completed else NotificationSeverity.DANGER,
        title=(
            f"“{document.title}” is ready" if completed else f"“{document.title}” could not be processed"
        ),
        message=(
            f"{version.page_count} page(s) processed; the document is searchable."
            if completed
            else (version.processing_error or "Processing did not complete.")
        ),
        link={"name": "document-viewer", "params": {"documentId": str(document.pk)}},
        dedupe_key=f"ingestion:{job.pk}:{version.processing_status}",
    )


_KB_MESSAGES = {
    "ready": (
        NotificationCategory.KNOWLEDGE_BASE_READY,
        NotificationSeverity.INFO,
        "{name} validated — ready to publish",
        "Validation found no blocking issues. It is not retrievable until it is published.",
    ),
    "quarantined": (
        NotificationCategory.KNOWLEDGE_BASE_QUARANTINED,
        NotificationSeverity.WARNING,
        "{name} is blocked by validation",
        "Validation found blocking issues in the retained text. Review the validation report.",
    ),
    "failed": (
        NotificationCategory.KNOWLEDGE_BASE_FAILED,
        NotificationSeverity.DANGER,
        "{name} could not be built",
        "{error}",
    ),
    "published": (
        NotificationCategory.KNOWLEDGE_BASE_PUBLISHED,
        NotificationSeverity.SUCCESS,
        "{name} is published",
        "Projects governed by this edition can now retrieve its text.",
    ),
}


def on_knowledge_base_status_changed(sender, knowledge_base, previous_status, actor=None, **kwargs) -> None:
    template = _KB_MESSAGES.get(knowledge_base.status)
    if template is None:
        return
    category, severity, title, message = template
    recipients = organization_users_with(knowledge_base.organization_id, "org.kb.manage")
    if knowledge_base.created_by_id:
        recipients.append(knowledge_base.created_by)
    # The person who pressed "publish" does not need telling that they did.
    if actor is not None:
        recipients = [u for u in recipients if u.pk != actor.pk]
    notify(
        recipients=recipients,
        organization_id=knowledge_base.organization_id,
        category=category,
        severity=severity,
        title=title.format(name=knowledge_base.name),
        message=message.format(error=knowledge_base.build_error or "Processing did not complete."),
        link={"name": "knowledge-base-detail", "params": {"knowledgeBaseId": str(knowledge_base.pk)}},
        dedupe_key=(
            f"kb:{knowledge_base.pk}:{knowledge_base.status}:"
            f"{(knowledge_base.validated_at or knowledge_base.updated_at).isoformat()}"
        ),
    )


def on_analysis_finished(sender, analysis, **kwargs) -> None:
    ok = analysis.status in ("completed", "partial")
    claim = analysis.claim
    notify(
        recipients=[analysis.requested_by],
        organization_id=analysis.project.organization_id,
        project_id=analysis.project_id,
        category=NotificationCategory.ANALYSIS_COMPLETED if ok else NotificationCategory.ANALYSIS_FAILED,
        severity=(
            NotificationSeverity.SUCCESS
            if analysis.status == "completed"
            else NotificationSeverity.WARNING if ok else NotificationSeverity.DANGER
        ),
        title=(
            f"Analysis of {claim.reference or claim.title} "
            + ("finished" if analysis.status == "completed" else "finished with failed strands" if ok else analysis.status)
        ),
        message=(
            "Findings are unreviewed until a person accepts, amends or rejects them."
            if ok
            else (analysis.error_message or "The analysis did not complete.")
        ),
        link={"name": "claim-detail", "params": {"claimId": str(claim.pk)}, "query": {"tab": "analysis"}},
        dedupe_key=f"analysis:{analysis.pk}:{analysis.status}",
    )


def refresh_deadline_notifications(organization_id, *, today: date | None = None, force: bool = False) -> int:
    """Warn about notice deadlines computed from recorded claim dates.

    Uses the same deterministic notice-timing computation as the claim screen
    and the analysis engine — no model is involved. Only requirements with no
    identified notice are reported: a notice already given needs no reminder.
    """
    key = f"notifications:deadline-scan:{organization_id}"
    if not force and cache.get(key):
        return 0
    cache.set(key, True, DEADLINE_SCAN_INTERVAL_SECONDS)

    from claimiq.analysis.services.claim_analysis import compute_notice_timing, notice_events_for
    from claimiq.claims.models import AssessmentOutcome, Claim

    today = today or timezone.localdate()
    created = 0
    claims = (
        Claim.objects.select_related("project", "created_by")
        .filter(
            project__organization_id=organization_id,
            project__deleted_at__isnull=True,
            awareness_date__isnull=False,
            human_outcome=AssessmentOutcome.NOT_ASSESSED,
        )
        .exclude(project__contract_edition="")
    )
    for claim in claims:
        computation = compute_notice_timing(
            claim.project.contract_edition, claim.awareness_date, notice_events_for(claim)
        )
        for finding in computation.findings:
            if finding.status.value != "not_given" or finding.deadline is None:
                continue
            remaining = (finding.deadline - today).days
            if remaining < 0:
                category, severity, state = (
                    NotificationCategory.DEADLINE_PASSED, NotificationSeverity.DANGER, "passed"
                )
                title = f"{claim.reference or claim.title}: Clause {finding.requirement.clause_number} deadline passed"
            elif remaining <= DEADLINE_WARNING_DAYS:
                category, severity, state = (
                    NotificationCategory.DEADLINE_APPROACHING, NotificationSeverity.WARNING, "approaching"
                )
                title = (
                    f"{claim.reference or claim.title}: Clause {finding.requirement.clause_number} "
                    f"deadline in {remaining} day(s)"
                )
            else:
                continue
            recipients = project_members_with(claim.project_id, "claim.view")
            if claim.created_by_id:
                recipients.append(claim.created_by)
            created += notify(
                recipients=recipients,
                organization_id=organization_id,
                project_id=claim.project_id,
                category=category,
                severity=severity,
                title=title,
                message=(
                    f"{finding.requirement.description} was due on {finding.deadline.isoformat()}, "
                    f"computed from the recorded awareness date {claim.awareness_date.isoformat()}. "
                    f"No notice under this provision is recorded. Absence from the record is not "
                    f"proof that none was given."
                ),
                link={"name": "claim-detail", "params": {"claimId": str(claim.pk)}, "query": {"tab": "notices"}},
                dedupe_key=(
                    f"deadline:{claim.pk}:{finding.requirement.clause_number}:{state}:"
                    f"{finding.deadline.isoformat()}"
                ),
            )
    return created


def unread_count(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def mark_read(notifications_qs) -> int:
    return notifications_qs.filter(read_at__isnull=True).update(read_at=timezone.now())
