"""Structure-aware chunking.

The legacy prototype split page text every 800 characters with a 150-character
overlap and attached ``{"page": n, "source": "contract"}``
(`../backend/rag.py:92`). Two consequences:

1. **Chunks straddled clause boundaries.** A window could end mid-sentence in
   Sub-Clause 20.2.1 and continue into 20.2.2, so a retrieved chunk could
   contain two different obligations with no marker separating them.

2. **Citations could not resolve past the page.** With only a page number, a
   citation could say "page 127" but not "Clause 20.2.1", and could not
   highlight the passage in a viewer.

Here, chunking follows the detected document structure. A chunk belongs to
exactly one clause, carries that clause's number and title, and records its
character span within the page so the viewer can highlight it.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from claimiq.ingestion.domain.clause_detection import ClauseHeading, PageText

#: Target chunk size in characters. Sized against the embedding model's window
#: (BGE-M3 handles long inputs, but retrieval precision degrades when a chunk
#: spans several distinct obligations).
DEFAULT_TARGET_CHARS = 1200

#: Hard ceiling. A single sentence longer than this is split at whitespace.
DEFAULT_MAX_CHARS = 2000

#: Overlap between adjacent chunks *within the same clause*. Never applied
#: across a clause boundary — bleeding one clause's text into another is the
#: defect this module exists to prevent.
DEFAULT_OVERLAP_CHARS = 150

#: Chunks shorter than this are dropped unless they are a complete clause.
DEFAULT_MIN_CHARS = 60

_SENTENCE_END_RE = re.compile(r"(?<=[.;:!?])[ \t]+(?=[A-Z\"'\(])")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")
_WS_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class ChunkSpan:
    """Location of a chunk within its source document.

    Enough to resolve a citation to a highlightable region: which page, and
    which character range on that page.
    """

    page_number: int
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit of text with full provenance.

    Attributes:
        text: The chunk content as it will be embedded and shown.
        spans: Source locations. Usually one; more when a chunk continues
            across a page break within the same clause.
        clause_number: Owning clause, if the chunk falls under a detected
            heading. ``None`` for text before the first heading (recitals,
            preamble).
        clause_title: Title of the owning clause.
        sequence: 0-based position within the document.
        heading_path: Ancestor titles from the top down, e.g.
            ``("Employer's and Contractor's Claims", "Notice of Claim")``.
            Prepended to the embedded text so a chunk carries its context.
        is_complete_clause: True when the chunk holds an entire clause rather
            than a fragment. Complete clauses are preferred by the assembler,
            because a partial obligation is a misleading citation.
    """

    text: str
    spans: tuple[ChunkSpan, ...]
    clause_number: str | None
    clause_title: str | None
    sequence: int
    heading_path: tuple[str, ...] = ()
    is_complete_clause: bool = False

    @property
    def page_numbers(self) -> tuple[int, ...]:
        seen: list[int] = []
        for span in self.spans:
            if span.page_number not in seen:
                seen.append(span.page_number)
        return tuple(seen)

    @property
    def primary_page(self) -> int:
        return self.spans[0].page_number if self.spans else 0

    def embedding_text(self) -> str:
        """Text handed to the embedding model.

        The clause number, title and ancestor path are prepended. A bare
        fragment reading "shall give a Notice within 28 days" is nearly
        meaningless to a retriever; prefixed with
        "Clause 20.2.1 Notice of Claim" it is precisely locatable. This costs a
        few tokens and materially improves clause-scoped recall.
        """
        header_parts: list[str] = []
        if self.clause_number:
            label = f"Clause {self.clause_number}"
            if self.clause_title:
                label = f"{label} {self.clause_title}"
            header_parts.append(label)
        elif self.heading_path:
            header_parts.append(" > ".join(self.heading_path))
        if not header_parts:
            return self.text
        return f"{header_parts[0]}\n\n{self.text}"


@dataclass
class ChunkingConfig:
    """Tunable chunking parameters.

    Exposed through the admin interface (`AI_ARCHITECTURE.md`) rather than
    hardcoded, because the right values depend on the embedding model and on
    how verbose the contracts in a given deployment are.
    """

    target_chars: int = DEFAULT_TARGET_CHARS
    max_chars: int = DEFAULT_MAX_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS
    min_chars: int = DEFAULT_MIN_CHARS
    respect_clause_boundaries: bool = True

    def validate(self) -> None:
        if self.target_chars <= 0:
            raise ValueError("target_chars must be positive")
        if self.max_chars < self.target_chars:
            raise ValueError("max_chars must be >= target_chars")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars must be non-negative")
        if self.overlap_chars >= self.target_chars:
            raise ValueError("overlap_chars must be smaller than target_chars")
        if self.min_chars < 0:
            raise ValueError("min_chars must be non-negative")


@dataclass(frozen=True)
class ClauseSegment:
    """A contiguous run of text belonging to one clause."""

    clause_number: str | None
    clause_title: str | None
    page_number: int
    start_offset: int
    text: str
    heading_path: tuple[str, ...] = ()


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs but keep line structure.

    Line structure carries meaning in contracts (sub-paragraph lists), so it is
    preserved; only horizontal runs from PDF extraction are collapsed.
    """
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def segment_by_clause(
    pages: Sequence[PageText],
    headings: Sequence[ClauseHeading],
    heading_titles: dict[str, tuple[str, ...]] | None = None,
) -> list[ClauseSegment]:
    """Split page text into runs of text, each owned by one clause.

    A clause's text runs from its heading to the next heading, which may be on
    a later page. Text before the first heading is emitted with
    ``clause_number=None`` rather than discarded — recitals and definitions
    preambles are substantive.
    """
    by_page: dict[int, list[ClauseHeading]] = {}
    for heading in headings:
        by_page.setdefault(heading.page_number, []).append(heading)
    for page_headings in by_page.values():
        page_headings.sort(key=lambda h: h.char_offset)

    segments: list[ClauseSegment] = []
    current_number: str | None = None
    current_title: str | None = None

    for page in pages:
        text = page.text
        page_headings = by_page.get(page.page_number, [])

        if not page_headings:
            body = _normalise_whitespace(text)
            if body:
                segments.append(
                    ClauseSegment(
                        clause_number=current_number,
                        clause_title=current_title,
                        page_number=page.page_number,
                        start_offset=0,
                        text=body,
                        heading_path=(heading_titles or {}).get(current_number or "", ()),
                    )
                )
            continue

        # Text preceding the first heading on this page belongs to whatever
        # clause was open when the page began.
        first = page_headings[0]
        if first.char_offset > 0:
            lead = _normalise_whitespace(text[: first.char_offset])
            if lead:
                segments.append(
                    ClauseSegment(
                        clause_number=current_number,
                        clause_title=current_title,
                        page_number=page.page_number,
                        start_offset=0,
                        text=lead,
                        heading_path=(heading_titles or {}).get(current_number or "", ()),
                    )
                )

        for index, heading in enumerate(page_headings):
            start = heading.char_offset
            end = (
                page_headings[index + 1].char_offset
                if index + 1 < len(page_headings)
                else len(text)
            )
            body = _normalise_whitespace(text[start:end])
            current_number = heading.number
            current_title = heading.title
            if body:
                segments.append(
                    ClauseSegment(
                        clause_number=heading.number,
                        clause_title=heading.title,
                        page_number=page.page_number,
                        start_offset=start,
                        text=body,
                        heading_path=(heading_titles or {}).get(heading.number, ()),
                    )
                )

    return segments


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Split text with no sentence boundary, at whitespace, under ``max_chars``."""
    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _split_units(text: str, max_chars: int) -> list[str]:
    """Break text into the smallest units chunking may recombine.

    Paragraphs first, then sentences, then a hard whitespace split. Splitting at
    a semantic boundary keeps a single obligation intact wherever possible.
    """
    units: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        for sentence in _SENTENCE_END_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                units.append(sentence)
            else:
                units.extend(_split_oversized(sentence, max_chars))
    return units


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """Trailing slice of ``text`` for overlap, cut at a sentence boundary."""
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return ""
    tail = text[-overlap_chars:]
    match = _SENTENCE_END_RE.search(tail)
    if match:
        return tail[match.end() :].strip()
    space = tail.find(" ")
    return tail[space + 1 :].strip() if space != -1 else tail.strip()


def chunk_segment(
    segment: ClauseSegment,
    config: ChunkingConfig,
    start_sequence: int,
) -> list[Chunk]:
    """Chunk one clause segment.

    A clause that fits in a single chunk is emitted whole and marked
    ``is_complete_clause``. Overlap is applied only between chunks of the same
    clause.
    """
    body = segment.text.strip()
    if not body:
        return []

    if len(body) <= config.max_chars:
        return [
            Chunk(
                text=body,
                spans=(
                    ChunkSpan(
                        page_number=segment.page_number,
                        start_offset=segment.start_offset,
                        end_offset=segment.start_offset + len(segment.text),
                    ),
                ),
                clause_number=segment.clause_number,
                clause_title=segment.clause_title,
                sequence=start_sequence,
                heading_path=segment.heading_path,
                is_complete_clause=segment.clause_number is not None,
            )
        ]

    units = _split_units(body, config.max_chars)
    chunks: list[Chunk] = []
    buffer = ""
    sequence = start_sequence

    def flush() -> None:
        nonlocal buffer, sequence
        content = buffer.strip()
        if not content:
            return
        if len(content) < config.min_chars and chunks:
            return
        chunks.append(
            Chunk(
                text=content,
                spans=(
                    ChunkSpan(
                        page_number=segment.page_number,
                        start_offset=segment.start_offset,
                        end_offset=segment.start_offset + len(segment.text),
                    ),
                ),
                clause_number=segment.clause_number,
                clause_title=segment.clause_title,
                sequence=sequence,
                heading_path=segment.heading_path,
                is_complete_clause=False,
            )
        )
        sequence += 1

    for unit in units:
        candidate = f"{buffer}\n\n{unit}".strip() if buffer else unit
        if len(candidate) > config.target_chars and buffer:
            flush()
            tail = _overlap_tail(buffer, config.overlap_chars)
            buffer = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            buffer = candidate

    flush()
    return chunks


def chunk_document(
    pages: Sequence[PageText],
    headings: Sequence[ClauseHeading],
    config: ChunkingConfig | None = None,
    heading_titles: dict[str, tuple[str, ...]] | None = None,
) -> list[Chunk]:
    """Chunk a whole document, following its clause structure.

    Args:
        pages: Extracted page text in document order.
        headings: Detected clause headings from
            :mod:`claimiq.ingestion.domain.clause_detection`.
        config: Chunking parameters. Defaults are used when omitted.
        heading_titles: Optional clause number → ancestor-title path, used to
            give each chunk its hierarchical context.

    Returns:
        Chunks in document order with contiguous ``sequence`` values.
    """
    cfg = config or ChunkingConfig()
    cfg.validate()

    if not cfg.respect_clause_boundaries:
        merged = "\n\n".join(_normalise_whitespace(p.text) for p in pages if p.text.strip())
        segment = ClauseSegment(
            clause_number=None,
            clause_title=None,
            page_number=pages[0].page_number if pages else 1,
            start_offset=0,
            text=merged,
        )
        return chunk_segment(segment, cfg, 0)

    segments = segment_by_clause(pages, headings, heading_titles)
    chunks: list[Chunk] = []
    for segment in segments:
        chunks.extend(chunk_segment(segment, cfg, len(chunks)))

    # Re-sequence so numbering is contiguous after any drops.
    return [
        Chunk(
            text=c.text,
            spans=c.spans,
            clause_number=c.clause_number,
            clause_title=c.clause_title,
            sequence=index,
            heading_path=c.heading_path,
            is_complete_clause=c.is_complete_clause,
        )
        for index, c in enumerate(chunks)
    ]
