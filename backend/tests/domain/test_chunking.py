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
from claimiq.ingestion.domain.clause_detection import PageText, detect_headings


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
    pages = _pages(
        "20.2.1 Notice of Claim\nThe claiming Party shall give a Notice to the Engineer.",
        "The Notice shall describe the event or circumstance giving rise to the Claim.",
    )
    headings, _ = detect_headings(pages)
    chunks = chunk_document(pages, headings)
    page_two = [c for c in chunks if c.primary_page == 2]
    assert page_two
    assert page_two[0].clause_number == "20.2.1", "continuation keeps its clause"


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
