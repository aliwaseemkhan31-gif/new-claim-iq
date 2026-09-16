"""Report generation: snapshot the record, compose, render, store.

Generation is deterministic and involves no model call: the snapshot is read
from the database, the document is composed by pure functions, and rendering
adds nothing. AI findings appear exactly as stored, with their review state.
"""
from __future__ import annotations

from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from claimiq.core.domain.errors import ValidationError
from claimiq.core.logging import get_logger
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.reports.domain.claim_report import (
    COMPLIANCE_LABELS,
    build_claim_report,
    build_claims_register,
)
from claimiq.reports.models import Report, ReportType
from claimiq.reports.services.renderers import render_docx, render_pdf

logger = get_logger("reports.generation")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _edition_label(code: str) -> str | None:
    if not code:
        return None
    return DEFAULT_REGISTRY.get_edition(code).label if DEFAULT_REGISTRY.has_edition(code) else code


def _project_snapshot(project) -> dict[str, Any]:
    from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus

    return {
        "id": str(project.pk),
        "name": project.name,
        "code": project.code,
        "edition_code": project.contract_edition or None,
        "edition_label": _edition_label(project.contract_edition),
        "standard_form_available": bool(project.contract_edition)
        and KnowledgeBase.objects.filter(
            organization_id=project.organization_id,
            edition_code=project.contract_edition,
            status=KnowledgeBaseStatus.PUBLISHED,
        ).exists(),
    }


def _latest_analysis(claim):
    from claimiq.analysis.models import ClaimAnalysis

    return (
        ClaimAnalysis.objects.filter(claim=claim, status__in=["completed", "partial", "failed"])
        .order_by("-finished_at", "-created_at")
        .first()
    )


def claim_snapshot(claim) -> dict[str, Any]:
    from collections import defaultdict

    from claimiq.analysis.models import AIFinding, FindingReview
    from claimiq.analysis.services.claim_analysis import (
        compute_evidence_gaps,
        compute_notice_timing,
        evidence_items_for,
        notice_events_for,
        review_outcome,
    )
    from claimiq.claims.api.views import _render_entry
    from claimiq.claims.services.timeline import build_project_chronology

    project = claim.project
    edition = project.contract_edition

    if not edition:
        notice: dict[str, Any] = {
            "note": "The project has not declared its governing contract edition, so notice periods cannot be determined."
        }
    else:
        computation = compute_notice_timing(edition, claim.awareness_date, notice_events_for(claim))
        notice = (
            computation.computed
            if computation.findings
            else {
                "note": (
                    f"No notice requirements are registered for {_edition_label(edition)}. "
                    f"Requirements from another edition are not substituted."
                )
            }
        )

    gap_outcome, _report = compute_evidence_gaps(claim.claim_type, evidence_items_for(claim))
    chronology = build_project_chronology(
        project_id=project.pk, claim_id=claim.pk, include_unreviewed=True, kinds=None
    )

    analysis_data = None
    run = _latest_analysis(claim)
    if run is not None:
        findings = list(AIFinding.objects.filter(analysis=run).order_by("sequence"))
        reviews: dict = defaultdict(list)
        for review in FindingReview.objects.filter(finding__analysis=run).order_by("created_at"):
            reviews[review.finding_id].append(review)
        by_strand: dict = defaultdict(list)
        for finding in findings:
            history = reviews.get(finding.pk, [])
            outcome = review_outcome(finding, history)
            by_strand[finding.strand_result_id].append(
                {
                    "statement": finding.statement,
                    "epistemic_status": finding.epistemic_status,
                    "review_state": outcome.state.value,
                    "effective_statement": outcome.effective_statement,
                    "review_reason": history[-1].reason if history else None,
                    "citations": [c.get("rendered") for c in finding.citations or [] if c.get("rendered")],
                }
            )
        analysis_data = {
            "id": str(run.pk),
            "status": run.status,
            "finished_at": _iso(run.finished_at),
            "llm_model": run.llm_model,
            "strands": [
                {
                    "strand": row.strand,
                    "status": row.status,
                    "confidence": row.confidence or None,
                    "confidence_reasons": row.confidence_reasons or [],
                    "summary": row.summary,
                    "insufficient_evidence": row.insufficient_evidence,
                    "skip_reason": row.skip_reason,
                    "error_message": row.error_message,
                    "findings": by_strand.get(row.pk, []),
                }
                for row in run.strands.order_by("sequence")
            ],
        }

    return {
        "claim": {
            "id": str(claim.pk),
            "reference": claim.reference,
            "title": claim.title,
            "description": claim.description,
            "claim_type": claim.claim_type,
            "claim_type_label": claim.get_claim_type_display(),
            "status_label": claim.get_status_display(),
            "claimant": claim.claimant.name if claim.claimant_id else None,
            "respondent": claim.respondent.name if claim.respondent_id else None,
            "event_date": _iso(claim.event_date),
            "awareness_date": _iso(claim.awareness_date),
            "notice_date": _iso(claim.notice_date),
            "submission_date": _iso(claim.submission_date),
            "amount_claimed": str(claim.amount_claimed) if claim.amount_claimed is not None else None,
            "currency": claim.currency,
            "time_claimed_days": claim.time_claimed_days,
            "contractual_basis": list(claim.contractual_basis or []),
        },
        "project": _project_snapshot(project),
        "notice": notice,
        "evidence": [
            {
                "title": e.title,
                "relevance": e.get_relevance_display(),
                "weight": e.weight or None,
                "document_title": e.document.title if e.document_id else None,
                "page_number": e.page_number,
                "reviewed": e.is_reviewed,
            }
            for e in claim.evidence.select_related("document")
        ],
        "gaps": {**gap_outcome.computed, "summary": gap_outcome.summary},
        "chronology": [_render_entry(e) for e in chronology.entries],
        "analysis": analysis_data,
        "issues": [
            {
                "title": issue.title,
                "category_label": issue.get_category_display(),
                "outcome": issue.human_outcome,
                "assessment": issue.human_assessment,
            }
            for issue in claim.issues.all()
        ],
        "human": {
            "outcome": claim.human_outcome,
            "assessment": claim.human_assessment,
            "assessed_by": (claim.assessed_by.full_name or claim.assessed_by.email) if claim.assessed_by_id else None,
            "assessed_at": _iso(claim.assessed_at),
        },
    }


def register_snapshot(project) -> dict[str, Any]:
    from claimiq.analysis.models import AIFinding
    from claimiq.analysis.services.claim_analysis import compute_notice_timing, notice_events_for
    from claimiq.claims.models import Claim

    rows = []
    for claim in Claim.objects.filter(project=project).order_by("reference", "created_at"):
        if not project.contract_edition:
            notice_summary = "Edition not declared"
        elif claim.awareness_date is None:
            notice_summary = "Awareness date not recorded"
        else:
            computation = compute_notice_timing(
                project.contract_edition, claim.awareness_date, notice_events_for(claim)
            )
            notice_summary = "; ".join(
                f"{f.requirement.clause_number}: {COMPLIANCE_LABELS.get(f.status.value, f.status.value)}"
                for f in computation.findings
            ) or "No requirements registered for this edition"
        run = _latest_analysis(claim)
        rows.append(
            {
                "reference": claim.reference,
                "title": claim.title,
                "claim_type_label": claim.get_claim_type_display(),
                "status_label": claim.get_status_display(),
                "amount_claimed": str(claim.amount_claimed) if claim.amount_claimed is not None else None,
                "currency": claim.currency,
                "time_claimed_days": claim.time_claimed_days,
                "notice_summary": notice_summary,
                "outcome": claim.human_outcome,
                "analysis_status": run.status if run else None,
                "unreviewed_findings": (
                    AIFinding.objects.filter(analysis=run, reviews__isnull=True).count() if run else 0
                ),
            }
        )
    return {"project": _project_snapshot(project), "claims": rows}


@transaction.atomic
def generate_report(*, report_type: str, project, user, claim=None) -> Report:
    """Snapshot, compose, render and store a new report version."""
    from claimiq.projects.models import Project

    if report_type not in ReportType.values:
        raise ValidationError(
            "Unknown report type.", details={"field": "report_type", "valid": list(ReportType.values)}
        )
    if report_type == ReportType.CLAIM_ASSESSMENT and claim is None:
        raise ValidationError("A claim assessment report needs a claim.", details={"field": "claim"})
    if report_type == ReportType.CLAIMS_REGISTER:
        claim = None

    # Serialise version numbering per project.
    Project.objects.select_for_update().filter(pk=project.pk).first()
    previous = (
        Report.objects.filter(project=project, claim=claim, report_type=report_type)
        .order_by("-version_number")
        .first()
    )
    version = previous.version_number + 1 if previous else 1

    base = {
        "generated_at": timezone.now().isoformat(timespec="seconds"),
        "generated_by": user.full_name or user.email,
        "version": version,
    }
    if report_type == ReportType.CLAIM_ASSESSMENT:
        snapshot = {**claim_snapshot(claim), **base}
        document = build_claim_report(snapshot)
    else:
        snapshot = {**register_snapshot(project), **base}
        document = build_claims_register(snapshot)

    data = document.to_dict()
    report = Report(
        organization_id=project.organization_id,
        project=project,
        claim=claim,
        report_type=report_type,
        title=document.title,
        version_number=version,
        snapshot=snapshot,
        document=data,
        generated_by=user,
        created_by=user,
    )
    stem = slugify(f"{document.title}-v{version}")[:80] or "report"
    report.pdf_file.save(f"{stem}.pdf", ContentFile(render_pdf(data)), save=False)
    report.docx_file.save(f"{stem}.docx", ContentFile(render_docx(data)), save=False)
    report.save()
    logger.info(
        "reports.generated",
        extra={"report_id": str(report.pk), "type": report_type, "version": version},
    )
    return report
