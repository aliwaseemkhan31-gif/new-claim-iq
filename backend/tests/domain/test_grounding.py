"""Tests for citation and grounding validation (ADR 0005).

The property under test: a fabricated citation or quotation is *detected*, not
trusted. The legacy prototype had only a prompt instruction and no check.
"""
from __future__ import annotations

import pytest

from claimiq.core.domain.errors import (
    EditionMixingError,
    UncitedFactError,
    UnknownCitationError,
    UnsupportedQuotationError,
)
from claimiq.ai.domain.citations import (
    Citation,
    EpistemicStatus,
    Finding,
    SourceChunk,
    assign_refs,
    build_source_map,
    enforce_grounding,
    normalise_for_comparison,
    resolve_ref,
    render_sources_block,
    validate_findings,
    verify_quotation,
)

SOURCE_TEXT = (
    "The claiming Party shall give a Notice to the Engineer, describing the "
    "event or circumstance giving rise to the Claim. The Notice shall be given "
    "as soon as practicable, and no later than 28 days after the claiming Party "
    "became aware of the event or circumstance."
)


def kb_source(ref: str = "S1", edition: str = "red-book-2017") -> SourceChunk:
    return SourceChunk(
        ref=ref,
        text=SOURCE_TEXT,
        document_id="doc-1",
        document_title="FIDIC Red Book 2017",
        page_number=121,
        clause_number="20.2.1",
        edition=edition,
        is_knowledge_base=True,
    )


def fact(statement: str, *citations: Citation) -> Finding:
    return Finding(
        statement=statement, status=EpistemicStatus.FACT, citations=tuple(citations)
    )


# ---------------------------------------------------------------------------
# Closed-world citation identifiers
# ---------------------------------------------------------------------------


def test_unknown_reference_is_detected() -> None:
    """A citation outside the assembled set is a fabricated citation."""
    sources = build_source_map([kb_source("S1")])
    report = validate_findings([fact("Notice is required.", Citation("S7"))], sources)
    assert report.is_grounded is False
    assert report.unknown_refs == ["S7"]

    with pytest.raises(UnknownCitationError) as exc:
        enforce_grounding(report)
    assert exc.value.code == "ai_unknown_citation"
    assert "S7" in exc.value.details["unknown_refs"]


def test_known_reference_passes() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings([fact("Notice is required.", Citation("S1"))], sources)
    assert report.is_grounded is True
    enforce_grounding(report)  # must not raise


def test_decorated_reference_resolves_to_its_identifier() -> None:
    """Observed against a real model: it echoed the whole rendered source line.

    qwen2.5:3b-instruct cited
    "S1 [FIDIC Red Book 2017 - Clause 20.2.1 - p.121]" when the identifier was
    "S1". The intent is unambiguous, so treating it as a fabricated citation
    would be a false positive.
    """
    sources = build_source_map([kb_source("S1")])
    for decorated in (
        "S1 [FIDIC Red Book 2017 — Clause 20.2.1 — p.121]",
        "SOURCE ID: S1",
    ):
        report = validate_findings([fact("Notice required.", Citation(decorated))], sources)
        assert report.is_grounded is True, decorated


KNOWN = {"S1", "S2", "S3"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("S1", "S1"),
        ("  S1  ", "S1"),
        ("[S2]", "S2"),
        ("(S3)", "S3"),
        ("s1", "S1"),
        # Both forms observed from qwen2.5:3b-instruct on successive runs.
        ("S1 [FIDIC Red Book 2017 - Clause 20.2.1 - p.121]", "S1"),
        ("SOURCE ID: S1", "S1"),
        ("Source S2, page 4", "S2"),
    ],
)
def test_decorated_refs_resolve(raw: str, expected: str) -> None:
    assert resolve_ref(raw, KNOWN) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "S9",                       # not assembled
        "S9 [Some Document - p.1]",  # decorated but still not assembled
        "the contract generally",    # no token at all
        "",                          # empty
        "S1 and S2",                 # ambiguous: two known refs
    ],
)
def test_unresolvable_refs_return_none(raw: str) -> None:
    """Closed-world: resolution can only ever recognise an assembled source."""
    assert resolve_ref(raw, KNOWN) is None


def test_resolution_does_not_weaken_the_closed_world_check() -> None:
    """The point that matters: an invented reference must still fail."""
    sources = build_source_map([kb_source("S1")])

    for invented in (
        "S9",
        "S9 [Some Document - p.1]",
        "SOURCE ID: S9",
        "the contract generally",
        "",
    ):
        report = validate_findings([fact("x", Citation(invented))], sources)
        assert report.is_grounded is False, invented
        with pytest.raises(UnknownCitationError):
            enforce_grounding(report)


def test_unparseable_reference_is_reported_as_unknown() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings([fact("x", Citation("see the contract"))], sources)
    assert report.unknown_refs == ["see the contract"]


def test_sources_block_labels_the_identifier_separately() -> None:
    """The layout fix: nothing in the old line said where the token ended."""
    block = render_sources_block(assign_refs([kb_source("x")]))
    assert "SOURCE ID: S1" in block
    assert "Reference:" in block
    assert "Text:" in block


def test_assign_refs_produces_sequential_opaque_ids() -> None:
    raw = [kb_source("db-uuid-a"), kb_source("db-uuid-b")]
    assigned = assign_refs(raw)
    assert [c.ref for c in assigned] == ["S1", "S2"]
    assert assigned[0].text == raw[0].text


def test_duplicate_refs_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_source_map([kb_source("S1"), kb_source("S1")])


# ---------------------------------------------------------------------------
# Quotation verification
# ---------------------------------------------------------------------------


def test_genuine_quotation_verifies() -> None:
    assert verify_quotation("no later than 28 days after", SOURCE_TEXT) is True


def test_fabricated_quotation_fails() -> None:
    """Changing "28" to "42" is a fabricated quotation, not a paraphrase."""
    assert verify_quotation("no later than 42 days after", SOURCE_TEXT) is False


def test_modal_change_is_caught() -> None:
    """"shall" to "may" inverts an obligation and must not pass."""
    assert verify_quotation("The Notice may be given as soon as practicable", SOURCE_TEXT) is False


def test_typography_differences_are_tolerated() -> None:
    """Smart quotes and dashes differ between PDF and model output harmlessly."""
    source = SourceChunk(
        ref="S1",
        text="The Contractor’s Notice — given under Sub-Clause 20.2.1 — shall be in writing.",
        document_id="d",
        document_title="t",
        page_number=1,
    )
    assert verify_quotation("The Contractor's Notice - given under Sub-Clause 20.2.1", source.text)


def test_whitespace_differences_are_tolerated() -> None:
    assert verify_quotation("shall  give   a\nNotice to the Engineer", SOURCE_TEXT) is True


def test_very_short_quotation_is_not_verified() -> None:
    """Too little information for a match to prove anything."""
    assert verify_quotation("Notice", "totally unrelated text") is True


def test_unsupported_quotation_raises() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [fact("Notice within 42 days.", Citation("S1", quotation="no later than 42 days after"))],
        sources,
    )
    assert report.is_grounded is False
    with pytest.raises(UnsupportedQuotationError):
        enforce_grounding(report)


def test_verified_quotations_are_counted() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [fact("Notice required.", Citation("S1", quotation="no later than 28 days after"))],
        sources,
    )
    assert report.quotations_verified == 1
    assert report.is_grounded is True


def test_normalise_for_comparison_folds_typography() -> None:
    assert normalise_for_comparison("The  Contractor’s—Notice") == "the contractor's-notice"


# ---------------------------------------------------------------------------
# Fact grounding
# ---------------------------------------------------------------------------


def test_uncited_fact_is_rejected() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings([fact("The notice was served late.")], sources)
    assert report.uncited_facts == ["The notice was served late."]
    with pytest.raises(UncitedFactError):
        enforce_grounding(report)


def test_uncited_inference_is_rejected() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [Finding("The claim is likely time-barred.", EpistemicStatus.INFERENCE)], sources
    )
    assert report.is_grounded is False


def test_unknown_status_needs_no_citation() -> None:
    """"Insufficient evidence" is a valid, expected outcome — not a failure."""
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [
            Finding(
                "The date of the Contractor's awareness is not established by the "
                "available documents.",
                EpistemicStatus.UNKNOWN,
            )
        ],
        sources,
    )
    assert report.is_grounded is True
    enforce_grounding(report)


def test_opinion_needs_no_citation() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [Finding("The position is arguable.", EpistemicStatus.OPINION)], sources
    )
    assert report.is_grounded is True


# ---------------------------------------------------------------------------
# Edition agreement — ADR 0004 enforced at output
# ---------------------------------------------------------------------------


def test_citing_wrong_edition_raises() -> None:
    """Defence in depth: catches a filter dropped upstream."""
    sources = build_source_map([kb_source("S1", edition="red-book-1987")])
    report = validate_findings(
        [fact("Claims procedure applies.", Citation("S1"))],
        sources,
        expected_edition="red-book-2017",
    )
    assert report.edition_mismatches == [("S1", "red-book-1987")]
    with pytest.raises(EditionMixingError):
        enforce_grounding(report)


def test_matching_edition_passes() -> None:
    sources = build_source_map([kb_source("S1", edition="red-book-2017")])
    report = validate_findings(
        [fact("Claims procedure applies.", Citation("S1"))],
        sources,
        expected_edition="red-book-2017",
    )
    assert report.is_grounded is True


def test_project_document_is_not_edition_checked() -> None:
    source = SourceChunk(
        ref="S1",
        text=SOURCE_TEXT,
        document_id="d",
        document_title="Contract Agreement.pdf",
        page_number=4,
        is_knowledge_base=False,
    )
    report = validate_findings(
        [fact("The contract says so.", Citation("S1"))],
        build_source_map([source]),
        expected_edition="red-book-2017",
    )
    assert report.is_grounded is True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_knowledge_base_citation_names_the_edition() -> None:
    """A reader must never have to guess which contract form was quoted."""
    rendered = kb_source("S1").render_citation()
    assert "FIDIC Red Book 2017" in rendered or "red-book-2017" in rendered
    assert "Clause 20.2.1" in rendered
    assert "p.121" in rendered


def test_project_document_citation_names_the_document() -> None:
    source = SourceChunk(
        ref="S1",
        text="x",
        document_id="d",
        document_title="Contract Agreement.pdf",
        page_number=4,
        clause_number="14.3",
    )
    rendered = source.render_citation()
    assert "Contract Agreement.pdf" in rendered
    assert "Clause 14.3" in rendered


def test_sources_block_exposes_refs_to_the_model() -> None:
    block = render_sources_block(assign_refs([kb_source("x"), kb_source("y")]))
    assert "S1" in block and "S2" in block
    assert SOURCE_TEXT[:40] in block


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_unused_sources_are_reported() -> None:
    sources = build_source_map(assign_refs([kb_source("a"), kb_source("b")]))
    report = validate_findings([fact("x", Citation("S1"))], sources)
    assert report.unused_sources == ["S2"]


def test_all_problems_collected_not_just_the_first() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [
            fact("bad ref", Citation("S9")),
            fact("uncited"),
            fact("bad quote", Citation("S1", quotation="no later than 99 days after")),
        ],
        sources,
    )
    assert report.unknown_refs and report.uncited_facts and report.unsupported_quotations


def test_most_severe_error_surfaces_first() -> None:
    sources = build_source_map([kb_source("S1")])
    report = validate_findings(
        [fact("bad ref", Citation("S9")), fact("uncited")], sources
    )
    with pytest.raises(UnknownCitationError):
        enforce_grounding(report)


def test_summary_states_grounded_status() -> None:
    sources = build_source_map([kb_source("S1")])
    assert "grounded" in validate_findings([fact("x", Citation("S1"))], sources).summary()
    assert "NOT grounded" in validate_findings([fact("x", Citation("S9"))], sources).summary()


# ---------------------------------------------------------------------------
# Quotations against OCR-damaged sources
#
# The scanned FIDIC 1987 reads "Notwithstandinga ny other provision" and "the
# Contractors hall send" — OCR moves spaces without losing characters. Matching
# on exact spacing rejected a model that quoted the provision as a person reads
# it, while accepting one that reproduced the damage; it made the better model
# look ungrounded on the N-55 corpus.
# ---------------------------------------------------------------------------

_OCR_SOURCE = (
    "53.1 Notwithstandinga ny other provision of the Contract, if the "
    "Contractori ntends to claim any additional payment pursuant to any Clause "
    "of these Conditions or otherwise, he shall give notice of his intention "
    "to the Engineer, with a copy to the Employer, within 28 days after the "
    "event giving rise to the claim has first arisen."
)


def test_a_quotation_that_repairs_ocr_spacing_is_verified() -> None:
    from claimiq.ai.domain.citations import verify_quotation

    assert verify_quotation(
        "Notwithstanding any other provision of the Contract, if the Contractor "
        "intends to claim any additional payment",
        _OCR_SOURCE,
    )


def test_a_quotation_reproducing_the_damage_is_still_verified() -> None:
    from claimiq.ai.domain.citations import verify_quotation

    assert verify_quotation("Notwithstandinga ny other provision of the Contract", _OCR_SOURCE)


def test_leniency_about_spacing_does_not_admit_changed_words() -> None:
    """The guarantee is unchanged: the characters must be in the source."""
    from claimiq.ai.domain.citations import verify_quotation

    assert not verify_quotation("he may give notice of his intention to the Engineer", _OCR_SOURCE)
    assert not verify_quotation(
        "within 42 days after the event giving rise to the claim", _OCR_SOURCE
    )
    assert not verify_quotation(
        "the Contractor shall be entitled to an extension of time", _OCR_SOURCE
    )
