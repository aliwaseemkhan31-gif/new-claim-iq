"""Report documents: structure built from a data snapshot.

A report is composed here as a neutral document — sections of paragraphs, fact
lists, tables, findings and notes — and rendered to PDF, DOCX or the screen
elsewhere. Keeping composition pure means the rules that matter can be tested
directly: every AI finding carries its review state, missing values say "Not
recorded" rather than disappearing, and the absence of a human determination
is stated, not implied.

The claim report follows the response structure in the organization's
Notice/Claim Management Hierarchy: what was received, the event, the
contractual basis, notice, evidence, responsibility and causation, time, cost,
the determination, and what further information is required.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

NOT_RECORDED = "Not recorded"


@dataclass(frozen=True)
class Block:
    """One unit of report content.

    ``kind`` is one of: ``paragraph``, ``facts``, ``table``, ``findings``,
    ``note``, ``list``.
    """

    kind: str
    text: str = ""
    tone: str = "neutral"
    facts: Tuple[Tuple[str, str], ...] = ()
    columns: Tuple[str, ...] = ()
    rows: Tuple[Tuple[str, ...], ...] = ()
    items: Tuple[str, ...] = ()
    findings: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Section:
    heading: str
    blocks: Tuple[Block, ...]


@dataclass(frozen=True)
class ReportDocument:
    title: str
    subtitle: str
    meta: Tuple[Tuple[str, str], ...]
    sections: Tuple[Section, ...]
    disclaimer: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Labels and formatting
# ---------------------------------------------------------------------------

EPISTEMIC_LABELS = {
    "fact": "Fact (established by the cited source)",
    "inference": "Inference (reasoned from the cited sources)",
    "opinion": "Opinion",
    "unknown": "Unknown (not established by the sources)",
}

REVIEW_LABELS = {
    "unreviewed": "UNREVIEWED AI finding — not checked by a person",
    "accepted": "Accepted by reviewer",
    "amended": "Amended by reviewer",
    "rejected": "Rejected by reviewer",
}

OUTCOME_LABELS = {
    "substantiated": "Substantiated — accept",
    "partially_substantiated": "Partially substantiated — partial entitlement",
    "unsubstantiated": "Unsubstantiated — reject",
    "insufficient_evidence": "Insufficient evidence — further information required",
    "not_assessed": "Not yet assessed",
}

COMPLIANCE_LABELS = {
    "compliant": "Given in time",
    "late": "Given late",
    "not_given": "No notice recorded",
    "indeterminate": "Cannot be determined",
    "not_required": "Not required",
}

REPORT_DISCLAIMER = (
    "This report assembles the recorded claim file. Computed timings are derived from "
    "recorded dates. AI findings are an aid to professional judgement, limited to the "
    "sources they cite; a finding marked unreviewed has not been checked by a person. "
    "Nothing in this report is a determination unless a human determination is recorded "
    "in the section so titled."
)


def text(value: Any) -> str:
    if value is None:
        return NOT_RECORDED
    rendered = str(value).strip()
    return rendered if rendered else NOT_RECORDED


def money(amount: Any, currency: Optional[str]) -> str:
    if amount in (None, ""):
        return NOT_RECORDED
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return text(amount)
    return f"{(currency or '').strip()} {value:,.2f}".strip()


def days(value: Any) -> str:
    return NOT_RECORDED if value in (None, "") else f"{value} day(s)"


# ---------------------------------------------------------------------------
# Claim assessment report
# ---------------------------------------------------------------------------

#: Report section heading → analysis strands it presents.
STRAND_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Responsibility and contractual entitlement", ("entitlement",)),
    ("Causation", ("causation",)),
    ("Time impact", ("time_impact",)),
    ("Cost impact (quantum)", ("quantum",)),
    ("Arguments the respondent may raise", ("counterarguments",)),
)


def _finding_rows(strand: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    rendered: List[Mapping[str, Any]] = []
    for finding in strand.get("findings") or ():
        state = finding.get("review_state") or "unreviewed"
        rendered.append(
            {
                "statement": finding.get("statement", ""),
                "effective_statement": (
                    finding.get("effective_statement") if state == "amended" else None
                ),
                "epistemic_status": EPISTEMIC_LABELS.get(
                    finding.get("epistemic_status", ""), text(finding.get("epistemic_status"))
                ),
                "review": REVIEW_LABELS.get(state, state),
                "review_state": state,
                "review_reason": finding.get("review_reason") or None,
                "citations": [c for c in (finding.get("citations") or ()) if c],
            }
        )
    return tuple(rendered)


def _strand_blocks(strand: Optional[Mapping[str, Any]]) -> List[Block]:
    if strand is None:
        return [Block("note", text="This analysis strand was not run.", tone="neutral")]
    status = strand.get("status")
    if status == "skipped":
        return [Block("note", text=f"Not applicable: {text(strand.get('skip_reason'))}", tone="neutral")]
    if status == "failed":
        return [
            Block(
                "note",
                text=f"This strand did not complete: {text(strand.get('error_message'))}",
                tone="danger",
            )
        ]
    blocks = [
        Block(
            "facts",
            facts=(
                ("Confidence", text(strand.get("confidence"))),
                ("Why", "; ".join(strand.get("confidence_reasons") or ()) or NOT_RECORDED),
                (
                    "Evidence sufficiency",
                    "The sources were insufficient to answer"
                    if strand.get("insufficient_evidence")
                    else "Sources addressed the question",
                ),
            ),
        )
    ]
    if strand.get("summary"):
        blocks.append(Block("paragraph", text=str(strand["summary"])))
    findings = _finding_rows(strand)
    if findings:
        blocks.append(Block("findings", findings=findings))
    return blocks


def build_claim_report(snapshot: Mapping[str, Any]) -> ReportDocument:
    """Compose the claim assessment report from a snapshot.

    Expected snapshot keys: ``claim``, ``project``, ``notice``, ``evidence``,
    ``gaps``, ``chronology``, ``analysis`` (or None), ``issues``, ``human``,
    ``generated_at``, ``generated_by``, ``version``.
    """
    claim = snapshot.get("claim") or {}
    project = snapshot.get("project") or {}
    notice = snapshot.get("notice") or {}
    evidence = snapshot.get("evidence") or []
    gaps = snapshot.get("gaps") or {}
    chronology = snapshot.get("chronology") or []
    analysis = snapshot.get("analysis")
    issues = snapshot.get("issues") or []
    human = snapshot.get("human") or {}

    sections: List[Section] = []
    number = 0

    def add(heading: str, blocks: Sequence[Block]) -> None:
        nonlocal number
        number += 1
        sections.append(Section(f"{number}. {heading}", tuple(blocks)))

    # 1. What was received
    add(
        "Notice or claim received",
        [
            Block(
                "facts",
                facts=(
                    ("Reference", text(claim.get("reference"))),
                    ("Title", text(claim.get("title"))),
                    ("Type of claim", text(claim.get("claim_type_label"))),
                    ("Claimant", text(claim.get("claimant"))),
                    ("Respondent", text(claim.get("respondent"))),
                    ("Status", text(claim.get("status_label"))),
                    ("Amount claimed", money(claim.get("amount_claimed"), claim.get("currency"))),
                    ("Time claimed", days(claim.get("time_claimed_days"))),
                    ("Notice date", text(claim.get("notice_date"))),
                    ("Submission date", text(claim.get("submission_date"))),
                ),
            )
        ],
    )

    # 2. The event
    event_blocks: List[Block] = [
        Block(
            "facts",
            facts=(
                ("Event date", text(claim.get("event_date"))),
                (
                    "Awareness date",
                    f"{claim['awareness_date']} (the date notice periods run from; commonly disputed)"
                    if claim.get("awareness_date")
                    else f"{NOT_RECORDED} — notice timing cannot be determined without it",
                ),
            ),
        )
    ]
    if claim.get("description"):
        event_blocks.append(Block("paragraph", text=str(claim["description"])))
        event_blocks.append(
            Block(
                "note",
                text="The description above is the claimant's account as recorded. It is not evidence.",
                tone="info",
            )
        )
    if chronology:
        event_blocks.append(
            Block(
                "table",
                columns=("Date", "Event", "Source"),
                rows=tuple(
                    (
                        text(entry.get("date_display") or entry.get("date")),
                        text(entry.get("title"))
                        + (" (unconfirmed AI extraction)" if entry.get("needs_review") else ""),
                        (
                            f"{entry['source_document_title']}"
                            + (f", p.{entry['source_page']}" if entry.get("source_page") else "")
                            if entry.get("source_document_title")
                            else "No source recorded"
                        ),
                    )
                    for entry in chronology
                ),
            )
        )
    add("The event", event_blocks)

    # 3. Contractual basis
    basis = claim.get("contractual_basis") or []
    add(
        "Contractual basis",
        [
            Block(
                "facts",
                facts=(
                    (
                        "Governing conditions",
                        text(project.get("edition_label"))
                        if project.get("edition_label")
                        else "Not declared — standard-form provisions cannot be applied",
                    ),
                    ("Clauses relied on", ", ".join(basis) if basis else NOT_RECORDED),
                    (
                        "Standard-form text available",
                        "Yes — a published knowledge base for this edition"
                        if project.get("standard_form_available")
                        else "No published knowledge base for this edition",
                    ),
                ),
            ),
            Block(
                "note",
                text=(
                    "The project's Particular or Supplementary Conditions govern where they "
                    "amend the standard form. Check them before relying on standard-form text."
                ),
                tone="info",
            ),
        ],
    )

    # 4. Notice
    notice_blocks: List[Block] = []
    findings = notice.get("findings") or []
    if findings:
        notice_blocks.append(
            Block(
                "table",
                columns=("Clause", "Requirement", "Outcome", "Deadline", "Notes"),
                rows=tuple(
                    (
                        text(f.get("clause_number")),
                        text(f.get("description")),
                        COMPLIANCE_LABELS.get(f.get("status", ""), text(f.get("status")))
                        + (" — condition precedent" if f.get("is_condition_precedent") else ""),
                        text(f.get("deadline")),
                        "; ".join(list(f.get("assumptions") or []) + list(f.get("warnings") or []))
                        or "—",
                    )
                    for f in findings
                ),
            )
        )
        notice_blocks.append(
            Block(
                "note",
                text=(
                    "Timing is computed from recorded dates, not by the AI. A late or missing "
                    "notice does not automatically defeat a claim: the wording, the stated "
                    "consequence, and any waiver or relief provision must be checked."
                ),
                tone="info",
            )
        )
    else:
        notice_blocks.append(
            Block("note", text=text(notice.get("note") or "Notice compliance was not computed."), tone="warning")
        )
    add("Notice requirements", notice_blocks)

    # 5. Evidence
    evidence_blocks: List[Block] = []
    if evidence:
        evidence_blocks.append(
            Block(
                "table",
                columns=("Evidence", "Relevance", "Weight", "Document", "Reviewed"),
                rows=tuple(
                    (
                        text(item.get("title")),
                        text(item.get("relevance")),
                        text(item.get("weight")),
                        (
                            f"{item['document_title']}"
                            + (f", p.{item['page_number']}" if item.get("page_number") else "")
                            if item.get("document_title")
                            else NOT_RECORDED
                        ),
                        "Yes" if item.get("reviewed") else "No",
                    )
                    for item in evidence
                ),
            )
        )
    else:
        evidence_blocks.append(
            Block(
                "note",
                text="No evidence is linked to this claim. A claim is not proven because it is asserted.",
                tone="warning",
            )
        )
    if gaps.get("elements"):
        if gaps.get("summary"):
            evidence_blocks.append(Block("paragraph", text=str(gaps["summary"])))
        evidence_blocks.append(
            Block(
                "table",
                columns=("What must be established", "Status", "Essential"),
                rows=tuple(
                    (text(e.get("label")), text(e.get("status")), "Yes" if e.get("is_essential") else "No")
                    for e in gaps["elements"]
                ),
            )
        )
    add("Facts and evidence reviewed", evidence_blocks)

    # 6–10. AI analysis strands
    strands: Dict[str, Mapping[str, Any]] = {}
    if analysis:
        strands = {s.get("strand"): s for s in analysis.get("strands") or ()}
        total = sum(len(s.get("findings") or ()) for s in strands.values())
        unreviewed = sum(
            1
            for s in strands.values()
            for f in (s.get("findings") or ())
            if (f.get("review_state") or "unreviewed") == "unreviewed"
        )
        intro = Block(
            "note",
            tone="warning" if unreviewed else "info",
            text=(
                f"From AI analysis run of {text(analysis.get('finished_at'))} "
                f"({text(analysis.get('status'))}, model {text(analysis.get('llm_model'))}). "
                f"{unreviewed} of {total} finding(s) have not been reviewed by a person."
            ),
        )
    for index, (heading, codes) in enumerate(STRAND_SECTIONS):
        if not analysis:
            blocks = [
                Block("note", text="No AI analysis has been run for this claim.", tone="neutral")
            ]
        else:
            blocks = [intro] if index == 0 else []
            for code in codes:
                blocks.extend(_strand_blocks(strands.get(code)))
        add(heading, blocks)

    # 11. Determination
    determination: List[Block] = [
        Block(
            "facts",
            facts=(
                ("Outcome", OUTCOME_LABELS.get(human.get("outcome") or "not_assessed", text(human.get("outcome")))),
                ("Assessed by", text(human.get("assessed_by"))),
                ("Assessed on", text(human.get("assessed_at"))),
            ),
        )
    ]
    if human.get("assessment"):
        determination.append(Block("paragraph", text=str(human["assessment"])))
    if (human.get("outcome") or "not_assessed") == "not_assessed":
        determination.append(
            Block(
                "note",
                text="No human determination has been recorded. This report is not a determination.",
                tone="warning",
            )
        )
    if issues:
        determination.append(
            Block(
                "table",
                columns=("Issue", "Category", "Outcome", "Assessment"),
                rows=tuple(
                    (
                        text(i.get("title")),
                        text(i.get("category_label")),
                        OUTCOME_LABELS.get(i.get("outcome") or "not_assessed", text(i.get("outcome"))),
                        text(i.get("assessment")),
                    )
                    for i in issues
                ),
            )
        )
    add("Determination", determination)

    # 12. Further information required
    requests: List[str] = []
    if not claim.get("awareness_date"):
        requests.append("The date the claimant became aware of the event.")
    for f in findings:
        if f.get("status") == "not_given":
            requests.append(
                f"Any notice given under Clause {f.get('clause_number')} ({f.get('description')})."
            )
    for element in gaps.get("elements") or ():
        if element.get("is_gap") and element.get("suggestion"):
            requests.append(str(element["suggestion"]))
    for code, strand in strands.items():
        if strand.get("insufficient_evidence") and strand.get("status") == "completed":
            requests.append(f"Records addressing {code.replace('_', ' ')}: the sources did not answer it.")
    add(
        "Further information required",
        [Block("list", items=tuple(dict.fromkeys(requests)))]
        if requests
        else [Block("paragraph", text="No outstanding information requests were identified from the record.")],
    )

    reference = claim.get("reference") or claim.get("title") or "claim"
    return ReportDocument(
        title=f"Claim assessment — {reference}",
        subtitle=text(claim.get("title")),
        meta=(
            ("Project", text(project.get("name"))),
            ("Governing conditions", text(project.get("edition_label"))),
            ("Generated", text(snapshot.get("generated_at"))),
            ("Generated by", text(snapshot.get("generated_by"))),
            ("Report version", text(snapshot.get("version"))),
        ),
        sections=tuple(sections),
        disclaimer=REPORT_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Claims register
# ---------------------------------------------------------------------------


def build_claims_register(snapshot: Mapping[str, Any]) -> ReportDocument:
    """Compose a project's claims register.

    Expected snapshot keys: ``project``, ``claims`` (each with reference, title,
    type label, status label, amount, currency, time, notice summary, outcome,
    analysis status and unreviewed count), ``generated_at``, ``generated_by``,
    ``version``.
    """
    project = snapshot.get("project") or {}
    claims = snapshot.get("claims") or []

    totals: Dict[str, Decimal] = {}
    for claim in claims:
        amount = claim.get("amount_claimed")
        if amount in (None, ""):
            continue
        try:
            totals[claim.get("currency") or ""] = totals.get(claim.get("currency") or "", Decimal("0")) + Decimal(str(amount))
        except InvalidOperation:
            continue

    summary_facts: List[Tuple[str, str]] = [("Claims", str(len(claims)))]
    for currency, total in sorted(totals.items()):
        summary_facts.append((f"Amount claimed ({currency or 'no currency'})", money(total, currency)))
    summary_facts.append(
        (
            "Not yet assessed",
            str(sum(1 for c in claims if (c.get("outcome") or "not_assessed") == "not_assessed")),
        )
    )

    register = (
        Block(
            "table",
            columns=("Reference", "Title", "Type", "Status", "Claimed", "Time", "Notice", "Outcome", "AI analysis"),
            rows=tuple(
                (
                    text(c.get("reference")),
                    text(c.get("title")),
                    text(c.get("claim_type_label")),
                    text(c.get("status_label")),
                    money(c.get("amount_claimed"), c.get("currency")),
                    days(c.get("time_claimed_days")),
                    text(c.get("notice_summary")),
                    OUTCOME_LABELS.get(c.get("outcome") or "not_assessed", text(c.get("outcome"))),
                    (
                        f"{c['analysis_status']}, {c.get('unreviewed_findings', 0)} unreviewed"
                        if c.get("analysis_status")
                        else "Not run"
                    ),
                )
                for c in claims
            ),
        )
        if claims
        else Block("note", text="No claims are recorded on this project.", tone="neutral")
    )

    return ReportDocument(
        title=f"Claims register — {text(project.get('name'))}",
        subtitle=text(project.get("edition_label")),
        meta=(
            ("Project", text(project.get("name"))),
            ("Generated", text(snapshot.get("generated_at"))),
            ("Generated by", text(snapshot.get("generated_by"))),
            ("Report version", text(snapshot.get("version"))),
        ),
        sections=(
            Section("1. Summary", (Block("facts", facts=tuple(summary_facts)),)),
            Section("2. Register", (register,)),
        ),
        disclaimer=REPORT_DISCLAIMER,
    )
