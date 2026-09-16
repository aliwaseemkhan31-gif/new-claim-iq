"""Tests for report composition."""
from __future__ import annotations

from claimiq.reports.domain.claim_report import (
    NOT_RECORDED,
    build_claim_report,
    build_claims_register,
)


def snapshot(**overrides):
    base = {
        "claim": {
            "reference": "EOT-7",
            "title": "Late structural drawings",
            "claim_type_label": "Extension of Time",
            "status_label": "Submitted",
            "claimant": "Acme Construction",
            "respondent": "Oversight Engineering",
            "awareness_date": "2026-03-01",
            "amount_claimed": "125000.00",
            "currency": "USD",
            "time_claimed_days": 21,
            "contractual_basis": ["8.5", "20.2.1"],
            "description": "Drawings for the culvert were issued six weeks late.",
        },
        "project": {"name": "N-55", "edition_label": "FIDIC Red Book 1987 (4th Edition)", "standard_form_available": True},
        "notice": {
            "findings": [
                {"clause_number": "53.1", "description": "Notice of intention to claim", "status": "not_given",
                 "deadline": "2026-03-29", "assumptions": [], "warnings": ["No dated notice was identified."]}
            ]
        },
        "evidence": [],
        "gaps": {"elements": [{"label": "Causation", "status": "missing", "is_essential": True, "is_gap": True,
                               "suggestion": "Programme showing the critical path through the culvert."}]},
        "chronology": [],
        "analysis": {
            "status": "completed", "finished_at": "2026-09-15", "llm_model": "qwen2.5:3b-instruct",
            "strands": [
                {"strand": "entitlement", "status": "completed", "confidence": "medium",
                 "confidence_reasons": ["Only a single source was cited."], "summary": "Clause 44 may apply.",
                 "insufficient_evidence": False,
                 "findings": [
                     {"statement": "Delay by the Engineer can entitle an extension.", "epistemic_status": "inference",
                      "review_state": "unreviewed", "citations": ["[red-book-1987 — Clause 44.1 — p.20]"]},
                     {"statement": "Notice was late.", "epistemic_status": "fact", "review_state": "amended",
                      "effective_statement": "No notice is recorded.", "citations": []},
                 ]},
                {"strand": "quantum", "status": "skipped", "skip_reason": "No amount claimed."},
                {"strand": "causation", "status": "failed", "error_message": "The model response was not valid."},
            ],
        },
        "issues": [],
        "human": {"outcome": "not_assessed"},
        "generated_at": "2026-09-15T10:00:00Z",
        "generated_by": "reviewer@example.com",
        "version": 1,
    }
    base.update(overrides)
    return base


def all_blocks(document):
    return [block for section in document.sections for block in section.blocks]


def section(document, fragment):
    return next(s for s in document.sections if fragment in s.heading)


def test_sections_follow_the_response_structure_in_order():
    headings = [s.heading for s in build_claim_report(snapshot()).sections]
    assert headings[0].startswith("1. Notice or claim received")
    assert headings[-1].startswith("12. Further information required")
    assert any("Notice requirements" in h for h in headings)
    assert any("Determination" in h for h in headings)


def test_every_ai_finding_carries_its_review_state():
    findings = [f for b in all_blocks(build_claim_report(snapshot())) if b.kind == "findings" for f in b.findings]
    assert findings[0]["review_state"] == "unreviewed"
    assert "UNREVIEWED" in findings[0]["review"]
    assert findings[1]["effective_statement"] == "No notice is recorded."


def test_unreviewed_count_is_stated():
    document = build_claim_report(snapshot())
    notes = [b.text for b in section(document, "entitlement").blocks if b.kind == "note"]
    assert any("1 of 2 finding(s) have not been reviewed" in n for n in notes)


def test_skipped_and_failed_strands_are_stated_not_dropped():
    document = build_claim_report(snapshot())
    assert "No amount claimed" in section(document, "quantum").blocks[0].text
    failed = section(document, "Causation").blocks[0]
    assert failed.tone == "danger" and "did not complete" in failed.text


def test_missing_values_say_not_recorded():
    document = build_claim_report(snapshot(claim={"title": "Bare claim"}))
    facts = dict(section(document, "received").blocks[0].facts)
    assert facts["Amount claimed"] == NOT_RECORDED
    assert facts["Claimant"] == NOT_RECORDED
    event = dict(section(document, "event").blocks[0].facts)
    assert event["Awareness date"].startswith(NOT_RECORDED)


def test_absence_of_a_determination_is_stated():
    document = build_claim_report(snapshot())
    notes = [b.text for b in section(document, "Determination").blocks if b.kind == "note"]
    assert any("This report is not a determination" in n for n in notes)


def test_no_analysis_is_stated_in_each_strand_section():
    document = build_claim_report(snapshot(analysis=None))
    assert "No AI analysis has been run" in section(document, "Time impact").blocks[0].text


def test_claimant_description_is_labelled_as_not_evidence():
    notes = [b.text for b in section(build_claim_report(snapshot()), "event").blocks if b.kind == "note"]
    assert any("It is not evidence" in n for n in notes)


def test_further_information_collects_real_gaps_only():
    items = section(build_claim_report(snapshot()), "Further information").blocks[0].items
    assert "Any notice given under Clause 53.1 (Notice of intention to claim)." in items
    assert "Programme showing the critical path through the culvert." in items
    assert not any("aware" in i for i in items), "the awareness date is recorded"


def test_register_totals_per_currency_and_counts_unassessed():
    document = build_claims_register(
        {
            "project": {"name": "N-55"},
            "claims": [
                {"reference": "A", "amount_claimed": "100.00", "currency": "PKR", "outcome": "not_assessed"},
                {"reference": "B", "amount_claimed": "50.50", "currency": "PKR", "outcome": "substantiated",
                 "analysis_status": "completed", "unreviewed_findings": 2},
                {"reference": "C", "amount_claimed": "10", "currency": "USD"},
            ],
        }
    )
    facts = dict(document.sections[0].blocks[0].facts)
    assert facts["Amount claimed (PKR)"] == "PKR 150.50"
    assert facts["Amount claimed (USD)"] == "USD 10.00"
    assert facts["Not yet assessed"] == "2"
    rows = document.sections[1].blocks[0].rows
    assert rows[1][-1] == "completed, 2 unreviewed"
    assert rows[2][-1] == "Not run"


def test_document_serialises_to_plain_data():
    data = build_claim_report(snapshot()).to_dict()
    assert data["sections"][0]["blocks"][0]["kind"] == "facts"
    assert data["disclaimer"]
