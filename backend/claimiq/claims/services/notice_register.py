"""Every notice in a project, with the dates it bears and how it stands.

The register a claims team keeps by hand: which notices were given, on what
date, under which clause, for which claim — and whether each one met its
deadline. The deadline is the deadline service's answer for the notice's
claim, so the register cannot disagree with the claim's own checklist.
"""
from __future__ import annotations

from typing import Dict, List

from claimiq.claims.services.deadlines import assess_claim, requirements_for_project


def register(project) -> List[dict]:
    from claimiq.correspondence.models import Notice

    notices = list(
        Notice.objects.filter(project=project, correspondence__deleted_at__isnull=True)
        .select_related("correspondence__document", "claim")
        .order_by("correspondence__sent_date", "created_at")
    )

    # One assessment per claim; each finding names the record it counted.
    requirements = requirements_for_project(project)
    findings_by_record: Dict[str, list] = {}
    for claim in {n.claim for n in notices if n.claim_id and n.claim.deleted_at is None}:
        for finding in assess_claim(claim, project_requirements=requirements):
            if finding.notice is not None:
                findings_by_record.setdefault(finding.notice.key, []).append(finding)

    rows = []
    for notice in notices:
        item = notice.correspondence
        counted = [
            f
            for f in findings_by_record.get(str(item.id), [])
            if f.requirement.clause_number == notice.clause_number
        ]
        finding = counted[0] if counted else None
        if notice.claim_id is None:
            status, detail = "unlinked", "Not linked to a claim, so no deadline applies to it yet."
        elif finding is None:
            status, detail = "not_counted", (
                "Not the notice the deadline was assessed on: an earlier notice under "
                "this clause, or one under another clause, was used."
            )
        else:
            status, detail = finding.status.value, finding.summary()
            if finding.deadline:
                detail += f" Deadline {finding.deadline.isoformat()}."

        rows.append(
            {
                "id": str(notice.pk),
                "correspondence": str(item.pk),
                "document": str(item.document_id) if item.document_id else None,
                "document_title": item.document.title if item.document_id else None,
                "reference": item.reference,
                "subject": item.subject,
                "sender": item.sender_raw,
                "recipient": item.recipient_raw,
                "letter_date": item.sent_date.isoformat() if item.sent_date else None,
                "received_date": item.received_date.isoformat() if item.received_date else None,
                "clause_number": notice.clause_number,
                "obligation": notice.obligation,
                "is_confirmed_notice": notice.is_confirmed_notice,
                "event_description": item.summary,
                "claim": str(notice.claim_id) if notice.claim_id else None,
                "claim_title": notice.claim.title if notice.claim_id else None,
                "claim_reference": notice.claim.reference if notice.claim_id else None,
                "status": status,
                "detail": detail,
                "deadline": finding.deadline.isoformat() if finding and finding.deadline else None,
                "days_used": finding.days_used if finding else None,
                "days_late": finding.days_late if finding else None,
            }
        )
    return rows
