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
#: Requires at least one dot, so plain integers are excluded.
#:
#: The second lookbehind rejects the decimal tail of a thousands-separated
#: amount. ``1,234.56`` otherwise entered at the ``234.56``, because the comma
#: before it is neither a word character nor a dot, and every sum on a payment
#: page was emitted as a cross-reference to a clause that does not exist. A
#: comma directly before a genuine reference ("Clauses 20.1,20.2", with no
#: space) is the only thing given up, and it is far rarer than money is.
_BARE_REF_RE = re.compile(
    r"(?<![\w.])(?<!\d,)(?P<number>\d{1,3}(?:\.\d{1,3}){1,4})(?![\w.])"
)

#: Dot leaders, the signature of a contents page: "Definitions ......... 12".
_DOT_LEADER_RE = re.compile(r"[.·…]{4,}[ \t]*\d{1,4}[ \t]*$")

#: A numbered entry ending in a bare page number, the other contents-page
#: signature: "20.2.1  Notice of Claim    145".
#:
#: The leading clause number is required. Without it the rule fired on any
#: line ending in a figure after a wide gap, which is how a schedule —
#: "Section 1 Completion Date   11", repeated down the page — was read as a
#: contents page and dropped whole. A contents entry exists to map a numbered
#: heading to a page; a line that names no clause is not one.
#:
#: What this gives up is an unnumbered contents page set without dot leaders
#: ("Definitions        5"). Those are rare in a contract, whose contents page
#: is a clause index by construction, and dot leaders still catch most of
#: them — whereas a schedule of rows ending in a figure is common, and
#: dropping one costs a whole page of evidence.
_NUMBERED_PAGENO_RE = re.compile(
    r"^[ \t]*\d{1,3}(?:\.\d{1,3}){0,3}[ \t]+"
    r"(?P<title>[^\n]*[A-Za-z’')\]])[ \t]{2,}\d{1,4}[ \t]*$"
)

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

#: Units of measure and currency markers seen in bills of quantities, rate
#: schedules and measurement sheets. Deliberately short: the numbers carry the
#: signal, and this only corroborates them.
_TABLE_UNIT_WORDS = frozenset(
    {
        "cum", "cuml", "sqm", "sm", "mtr", "rmt", "nos", "no", "each", "ltr",
        "kg", "tonne", "tons", "ton", "mt", "lm", "ha", "pcs", "set", "sets",
        "rs", "pkr", "usd", "lump", "ls",
    }
)

#: A token that is a quantity rather than a word: ``647.72``, ``1,685,808``,
#: ``010+500`` (a chainage), ``38220.46``, ``91.484``.
_NUMERIC_CHARS = set("0123456789,.+-/:")

#: Two quantities in a heading title is already two more than a clause heading
#: carries. "20.2 Claims for Payment and/or EOT" has none; "2 AggregateBase Cum
#: 647.72 2602.7 1,685,808" has three.
_TABLE_ROW_MIN_NUMBERS = 2

#: …or a title that is substantially made of quantities, which catches a short
#: row like "7 Mudflow 047+135 50.00".
_TABLE_ROW_NUMERIC_RATIO = 0.34

#: Words a title lower-cases by convention, so their case says nothing about
#: whether the line is a heading. "Engineer at Liberty to Object" is as
#: title-cased as "Notice of Claim"; counting "at", "to" and "of" against them
#: would read both as running prose.
_TITLE_STOPWORDS = frozenset(
    {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
        "or", "the", "to", "under", "with",
    }
)

#: How much of a contents entry's title must be capitalised. A contents entry
#: reproduces a heading, and a heading is title-cased; a table row's
#: description is not. This is the only thing separating "1 Some General
#: Title 1   2" (a contents entry) from "1 Item description number 1   3" (a
#: row of a table), which are otherwise the same shape.
_CONTENTS_TITLE_CAPS_RATIO = 0.6

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

#: The same, for a page topped by a front-matter marker. A short index —
#: "INDEX OF SUB-CLAUSES" over two entries — never reaches four, and its
#: title is the evidence that makes two enough.
_TOC_PAGE_MIN_HITS_MARKED = 2


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

    Both forms require a leading clause number and a title that reads like a
    heading. Neither requirement was there before, and between them they
    dropped two kinds of page whole: a schedule whose rows end in a figure,
    and a table whose rows are numbered and end in a quantity. Both are
    shaped exactly like a contents entry; what tells them apart is that a
    contents entry reproduces a heading, so it is title-cased and carries no
    quantities.

    >>> is_contents_line("1.1 Definitions ................ 12")
    True
    >>> is_contents_line("20.2.1  Notice of Claim    145")
    True
    >>> is_contents_line("20.2.1 Notice of Claim")
    False
    >>> is_contents_line("1 Item description number 1   3")
    False
    >>> is_contents_line("Section 1 Completion Date   11")
    False
    """
    stripped = line.rstrip()
    if not stripped:
        return False
    # Dot leaders are unambiguous: nothing but a contents page sets them.
    if _DOT_LEADER_RE.search(stripped):
        return True
    if len(stripped) > 110:
        return False
    entry = _CONTENTS_ENTRY_RE.match(stripped) or _NUMBERED_PAGENO_RE.match(stripped)
    if not entry:
        return False
    title = entry.group("title").rstrip()
    if title.endswith((".", ",", ";", ":")):
        return False
    return reads_as_a_heading_title(title)


def reads_as_a_heading_title(title: str) -> bool:
    """True if ``title`` is written the way a clause heading is written.

    Separated out so the judgement can be inspected on its own, the way
    :func:`numeric_profile` is. Quantities and the words a title lower-cases
    by convention are both excluded before the ratio is taken: counting them
    read "Engineer at Liberty to Object" as prose and "Item description
    number 1" as a heading, which is backwards on both.

    >>> reads_as_a_heading_title("Engineer at Liberty to Object")
    True
    >>> reads_as_a_heading_title("Item description number 1")
    False
    """
    if looks_like_table_row(title):
        return False
    words = [w for w in re.split(r"[ \t]+", title.strip()) if w]
    content = [
        w
        for w in words
        if not _is_quantity_token(w)
        and w.lower().strip(".,;:()'’") not in _TITLE_STOPWORDS
    ]
    if not content:
        return False
    capitalised = sum(1 for w in content if w[:1].isupper())
    return capitalised / len(content) >= _CONTENTS_TITLE_CAPS_RATIO


def _is_quantity_token(token: str) -> bool:
    """True if ``token`` is a number rather than a word.

    Strips the punctuation a column value carries — brackets, a trailing
    percent, a leading currency symbol — then requires a digit and nothing but
    digits and separators.

    >>> _is_quantity_token("1,685,808")
    True
    >>> _is_quantity_token("010+500")
    True
    >>> _is_quantity_token("Definitions")
    False
    >>> _is_quantity_token("A1(Shoulders)")
    False
    """
    core = token.strip("()[]{}\u201c\u201d\"'")
    core = core.lstrip("$\u00a3\u20ac\u20a8").rstrip("%")
    core = core.strip(".,;:")
    if not core:
        return False
    if not any(ch.isdigit() for ch in core):
        return False
    return all(ch in _NUMERIC_CHARS for ch in core)


def numeric_profile(title: str) -> tuple:
    """How much of ``title`` is quantities: ``(count, share_of_tokens)``.

    Separated from the scoring so the judgement can be inspected on its own —
    "why was this rejected" is answerable with one call.
    """
    tokens = [t for t in re.split(r"[ \t]+", title.strip()) if t]
    if not tokens:
        return 0, 0.0
    numeric = sum(1 for t in tokens if _is_quantity_token(t))
    return numeric, numeric / len(tokens)


def looks_like_table_row(title: str) -> bool:
    """True if a heading candidate's title is really a row of a table.

    Contracts and claims are full of priced tables — bills of quantities, rate
    schedules, measurement sheets — and a row of one is structurally identical
    to a clause heading: a line-leading number, then text. Read as headings
    they fabricate a clause hierarchy out of line items, and every chunk under
    that hierarchy is cited as a clause that does not exist. A nine-page cost
    annex produced 128 of them.

    The test is the quantities. A clause heading names an obligation and
    carries no figures; a table row is mostly figures.

    >>> looks_like_table_row("AggregateBase Cum 647.72 2602.7 1,685,808")
    True
    >>> looks_like_table_row("Mudflow 017+390 017+425 35.00 3.65 0.50 63.88")
    True
    >>> looks_like_table_row("Claims for Payment and/or EOT")
    False
    >>> looks_like_table_row("Payment of 10 per cent")
    False
    """
    count, ratio = numeric_profile(title)
    if count >= _TABLE_ROW_MIN_NUMBERS:
        return True
    if count and ratio >= _TABLE_ROW_NUMERIC_RATIO:
        return True
    # One quantity beside a unit of measure and little else is still a row:
    # "Prime Coat Sm 4233.60" reduced to its first columns.
    if count == 1:
        words = [w.strip("()[].,;:").lower() for w in re.split(r"[ \t]+", title) if w]
        if any(w in _TABLE_UNIT_WORDS for w in words) and len(words) <= 5:
            return True
    return False


def _is_structural_marker(line: str) -> bool:
    """True if the line is a front/back-matter section marker.

    A marker is a page title, so it is short and does not close a sentence.
    Without the punctuation test "Contents: the Contractor shall submit the
    list of materials." was a marker, because it begins with one of the words.
    """
    low = line.strip().lower()
    if not low or len(low) > 60:
        return False
    if low.endswith((".", ";")):
        return False
    for marker in _NON_CONTENT_MARKERS:
        if low == marker or low.startswith(marker + " ") or low.startswith(marker + ":"):
            return True
    return False


def classify_page(page: PageText) -> bool:
    """Return True if the page should be treated as body content.

    A page is excluded on the share of its lines that are contents entries,
    never on what its first line begins with. The marker words only lower the
    number of entries required, and cannot drop a page on their own.

    Deciding on the first line was the single most expensive defect in this
    module. "General Conditions" is the running header of every even page of
    the FIDIC 2017 forms, so the rule fired on a third of the book: 73 of the
    Yellow Book's 231 pages were classified as front matter and never
    indexed, taking Sub-Clauses 20.2, 20.2.1 and 20.2.5 — the notice-of-claim
    provisions the product exists to answer on — out of the knowledge base
    altogether. The same rule dropped any page opening with "Annex" or
    "Appendix", which on a claim submission is where the evidence is.

    A marker still means something: it is what separates a four-line index
    from a body page that happens to carry two contents-like lines. So it
    halves the evidence needed, and nothing more.
    """
    lines = [ln for ln in page.text.splitlines() if ln.strip()]
    if not lines:
        return False

    toc_hits = sum(1 for ln in lines if is_contents_line(ln))
    if not toc_hits:
        return True
    if toc_hits / len(lines) < _TOC_PAGE_RATIO:
        return True

    marked = any(_is_structural_marker(ln) for ln in lines[:3])
    minimum = _TOC_PAGE_MIN_HITS_MARKED if marked else _TOC_PAGE_MIN_HITS
    return toc_hits < minimum


def _iter_lines(text: str) -> Iterable[tuple[str, str]]:
    """Yield ``(line, line_with_terminator)`` for each line of ``text``.

    A heading's ``char_offset`` has to index the raw page text, because
    chunking slices the page with it and the viewer highlights with it. The
    obvious ``len(line) + 1`` assumes every line ends in one character, which
    CRLF text breaks: the cut drifted one character further left per line, so
    on a Windows-authored document the clause boundaries landed mid-word and
    the highlight pointed at the wrong provision. ``splitlines(keepends=True)``
    splits at exactly the same places as ``splitlines()`` while keeping each
    terminator, so the real width is available without normalising the text or
    guessing which of the eight line separators Python recognises was used.
    """
    return zip(text.splitlines(), text.splitlines(keepends=True))


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

    # So is a table row. Rejected rather than penalised: the scoring signals
    # above are about how heading-like a line reads, and a priced row reads
    # perfectly heading-like — short, title-cased, no full stop. It scored 0.55
    # against a 0.55 threshold, so no penalty short of disqualification is
    # stable.
    if looks_like_table_row(title):
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
    # A marginal note is a label; a row of quantities is not one. Only the
    # note is tested: the provision's text that follows it is prose, and prose
    # cites clause numbers and states periods and percentages as a matter of
    # course. Judging a heading by the figures in the paragraph under it threw
    # away real headings ("4.1 Impartiality") whose body happened to
    # cross-refer twice.
    if looks_like_table_row(title):
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
        for line_index, (line, raw_line) in enumerate(_iter_lines(page.text)):
            line_start = offset
            # Advanced here, before any branch, so the `continue` below cannot
            # skip it. ``raw_line`` carries its own terminator, so the offset
            # stays in raw-page coordinates whatever the line ending is.
            offset += len(raw_line)

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
                                char_offset=line_start,
                                confidence=round(confidence, 3),
                                depth=clause_depth(number),
                            )
                        )
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
                                char_offset=line_start + margin.start("number"),
                                confidence=round(confidence, 3),
                                depth=clause_depth(number),
                            )
                        )

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


def heading_paths(roots: Sequence[ClauseNode]) -> dict:
    """Clause number -> its title path from the top of the hierarchy down.

    ``{"20.2.1": ("Employer's and Contractor's Claims", "Claims For Payment",
    "Notice of Claim")}``. Chunking prepends this so a fragment reading "shall
    give a Notice within 28 days" carries the provision it sits under; without
    it every chunk's ancestry was empty, because the stage that chunks never
    asked for it.
    """
    out: dict = {}

    def walk(node: ClauseNode, prefix: tuple) -> None:
        path = prefix + (node.title,)
        out[node.number] = path
        for child in node.children:
            walk(child, path)

    for root in roots:
        walk(root, ())
    return out


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
