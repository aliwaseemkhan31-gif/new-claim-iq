"""A project's contract deadlines: the standard form's, and its own.

Reads the project's contract documents for amended notice periods and records
what it finds as suggestions. A person confirms or rejects each one; only
confirmed rows reach the deadline service.
"""
from __future__ import annotations

from typing import List

from claimiq.claims.domain.contract_terms import (
    CONTRACT_DOCUMENT_TYPES,
    Passage,
    is_particular_conditions_page,
    suggest_amendments,
)
from claimiq.claims.domain.notice_compliance import requirements_for_edition
from claimiq.claims.models import ContractDeadline
from claimiq.claims.services.deadlines import requirements_for_project
from claimiq.core.domain.errors import ValidationError


def effective_payload(project) -> List[dict]:
    """Every requirement the project's claims face, beside the standard period."""
    edition = project.contract_edition or ""
    standard = {
        (r.clause_number, r.obligation.value): r for r in requirements_for_edition(edition)
    } if edition else {}
    rows = []
    for requirement in requirements_for_project(project):
        base = standard.get((requirement.clause_number, requirement.obligation.value))
        rows.append(
            {
                "clause_number": requirement.clause_number,
                "obligation": requirement.obligation.value,
                "title": requirement.label,
                "description": requirement.description,
                "period_days": requirement.period_days,
                "day_count": requirement.day_count.value,
                "runs_from": requirement.runs_from.value,
                "runs_from_clause": requirement.runs_from_clause,
                "applies_to": requirement.applies_to.value,
                "recipient": requirement.recipient,
                "is_condition_precedent": requirement.is_condition_precedent,
                "late_consequence": requirement.late_consequence,
                "standard_period_days": base.period_days if base else None,
                "source": requirement.source or "Standard form",
                "is_amended": requirement.is_amended,
            }
        )
    removed = [
        r for key, r in standard.items()
        if not any(
            (row["clause_number"], row["obligation"]) == key for row in rows
        )
    ]
    for requirement in removed:
        rows.append(
            {
                "clause_number": requirement.clause_number,
                "obligation": requirement.obligation.value,
                "title": requirement.label,
                "description": "Deleted by the project's contract documents.",
                "period_days": None,
                "day_count": requirement.day_count.value,
                "runs_from": requirement.runs_from.value,
                "runs_from_clause": requirement.runs_from_clause,
                "applies_to": requirement.applies_to.value,
                "recipient": requirement.recipient,
                "is_condition_precedent": requirement.is_condition_precedent,
                "late_consequence": "",
                "standard_period_days": requirement.period_days,
                "source": "Deleted",
                "is_amended": True,
            }
        )
    return rows


def _passages(project) -> List[Passage]:
    from claimiq.documents.models import Document, DocumentChunk, DocumentPage

    documents = Document.objects.filter(
        project=project,
        deleted_at__isnull=True,
        document_type__in=CONTRACT_DOCUMENT_TYPES,
        current_version__isnull=False,
    ).select_related("current_version")
    passages: List[Passage] = []
    for document in documents:
        for chunk in DocumentChunk.objects.filter(version=document.current_version).only(
            "text", "clause_number", "start_page"
        ):
            passages.append(
                Passage(
                    document_id=str(document.pk),
                    document_title=document.title,
                    document_type=document.document_type,
                    text=chunk.text,
                    clause_number=chunk.clause_number or "",
                    page=chunk.start_page,
                )
            )
        # Whole pages too. Chunking labels a passage with the clause it found,
        # and in a Particular Conditions section that is often wrong or
        # missing; the page keeps the amendment and its clause number together.
        for page in DocumentPage.objects.filter(version=document.current_version).only(
            "text", "page_number"
        ):
            text = page.text or ""
            if not text.strip():
                continue
            passages.append(
                Passage(
                    document_id=str(document.pk),
                    document_title=document.title,
                    document_type=document.document_type,
                    text=text,
                    page=page.page_number,
                    amending=is_particular_conditions_page(text),
                )
            )
    return passages


def scan(project, *, user) -> dict:
    """Read the project's contract documents and record suggested amendments.

    Re-running is safe: a suggestion already recorded for the same clause,
    obligation, period and document — in any status, including rejected — is
    not recorded again. A person who rejected a reading has answered it.
    """
    edition = project.contract_edition or ""
    if not edition:
        raise ValidationError(
            "Set the project's contract edition first: the standard periods to "
            "compare against depend on it.",
            details={"field": "contract_edition"},
        )
    standard = requirements_for_edition(edition)
    passages = _passages(project)
    suggestions = suggest_amendments(standard, passages)

    created = []
    for suggestion in suggestions:
        exists = ContractDeadline.objects.filter(
            project=project,
            clause_number=suggestion.clause_number,
            obligation=suggestion.obligation.value,
            period_days=suggestion.period_days,
            late_consequence=suggestion.late_consequence or "",
            source_document_id=suggestion.document_id,
        ).exists()
        if exists:
            continue
        created.append(
            ContractDeadline.objects.create(
                project=project,
                clause_number=suggestion.clause_number,
                obligation=suggestion.obligation.value,
                action=suggestion.action.value,
                status=ContractDeadline.Status.SUGGESTED,
                period_days=suggestion.period_days,
                day_count=suggestion.day_count.value if suggestion.day_count else "",
                note=suggestion.note,
                late_consequence=suggestion.late_consequence or "",
                source_document_id=suggestion.document_id,
                source_page=suggestion.page,
                source_excerpt=suggestion.excerpt,
                created_by=user,
            )
        )
    documents = {p.document_id for p in passages}
    return {
        "documents_read": len(documents),
        "passages_read": len(passages),
        "found": len(suggestions),
        "created": len(created),
        "created_ids": [str(row.pk) for row in created],
    }
