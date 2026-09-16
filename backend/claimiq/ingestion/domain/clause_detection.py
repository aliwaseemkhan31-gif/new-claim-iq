"""Clause structure detection for construction contracts.

The legacy prototype used one regex (`../backend/contract_parser.py:14`) that
matched any line-leading number and treated every hit as a clause. Three
consequences followed, all visible in its output:

1. **Headings and cross-references were conflated.** ``20.2.1 Notice of Claim``
   (a heading, defining where the clause lives) and ``as required by Sub-Clause
   20.2.1`` (a reference, pointing elsewhere) produced identical records. The
   clause index therefore recorded the first *mention* of a clause as its
   location, which is frequently the wrong page.

2. **Contents pages were indexed as content.** A table-of-contents line is
   line-leading-number-then-title, so it matched perfectly. This is the direct
   cause of the 422 contaminated chunks purged by hand from the legacy 2017
   knowledge base.

3. **No hierarchy.** ``20`` / ``20.2`` / ``20.2.1`` were flat peers, so the
   parent of a sub-clause was unknown and clause-scoped retrieval could not
   roll up.

This module separates the three cases, scores each candidate, and returns a
hierarchy. It is pure Python (stdlib only) and runs on Python 3.9+, so it is
exhaustively unit-testable without a database. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

#: A dotted clause number: 20, 20.2, 20.2.1, 4.12.3
_NUMBER = r"\d{1,3}(?:\.\d{1,3}){0,4}"

#: Words that explicitly introduce a cross-reference.
_REF_KEYWORD = r"(?:Sub-?Clauses?|Clauses?|Articles?|Sections?|Paragraphs?|Items?)"

#: A heading: line-leading number, then a title. The title must start with a
#: capital or a quote/paren, and the line must not read like a sentence.
_HEADING_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?P<kw>" + _REF_KEYWORD + r")[ \t]+)?"
    r"(?P<number>" + _NUMBER + r")"
    r"[.\)]?"
    r"[ \t–—:\-]+"
    r"(?P<title>[\"'\(“]?[A-Z][^\n]{2,120}?)"
    r"[ \t]*$",
)

#: A cross-reference introduced by a keyword: "Sub-Clause 20.2.1".
_KEYWORD_REF_RE = re.compile(
    r"\b(?P<label>" + _REF_KEYWORD + r")[ \t]+(?P<number>" + _NUMBER + r")\b",
    re.IGNORECASE,
)

#: A bare dotted number appearing inside running text: "under 20.2.1,".
#: Requires at least one dot, so plain integers and money amounts are excluded.
_BARE_REF_RE = re.compile(r"(?<![\w.])(?P<number>\d{1,3}(?:\.\d{1,3}){1,4})(?![\w.])")

#: Dot leaders, the signature of a contents page: "Definitions ......... 12".
_DOT_LEADER_RE = re.compile(r"[.·…]{4,}[ \t]*\d{1,4}[ \t]*$")

#: A line ending in a bare page number, the other contents-page signature.
_TRAILING_PAGENO_RE = re.compile(r"[A-Za-z’')\]][ \t]{2,}(\d{1,4})[ \t]*$")

#: A numbered contents entry whose page number follows a single space:
#: "16.1 Contractor's Employees 8". Structurally identical to a heading, so it
#: is recognised by its trailing page number and its brevity. The FIDIC 1987
#: printing sets its contents this way, with no dot leaders.
_CONTENTS_ENTRY_RE = re.compile(
    r"^[ \t]*\d{1,3}(?:\.\d{1,3}){0,3}[ \t]+"
    r"(?P<title>[^\n]{2,80}?)"
    r"[ \t]+\d{1,4}[ \t]*$"
)

#: A clause number printed inline after a marginal note, with the body text
#: running on from it: "Substantiation 53.3 Within 28 days, ...". The number is
#: not at the start of the line, so the line-leading pattern never sees it —
#: which left every passage of such a document carrying the previous clause's
#: number, and citations naming the wrong provision.
_MARGIN_HEADING_RE = re.compile(
    r"^[ \t]*(?P<title>[A-Z][A-Za-z\u2019'&/()\-, ]{2,60}?)[ \t]+"
    r"(?P<number>\d{1,3}(?:\.\d{1,3}){1,3})[ \t]+"
    r'(?P<rest>[A-Z\u201c"(][^\n]{15,})$'
)

#: Currency / measurement, to keep "1.5 million" and "2.5 m" out of references.
_UNIT_SUFFIX_RE = re.compile(
    r"^[ \t]*(?:%|million|billion|thousand|m\b|km\b|mm\b|cm\b|kg\b|t\b|"
    r"days?\b|weeks?\b|months?\b|years?\b|hours?\b)",
    re.IGNORECASE,
)

#: Tokens that mark a line as front/back matter rather than conditions text.
_NON_CONTENT_MARKERS = (
    "table of contents",
    "contents",
    "index",
    "general conditions",  # only when standing alone; see _is_structural_marker
    "notes on the preparation",
    "guidance for the preparation",
    "forms of securities",
    "sample forms",
    "annex",
    "appendix",
)


@dataclass(frozen=True)
class ClauseHeading:
    """A detected structural heading — where a clause actually begins.

    Attributes:
        number: Normalised dotted number, e.g. ``20.2.1``.
        title: Heading text with trailing punctuation stripped.
        page_number: 1-based page the heading appears on.
        line_index: 0-based line index within that page.
        char_offset: Character offset of the heading within the page text.
        confidence: 0.0–1.0. Below :data:`HEADING_MIN_CONFIDENCE` the candidate
            is not emitted.
        depth: Hierarchy depth; ``20`` is 1, ``20.2`` is 2, ``20.2.1`` is 3.
    """

    number: str
    title: str
    page_number: int
    line_index: int
    char_offset: int
    confidence: float
    depth: int

    @property
    def parent_number(self) -> str | None:
        """The enclosing clause number, or None for a top-level clause."""
        if "." not in self.number:
            return None
        return self.number.rsplit(".", 1)[0]


@dataclass(frozen=True)
class ClauseCitation:
    """A cross-reference to a clause found in running text.

    Distinct from :class:`ClauseHeading`: a citation points at a clause, it does
    not locate one. Conflating the two was defect (1) above.
    """

    number: str
    label: str
    page_number: int
    char_offset: int
    is_explicit: bool
    """True when introduced by a keyword ("Sub-Clause 20.2"); False for a bare
    dotted number, which is weaker evidence."""


@dataclass
class ClauseNode:
    """A clause in the assembled hierarchy."""

    number: str
    title: str
    page_number: int
    depth: int
    confidence: float
    children: list[ClauseNode] = field(default_factory=list)
    parent_number: str | None = None

    def walk(self) -> Iterable[ClauseNode]:
        """Depth-first traversal including self."""
        yield self
        for child in self.children:
            for node in child.walk():
                yield node


@dataclass(frozen=True)
class PageText:
    """Input unit: the extracted text of one page."""

    page_number: int
    text: str


@dataclass
class DetectionResult:
    headings: list[ClauseHeading]
    citations: list[ClauseCitation]
    roots: list[ClauseNode]
    skipped_pages: list[int]
    """Pages classified as front/back matter and excluded from heading detection."""

    def clause_numbers(self) -> list[str]:
        return [h.number for h in self.headings]

    def find(self, number: str) -> ClauseNode | None:
        for root in self.roots:
            for node in root.walk():
                if node.number == number:
                    return node
        return None


HEADING_MIN_CONFIDENCE = 0.55

# A page whose lines are this proportion contents-like is treated as front matter.
_TOC_PAGE_RATIO = 0.30
_TOC_PAGE_MIN_HITS = 4


def normalise_clause_number(raw: str) -> str:
    """Normalise a clause number to canonical dotted form.

    Strips trailing separators and removes leading zeros from each segment so
    ``08.02`` and ``8.2`` are the same clause.

    >>> normalise_clause_number("20.2.1.")
    '20.2.1'
    >>> normalise_clause_number("08.02")
    '8.2'
    """
    cleaned = raw.strip().rstrip(".)–—-").strip()
    segments = [s for s in cleaned.split(".") if s != ""]
    out: list[str] = []
    for segment in segments:
        if segment.isdigit():
            out.append(str(int(segment)))
        else:
            out.append(segment)
    return ".".join(out)


def clause_depth(number: str) -> int:
    """Hierarchy depth of a clause number. ``20`` -> 1, ``20.2.1`` -> 3."""
    return len([s for s in number.split(".") if s])


def parent_of(number: str) -> str | None:
    """Parent clause number, or None at top level."""
    return number.rsplit(".", 1)[0] if "." in number else None


def is_contents_line(line: str) -> bool:
    """True if the line looks like a table-of-contents entry.

    Two signatures are recognised: dot leaders followed by a page number, and a
    title followed by wide whitespace and a bare page number. Both are
    structurally indistinguishable from a heading without this check — which is
    precisely how the legacy 2017 knowledge base acquired 422 contaminated
    chunks.

    >>> is_contents_line("1.1 Definitions ................ 12")
    True
    >>> is_contents_line("20.2.1  Notice of Claim    145")
    True
    >>> is_contents_line("20.2.1 Notice of Claim")
    False
    """
    stripped = line.rstrip()
    if not stripped:
        return False
    if _DOT_LEADER_RE.search(stripped):
        return True
    if _TRAILING_PAGENO_RE.search(stripped):
        return True
    if len(stripped) <= 110:
        entry = _CONTENTS_ENTRY_RE.match(stripped)
        if entry and not entry.group("title").rstrip().endswith((".", ",", ";", ":")):
            return True
    return False


def _is_structural_marker(line: str) -> bool:
    """True if the line is a front/back-matter section marker."""
    low = line.strip().lower()
    if not low or len(low) > 60:
        return False
    for marker in _NON_CONTENT_MARKERS:
        if low == marker or low.startswith(marker + " ") or low.startswith(marker + ":"):
            return True
    return False


def classify_page(page: PageText) -> bool:
    """Return True if the page should be treated as body content.

    A page is excluded when a meaningful share of its non-empty lines are
    contents-style entries, or when it is topped by a front-matter marker.
    """
    lines = [ln for ln in page.text.splitlines() if ln.strip()]
    if not lines:
        return False

    for line in lines[:3]:
        if _is_structural_marker(line):
            return False

    toc_hits = sum(1 for ln in lines if is_contents_line(ln))
    if toc_hits >= _TOC_PAGE_MIN_HITS and toc_hits / len(lines) >= _TOC_PAGE_RATIO:
        return False

    return True


def _score_heading(number: str, title: str, keyword: str | None, line: str) -> float:
    """Confidence that a candidate line is a genuine clause heading.

    Additive scoring over independent signals, clamped to [0, 1]. Kept explicit
    and inspectable rather than learned: this runs offline, must be debuggable
    from a single line of text, and its failures must be explainable to a user
    who disagrees with the extracted structure.
    """
    score = 0.5

    if keyword:
        # "Clause 20 — General Provisions" is unambiguous.
        score += 0.25

    # Headings are short. Body paragraphs that happen to start with a number
    # are long.
    title_len = len(title)
    if title_len <= 60:
        score += 0.15
    elif title_len <= 90:
        score += 0.05
    else:
        score -= 0.20

    # Headings rarely end in a full stop; sentences do.
    if title.endswith("."):
        score -= 0.25

    # Title-case or all-caps is strong evidence of a heading.
    words = [w for w in re.split(r"[ \t]+", title) if w]
    if words:
        capitalised = sum(1 for w in words if w[:1].isupper())
        ratio = capitalised / len(words)
        if ratio >= 0.7:
            score += 0.20
        elif ratio >= 0.5:
            score += 0.10
        else:
            score -= 0.10

    # Sentence connectives indicate running prose, not a heading.
    lowered = " " + title.lower() + " "
    for token in (" shall ", " the contractor shall ", " means ", " if the ", " unless "):
        if token in lowered:
            score -= 0.20
            break

    # Deeply nested numbers are more often references than headings.
    if clause_depth(number) >= 4:
        score -= 0.10

    # A contents line reaching here is disqualified outright.
    if is_contents_line(line):
        return 0.0

    return max(0.0, min(1.0, score))


def _score_margin_heading(number: str, title: str, rest: str, line: str) -> float:
    """Confidence that a marginal note plus an inline number is a heading.

    Scored separately from a line-leading heading because the evidence differs:
    the marginal note is the title, and the provision's text follows on the same
    line. A reference keyword in the title position ("Sub-Clause 20.2 The
    Contractor shall ...") is prose, not a heading, and is rejected outright.
    """
    if re.fullmatch(_REF_KEYWORD, title.strip(), re.IGNORECASE):
        return 0.0
    if is_contents_line(line):
        return 0.0
    if len(rest.split()) < 4:
        return 0.0

    score = 0.5

    words = [w for w in re.split(r"[ \t]+", title) if w]
    capitalised = sum(1 for w in words if w[:1].isupper())
    if words and capitalised / len(words) >= 0.6:
        score += 0.2
    else:
        score -= 0.2

    if len(title) <= 40:
        score += 0.1

    # A marginal note is a label, not a sentence.
    lowered = " " + title.lower() + " "
    for token in (" shall ", " means ", " unless ", " if the ", " which "):
        if token in lowered:
            score -= 0.4
            break

    return max(0.0, min(1.0, score))


def detect_headings(pages: Sequence[PageText]) -> tuple[list[ClauseHeading], list[int]]:
    """Detect clause headings across ``pages``.

    Returns the accepted headings and the page numbers skipped as front/back
    matter.
    """
    headings: list[ClauseHeading] = []
    skipped: list[int] = []

    for page in pages:
        if not classify_page(page):
            skipped.append(page.page_number)
            continue

        offset = 0
        for line_index, line in enumerate(page.text.splitlines()):
            match = _HEADING_RE.match(line)
            if match:
                number = normalise_clause_number(match.group("number"))
                title = match.group("title").strip().rstrip(".;:,")
                keyword = match.group("kw")
                if number and title:
                    confidence = _score_heading(number, title, keyword, line)
                    if confidence >= HEADING_MIN_CONFIDENCE:
                        headings.append(
                            ClauseHeading(
                                number=number,
                                title=title,
                                page_number=page.page_number,
                                line_index=line_index,
                                char_offset=offset,
                                confidence=round(confidence, 3),
                                depth=clause_depth(number),
                            )
                        )
                        offset += len(line) + 1
                        continue

            # The number may sit inline after a marginal note instead.
            margin = _MARGIN_HEADING_RE.match(line)
            if margin:
                number = normalise_clause_number(margin.group("number"))
                title = margin.group("title").strip().rstrip(".;:,")
                if number and title:
                    confidence = _score_margin_heading(
                        number, title, margin.group("rest"), line
                    )
                    if confidence >= HEADING_MIN_CONFIDENCE:
                        headings.append(
                            ClauseHeading(
                                number=number,
                                title=title,
                                page_number=page.page_number,
                                line_index=line_index,
                                # The provision starts at its number, not at the
                                # marginal note, so chunking splits there.
                                char_offset=offset + margin.start("number"),
                                confidence=round(confidence, 3),
                                depth=clause_depth(number),
                            )
                        )

            offset += len(line) + 1

    return headings, skipped


def detect_citations(pages: Sequence[PageText]) -> list[ClauseCitation]:
    """Detect cross-references to clauses in running text.

    Explicit keyword references are always emitted. Bare dotted numbers are
    emitted only when they are not immediately followed by a unit or currency
    word, which keeps "1.5 million" and "2.5 m" out of the results.
    """
    citations: list[ClauseCitation] = []

    for page in pages:
        text = page.text

        explicit_spans: list[tuple[int, int]] = []
        for match in _KEYWORD_REF_RE.finditer(text):
            number = normalise_clause_number(match.group("number"))
            if not number:
                continue
            explicit_spans.append(match.span())
            citations.append(
                ClauseCitation(
                    number=number,
                    label=match.group(0).strip(),
                    page_number=page.page_number,
                    char_offset=match.start(),
                    is_explicit=True,
                )
            )

        for match in _BARE_REF_RE.finditer(text):
            start, end = match.span()
            # Skip numbers already captured as part of an explicit reference.
            if any(s <= start < e for s, e in explicit_spans):
                continue
            if _UNIT_SUFFIX_RE.match(text[end : end + 12]):
                continue
            number = normalise_clause_number(match.group("number"))
            if not number:
                continue
            citations.append(
                ClauseCitation(
                    number=number,
                    label=match.group(0),
                    page_number=page.page_number,
                    char_offset=start,
                    is_explicit=False,
                )
            )

    return citations


def build_hierarchy(headings: Sequence[ClauseHeading]) -> list[ClauseNode]:
    """Assemble detected headings into a clause tree.

    When the same number is detected more than once, the highest-confidence
    occurrence wins; ties break toward the earliest page, since a clause is
    defined where it first appears and later occurrences are usually
    continuations or running headers.

    Orphans — a ``20.2.1`` with no detected ``20.2`` — are attached to the
    nearest present ancestor, or promoted to a root if none exists. Real
    documents have imperfect extraction and dropping the sub-clause would lose
    more than re-parenting it does.
    """
    best: dict[str, ClauseHeading] = {}
    for heading in headings:
        current = best.get(heading.number)
        if current is None:
            best[heading.number] = heading
            continue
        if (heading.confidence, -heading.page_number) > (
            current.confidence,
            -current.page_number,
        ):
            best[heading.number] = heading

    def sort_key(number: str) -> tuple[int, ...]:
        return tuple(int(p) if p.isdigit() else 0 for p in number.split("."))

    nodes: dict[str, ClauseNode] = {}
    for number in sorted(best, key=sort_key):
        heading = best[number]
        nodes[number] = ClauseNode(
            number=number,
            title=heading.title,
            page_number=heading.page_number,
            depth=heading.depth,
            confidence=heading.confidence,
        )

    roots: list[ClauseNode] = []
    for number in sorted(nodes, key=sort_key):
        node = nodes[number]
        ancestor = parent_of(number)
        while ancestor is not None and ancestor not in nodes:
            ancestor = parent_of(ancestor)
        if ancestor is None:
            roots.append(node)
        else:
            node.parent_number = ancestor
            nodes[ancestor].children.append(node)

    return roots


def detect(pages: Sequence[PageText]) -> DetectionResult:
    """Run the full detection pass over ``pages``."""
    headings, skipped = detect_headings(pages)
    citations = detect_citations(pages)
    roots = build_hierarchy(headings)
    return DetectionResult(
        headings=headings,
        citations=citations,
        roots=roots,
        skipped_pages=skipped,
    )
