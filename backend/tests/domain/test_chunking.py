"""Tests for structure-aware chunking.

The property under test: a chunk belongs to exactly one clause and knows where
it came from. The legacy prototype's fixed-window splitter satisfied neither.
"""
from __future__ import annotations

import pytest

from claimiq.ingestion.domain.chunking import (
    Chunk,
    ChunkingConfig,
    chunk_document,
    segment_by_clause,
)
from claimiq.ingestion.domain.clause_detection import (
    PageText,
    build_hierarchy,
    detect_headings,
    heading_paths,
)


def _pages(*texts: str) -> list[PageText]:
    return [PageText(page_number=i, text=t) for i, t in enumerate(texts, start=1)]


def _chunk_for(chunks: list[Chunk], clause: str) -> list[Chunk]:
    return [c for c in chunks if c.clause_number == clause]


def test_chunk_carries_clause_provenance() -> None:
    pages = _pages("20.2.1 Notice of Claim\n" + "The claiming Party shall give a Notice. " * 3)
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)

    assert chunks
    assert chunks[0].clause_number == "20.2.1"
    assert chunks[0].clause_title == "Notice of Claim"
    assert chunks[0].primary_page == 1


def test_chunks_do_not_straddle_clause_boundaries() -> None:
    """The central improvement: two obligations never share a chunk."""
    pages = _pages(
        "20.2.1 Notice of Claim\n"
        "The claiming Party shall give a Notice within 28 days.\n"
        "20.2.2 Engineer's Initial Response\n"
        "The Engineer shall respond within 14 days."
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)

    for chunk in chunks:
        if chunk.clause_number == "20.2.1":
            assert "Engineer's Initial Response" not in chunk.text
        if chunk.clause_number == "20.2.2":
            assert "Notice of Claim" not in chunk.text

    assert {c.clause_number for c in chunks} == {"20.2.1", "20.2.2"}


def test_short_clause_is_marked_complete() -> None:
    pages = _pages("20.2.1 Notice of Claim\nThe claiming Party shall give a Notice.")
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    assert chunks[0].is_complete_clause is True


def test_long_clause_is_split_into_multiple_chunks() -> None:
    body = " ".join(
        f"Sentence number {i} describing an obligation of the Contractor in detail."
        for i in range(80)
    )
    pages = _pages(f"20.2.1 Notice of Claim\n{body}")
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings, ChunkingConfig(target_chars=600, max_chars=900))

    assert len(chunks) > 1
    assert all(c.clause_number == "20.2.1" for c in chunks)
    assert all(not c.is_complete_clause for c in chunks)


def test_chunks_respect_max_chars() -> None:
    body = " ".join(f"Obligation {i} of the Contractor." for i in range(200))
    pages = _pages(f"14.3 Application for Interim Payment\n{body}")
    headings, _ = detect_headings(pages)
    config = ChunkingConfig(target_chars=500, max_chars=800)
    chunks = chunk_document(pages, headings, config)
    for chunk in chunks:
        assert len(chunk.text) <= config.max_chars * 1.2


def test_sequences_are_contiguous() -> None:
    pages = _pages(
        "20.1 Claims\nText for the first clause here.",
        "20.2 Claims For Payment\nText for the second clause here.",
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    assert [c.sequence for c in chunks] == list(range(len(chunks)))


def test_preamble_before_first_heading_is_retained() -> None:
    """Recitals are substantive and must not be dropped."""
    pages = _pages(
        "WHEREAS the Employer desires the execution of the Works and has accepted "
        "a Tender by the Contractor for their execution and completion.\n"
        "1.1 Definitions\nIn the Conditions, the following words shall have the meanings assigned."
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    assert any(c.clause_number is None and "WHEREAS" in c.text for c in chunks)


def test_clause_text_continues_across_pages() -> None:
    """A clause is not cut at a page break.

    It used to be: the page-two tail became a chunk of its own, owned by the
    clause but starting mid-provision. The clause now stays one run of text,
    carrying a span on each page it occupies.
    """
    pages = _pages(
        "20.2.1 Notice of Claim\nThe claiming Party shall give a Notice to the Engineer.",
        "The Notice shall describe the event or circumstance giving rise to the Claim.",
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    carrying = [c for c in chunks if 2 in c.page_numbers]
    assert carrying, "the continuation must still be reachable"
    chunk = carrying[0]
    assert chunk.clause_number == "20.2.1", "continuation keeps its clause"
    assert chunk.page_numbers == (1, 2)
    assert "shall describe the event" in chunk.text
    assert [span.page_number for span in chunk.spans] == [1, 2]


def test_embedding_text_prefixes_clause_context() -> None:
    """A bare fragment is near-meaningless to a retriever; the prefix locates it."""
    pages = _pages("20.2.1 Notice of Claim\nThe claiming Party shall give a Notice.")
    headings, _ = detect_headings(pages)
    chunk = chunk_document(pages, headings)[0]
    embedded = chunk.embedding_text()
    assert embedded.startswith("Clause 20.2.1 Notice of Claim")
    assert chunk.text in embedded


def test_embedding_text_without_clause_is_unchanged() -> None:
    pages = _pages("Some preamble text that belongs to no clause at all in this document.")
    chunk = chunk_document(pages, [])[0]
    assert chunk.embedding_text() == chunk.text


def test_spans_locate_the_chunk_for_highlighting() -> None:
    pages = _pages("20.2.1 Notice of Claim\nThe claiming Party shall give a Notice.")
    headings, _ = detect_headings(pages)
    chunk = chunk_document(pages, headings)[0]
    assert chunk.spans
    span = chunk.spans[0]
    assert span.page_number == 1
    assert span.end_offset > span.start_offset


def test_page_numbers_are_deduplicated() -> None:
    pages = _pages("20.1 Claims\nSome text about claims under the Contract.")
    headings, _ = detect_headings(pages)
    chunk = chunk_document(pages, headings)[0]
    assert chunk.page_numbers == (1,)


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def test_segment_by_clause_assigns_ownership() -> None:
    pages = _pages(
        "20.1 Claims\nFirst clause body text goes here.\n"
        "20.2 Claims For Payment\nSecond clause body text goes here."
    )
    headings, _ = detect_headings(pages)
    segments = segment_by_clause(pages, headings)
    owned = [s.clause_number for s in segments if s.clause_number]
    assert owned == ["20.1", "20.2"]


def test_empty_pages_produce_no_chunks() -> None:
    assert chunk_document(_pages("", "   \n  "), []) == []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_invalid_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(target_chars=0).validate()
    with pytest.raises(ValueError):
        ChunkingConfig(target_chars=1000, max_chars=500).validate()
    with pytest.raises(ValueError):
        ChunkingConfig(target_chars=500, overlap_chars=500).validate()


def test_disabling_clause_boundaries_falls_back_to_flat_chunking() -> None:
    """The legacy behaviour remains reachable for non-structured documents."""
    pages = _pages("20.1 Claims\nSome text.", "20.2 Payment\nMore text.")
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings, ChunkingConfig(respect_clause_boundaries=False))
    assert all(c.clause_number is None for c in chunks)


# ---------------------------------------------------------------------------
# Where a chunk actually is on the page
#
# Every chunk of a split segment used to record the offsets of the whole
# segment, so a citation to one sentence highlighted the entire clause. The
# span must now be the chunk's own slice, and must survive the whitespace
# normalisation that happens between the page and the chunk.
# ---------------------------------------------------------------------------

LONG_CLAUSE = (
    "20.2.1 Notice of Claim\n"
    + "The claiming Party shall give a Notice to the Engineer describing the "
    "event or circumstance giving rise to the Claim. " * 40
)


def _normalised(text: str) -> str:
    return " ".join(text.split())


def test_each_chunk_records_its_own_slice_not_the_whole_segment() -> None:
    pages = _pages(LONG_CLAUSE)
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    assert len(chunks) > 1, "this fixture must split, or it tests nothing"

    whole = (chunks[0].spans[0].start_offset, chunks[0].spans[0].end_offset)
    assert any(
        (c.spans[0].start_offset, c.spans[0].end_offset) != whole for c in chunks[1:]
    ), "every chunk reported the same span, which is the defect"

    for chunk in chunks:
        span = chunk.spans[0]
        assert span.end_offset > span.start_offset
        width = span.end_offset - span.start_offset
        # A span may be a little wider than its text (collapsed whitespace) but
        # must not cover the whole clause.
        assert width <= len(chunk.text) * 2


def test_a_span_points_at_the_words_the_chunk_holds() -> None:
    """The span is only useful if the page text under it is the chunk."""
    pages = _pages(LONG_CLAUSE)
    headings, _ = detect_headings(pages)
    page_text = pages[0].text

    for chunk in chunk_document(pages, headings):
        for span in chunk.spans:
            source = _normalised(page_text[span.start_offset : span.end_offset])
            opening = _normalised(chunk.text)[:40]
            assert opening and opening in _normalised(chunk.text)
            assert source, "a span must not point at nothing"
            # The first words of the chunk appear inside what the span covers.
            assert _normalised(chunk.text)[:30] in source


def test_spans_survive_collapsed_whitespace() -> None:
    """PDF extraction leaves runs of spaces; the offsets must be of the page,
    not of the cleaned-up text."""
    raw = "20.1 Claims\nThe    Contractor     shall    give      notice promptly."
    pages = _pages(raw)
    headings, _ = detect_headings(pages)
    chunk = chunk_document(pages, headings)[0]
    span = chunk.spans[0]
    covered = raw[span.start_offset : span.end_offset]
    assert "Contractor" in covered
    assert "promptly" in covered


# ---------------------------------------------------------------------------
# Complete clauses
# ---------------------------------------------------------------------------


def test_a_page_fragment_is_not_a_complete_clause() -> None:
    """The flag decides which chunk a retriever prefers.

    A clause carried over a page break used to produce a tail chunk that fitted
    in one chunk and was therefore marked complete — so the assembler could
    prefer half a provision over the whole of it.
    """
    pages = _pages(
        "20.2.1 Notice of Claim\n" + "The claiming Party shall give a Notice. " * 80,
        "The Notice shall state the contractual basis of the Claim.",
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)

    assert len(chunks) > 1
    assert not any(
        c.is_complete_clause for c in chunks
    ), "a clause too long for one chunk has no complete-clause chunk"


def test_a_clause_spanning_a_break_is_complete_when_it_fits() -> None:
    pages = _pages(
        "20.2.1 Notice of Claim\nThe claiming Party shall give a Notice.",
        "The Notice shall state the contractual basis of the Claim.",
    )
    headings, _ = detect_headings(pages)
    chunk = chunk_document(pages, headings)[0]
    assert chunk.is_complete_clause is True
    assert chunk.page_numbers == (1, 2)


def test_text_carried_onto_a_page_without_its_heading_is_not_complete() -> None:
    """Page two opens mid-clause and page three starts a new one."""
    pages = _pages(
        "20.1 Claims\nThe first provision of the contract appears here in full.",
        "A continuation sentence that belongs to the clause begun on page one.",
        "20.2 Claims For Payment\nThe second provision appears here in full.",
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    first = _chunk_for(chunks, "20.1")[0]
    assert first.page_numbers == (1, 2), "the clause is one run across the break"
    assert first.is_complete_clause is True


# ---------------------------------------------------------------------------
# Ancestry
# ---------------------------------------------------------------------------


def test_heading_paths_run_from_the_top_down() -> None:
    pages = _pages(
        "20 Employer's and Contractor's Claims\n"
        "20.2 Claims For Payment\n"
        "20.2.1 Notice of Claim\nThe claiming Party shall give a Notice."
    )
    headings, _ = detect_headings(pages)
    paths = heading_paths(build_hierarchy(headings))
    assert paths["20.2.1"] == (
        "Employer's and Contractor's Claims",
        "Claims For Payment",
        "Notice of Claim",
    )
    assert paths["20"] == ("Employer's and Contractor's Claims",)


def test_a_chunk_carries_its_ancestry_when_the_hierarchy_is_given() -> None:
    """Regression: the ingestion stage called the chunker without the
    hierarchy, so this was empty on every chunk in the database."""
    pages = _pages(
        "20 Employer's and Contractor's Claims\n"
        "20.2 Claims For Payment\n"
        "20.2.1 Notice of Claim\nThe claiming Party shall give a Notice."
    )
    headings, _ = detect_headings(pages)
    paths = heading_paths(build_hierarchy(headings))
    chunks = chunk_document(pages, headings, heading_titles=paths)
    notice = _chunk_for(chunks, "20.2.1")[0]
    assert notice.heading_path == (
        "Employer's and Contractor's Claims",
        "Claims For Payment",
        "Notice of Claim",
    )
