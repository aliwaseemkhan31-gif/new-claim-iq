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
from typing import Optional, Sequence

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
class SegmentPart:
    """One page's contribution to a clause segment.

    ``offsets`` maps each character of the normalised ``text`` back to its
    position in the page's raw text, with one extra entry at the end so an
    exclusive slice end is always addressable. Normalisation collapses runs of
    spaces, so without this map a position in the cleaned text says nothing
    about where the words are on the page — and a highlight drawn from it
    lands in the wrong place.
    """

    page_number: int
    text: str
    offsets: tuple  # tuple[int, ...]; len == len(text) + 1


@dataclass(frozen=True)
class ClauseSegment:
    """A contiguous run of text belonging to one clause.

    A clause does not stop at a page break, so a segment may carry parts from
    several consecutive pages. It used to be cut at every break, which had two
    consequences: the tail of a clause became a chunk owned by that clause but
    starting mid-sentence, and — because a segment that fits in one chunk is
    marked complete — that fragment was flagged as the whole clause. A
    retriever preferring complete clauses then preferred the fragment.
    """

    clause_number: Optional[str]
    clause_title: Optional[str]
    parts: tuple  # tuple[SegmentPart, ...]
    starts_clause: bool = False
    """True when this run begins at the clause's own heading, rather than
    continuing a clause that began earlier."""
    heading_path: tuple = ()

    #: Separator used to join parts into one body. Two newlines, so a page
    #: break reads as a paragraph break rather than running two lines together.
    JOIN = "\n\n"

    @property
    def page_number(self) -> int:
        return self.parts[0].page_number if self.parts else 0

    @property
    def start_offset(self) -> int:
        return self.parts[0].offsets[0] if self.parts and self.parts[0].offsets else 0

    @property
    def text(self) -> str:
        return self.JOIN.join(part.text for part in self.parts)

    def locate(self, start: int, end: int) -> tuple:
        """Map a ``[start, end)`` slice of :attr:`text` onto the source pages.

        Returns one :class:`ChunkSpan` per page the slice touches, each holding
        that page's own character range. A chunk that straddles a page break
        therefore highlights correctly on both pages instead of claiming the
        whole segment on the first.
        """
        spans: list[ChunkSpan] = []
        cursor = 0
        for index, part in enumerate(self.parts):
            if index:
                cursor += len(self.JOIN)
            part_start, part_end = cursor, cursor + len(part.text)
            cursor = part_end

            overlap_start = max(start, part_start)
            overlap_end = min(end, part_end)
            if overlap_start >= overlap_end:
                continue

            local_start = overlap_start - part_start
            local_end = overlap_end - part_start
            spans.append(
                ChunkSpan(
                    page_number=part.page_number,
                    start_offset=part.offsets[local_start],
                    end_offset=part.offsets[local_end],
                )
            )
        if not spans and self.parts:
            # A degenerate slice still has to point somewhere real.
            first = self.parts[0]
            spans.append(
                ChunkSpan(
                    page_number=first.page_number,
                    start_offset=first.offsets[0],
                    end_offset=first.offsets[0],
                )
            )
        return tuple(spans)


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs but keep line structure.

    Line structure carries meaning in contracts (sub-paragraph lists), so it is
    preserved; only horizontal runs from PDF extraction are collapsed.
    """
    return _normalise_with_offsets(text)[0]


def _normalise_with_offsets(text: str):
    """:func:`_normalise_whitespace`, plus where every character came from.

    Returns ``(normalised, offsets)`` where ``offsets[i]`` is the index in
    ``text`` of the character that became ``normalised[i]``, and the final
    entry is one past the last character kept.
    """
    out_chars: list[str] = []
    out_offsets: list[int] = []

    base = 0
    pending_blank = False
    wrote_any = False

    for line in text.split("\n"):
        line_chars: list[str] = []
        line_offsets: list[int] = []

        index = 0
        length = len(line)
        while index < length and line[index] in " \t":
            index += 1
        while index < length:
            char = line[index]
            if char in " \t":
                run_end = index
                while run_end < length and line[run_end] in " \t":
                    run_end += 1
                if run_end < length:  # not trailing whitespace
                    line_chars.append(" ")
                    line_offsets.append(base + index)
                index = run_end
            else:
                line_chars.append(char)
                line_offsets.append(base + index)
                index += 1

        if line_chars:
            if wrote_any:
                # One newline for the line break, plus one more if a blank line
                # separated them — mirroring the blank-run collapse.
                for _ in range(2 if pending_blank else 1):
                    out_chars.append("\n")
                    out_offsets.append(line_offsets[0])
            out_chars.extend(line_chars)
            out_offsets.extend(line_offsets)
            wrote_any = True
            pending_blank = False
        elif wrote_any:
            pending_blank = True

        base += len(line) + 1  # the "\n" consumed by split

    end = (out_offsets[-1] + 1) if out_offsets else 0
    out_offsets.append(end)
    return "".join(out_chars), tuple(out_offsets)


def _merge_parts(parts: Sequence[SegmentPart]) -> tuple:
    return tuple(parts)


def segment_by_clause(
    pages: Sequence[PageText],
    headings: Sequence[ClauseHeading],
    heading_titles=None,
) -> list[ClauseSegment]:
    """Split page text into runs of text, each owned by one clause.

    A clause's text runs from its heading to the next heading, which may be on
    a later page; consecutive pages carrying the same clause are joined into
    one segment rather than cut at the break. Text before the first heading is
    emitted with ``clause_number=None`` rather than discarded — recitals and
    definitions preambles are substantive.
    """
    by_page: dict = {}
    for heading in headings:
        by_page.setdefault(heading.page_number, []).append(heading)
    for page_headings in by_page.values():
        page_headings.sort(key=lambda h: h.char_offset)

    titles = heading_titles or {}
    # (clause_number, clause_title, starts_clause, heading_path, part)
    raw: list = []
    current_number = None
    current_title = None

    def emit(number, title, starts, page_number, raw_text, base):
        body, offsets = _normalise_with_offsets(raw_text)
        if not body:
            return
        shifted = tuple(offset + base for offset in offsets)
        raw.append(
            (
                number,
                title,
                starts,
                titles.get(number or "", ()),
                SegmentPart(page_number=page_number, text=body, offsets=shifted),
            )
        )

    for page in pages:
        text = page.text
        page_headings = by_page.get(page.page_number, [])

        if not page_headings:
            emit(current_number, current_title, False, page.page_number, text, 0)
            continue

        # Text preceding the first heading on this page belongs to whatever
        # clause was open when the page began.
        first = page_headings[0]
        if first.char_offset > 0:
            emit(
                current_number,
                current_title,
                False,
                page.page_number,
                text[: first.char_offset],
                0,
            )

        for index, heading in enumerate(page_headings):
            start = heading.char_offset
            end = (
                page_headings[index + 1].char_offset
                if index + 1 < len(page_headings)
                else len(text)
            )
            current_number = heading.number
            current_title = heading.title
            emit(heading.number, heading.title, True, page.page_number, text[start:end], start)

    # Join consecutive runs of the same clause on consecutive pages. Only
    # consecutive pages: a gap means a page was dropped as front matter, and
    # bridging it would weld together text that is not continuous.
    segments: list[ClauseSegment] = []
    pending: list = []

    def flush_pending() -> None:
        if not pending:
            return
        number, title, starts, path, _ = pending[0]
        segments.append(
            ClauseSegment(
                clause_number=number,
                clause_title=title,
                parts=_merge_parts([item[4] for item in pending]),
                starts_clause=starts,
                heading_path=path,
            )
        )
        pending.clear()

    for item in raw:
        if pending:
            previous = pending[-1]
            same_clause = previous[0] == item[0]
            # A new run that starts at its own heading begins a new segment
            # even when it carries the same number — a clause repeated in a
            # schedule is not a continuation of the one in the conditions.
            continues = (
                same_clause
                and not item[2]
                and item[4].page_number in (previous[4].page_number, previous[4].page_number + 1)
            )
            if not continues:
                flush_pending()
        pending.append(item)
    flush_pending()

    return segments


def _split_spans(pattern, text: str, base: int = 0) -> list:
    """Split ``text`` on ``pattern``, keeping each piece's start index."""
    pieces: list = []
    position = 0
    for match in pattern.finditer(text):
        pieces.append((text[position : match.start()], base + position))
        position = match.end()
    pieces.append((text[position:], base + position))
    return pieces


def _stripped(piece: str, start: int):
    """Strip ``piece``, moving ``start`` to the first character kept."""
    lead = len(piece) - len(piece.lstrip())
    return piece.strip(), start + lead


def _split_oversized(text: str, max_chars: int, base: int = 0) -> list:
    """Split text with no sentence boundary, at whitespace, under ``max_chars``."""
    parts: list = []
    position = 0
    while len(text) - position > max_chars:
        window = text[position : position + max_chars]
        cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        piece, start = _stripped(text[position : position + cut], base + position)
        if piece:
            parts.append((piece, start))
        position += cut
    remainder, start = _stripped(text[position:], base + position)
    if remainder:
        parts.append((remainder, start))
    return parts


def _split_units(text: str, max_chars: int) -> list:
    """Break text into the smallest units chunking may recombine.

    Paragraphs first, then sentences, then a hard whitespace split. Splitting at
    a semantic boundary keeps a single obligation intact wherever possible.
    Each unit carries its start index in ``text``, so a chunk built from units
    knows where on the page it came from.
    """
    units: list = []
    for raw_paragraph, paragraph_start in _split_spans(_PARAGRAPH_SPLIT_RE, text):
        paragraph, paragraph_start = _stripped(raw_paragraph, paragraph_start)
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append((paragraph, paragraph_start))
            continue
        for raw_sentence, sentence_start in _split_spans(
            _SENTENCE_END_RE, paragraph, paragraph_start
        ):
            sentence, sentence_start = _stripped(raw_sentence, sentence_start)
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                units.append((sentence, sentence_start))
            else:
                units.extend(_split_oversized(sentence, max_chars, sentence_start))
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
) -> list:
    """Chunk one clause segment.

    A clause that fits in a single chunk is emitted whole and marked
    ``is_complete_clause`` — but only when the segment begins at the clause's
    own heading, so the tail of a clause carried over a page break is never
    passed off as the whole of it. Overlap is applied only between chunks of
    the same clause.
    """
    body = segment.text
    if not body.strip():
        return []

    whole_clause = segment.clause_number is not None and segment.starts_clause

    if len(body) <= config.max_chars:
        return [
            Chunk(
                text=body,
                spans=segment.locate(0, len(body)),
                clause_number=segment.clause_number,
                clause_title=segment.clause_title,
                sequence=start_sequence,
                heading_path=segment.heading_path,
                is_complete_clause=whole_clause,
            )
        ]

    units = _split_units(body, config.max_chars)
    chunks: list = []
    buffer = ""
    buffer_start = 0
    buffer_end = 0
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
                # The span covers this chunk's own material. The overlap tail
                # repeats the previous chunk's closing words, and a highlight
                # that reached back over them would mark the same sentence on
                # two chunks.
                spans=segment.locate(buffer_start, buffer_end),
                clause_number=segment.clause_number,
                clause_title=segment.clause_title,
                sequence=sequence,
                heading_path=segment.heading_path,
                is_complete_clause=False,
            )
        )
        sequence += 1

    for unit, unit_start in units:
        candidate = f"{buffer}\n\n{unit}".strip() if buffer else unit
        if len(candidate) > config.target_chars and buffer:
            flush()
            tail = _overlap_tail(buffer, config.overlap_chars)
            buffer = f"{tail}\n\n{unit}".strip() if tail else unit
            buffer_start = unit_start
            buffer_end = unit_start + len(unit)
        else:
            if not buffer:
                buffer_start = unit_start
            buffer = candidate
            buffer_end = unit_start + len(unit)

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
        parts = []
        for page in pages:
            if not page.text.strip():
                continue
            body, offsets = _normalise_with_offsets(page.text)
            if body:
                parts.append(
                    SegmentPart(page_number=page.page_number, text=body, offsets=offsets)
                )
        if not parts:
            return []
        segment = ClauseSegment(
            clause_number=None,
            clause_title=None,
            parts=tuple(parts),
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
