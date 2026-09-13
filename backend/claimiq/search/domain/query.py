"""Query understanding.

The first stage of retrieval: work out what the user is actually asking for
before deciding how to search for it.

The prototype passed the raw question straight to a similarity search
(`../backend/rag.py:310`), with one exception — it regex-scanned for clause
numbers and then repeated them in the query string to bias the embedding. That
instinct was right; the mechanism was not. Here the extracted structure is used
to *choose retrievers and filters*, not to nudge a vector.

Three things are extracted:

1. **Clause references** — routed to exact relational lookup, not similarity.
2. **Intent** — a notice-compliance question needs correspondence and dates; a
   clause-meaning question needs the knowledge base. Same words, different
   corpus.
3. **Constraints** — dates, amounts, parties, and edition hints, which become
   metadata filters rather than tokens in a bag of words.

Query expansion is deliberately conservative. Construction contracts use
defined terms with precise meanings — "the Works", "Taking-Over Certificate",
"Notice" — and expanding those into near-synonyms actively harms precision,
because "notice" and "letter" are not interchangeable when the question is
whether a contractual Notice was given.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from claimiq.ingestion.domain.clause_detection import normalise_clause_number


class QueryIntent(str, Enum):
    """What kind of question this is.

    Drives which retrievers run and which corpus is searched. Classification is
    keyword-based and explicit rather than learned: it runs offline, must be
    debuggable from the question text alone, and a wrong answer must be
    explainable to the user who asked.
    """

    CLAUSE_LOOKUP = "clause_lookup"
    """What does clause X say or require. Knowledge base and contract."""

    NOTICE_COMPLIANCE = "notice_compliance"
    """Was notice given, and in time. Correspondence plus the notice provision."""

    ENTITLEMENT = "entitlement"
    """Does the contract support this claim. Contract plus knowledge base."""

    EVIDENCE_SEARCH = "evidence_search"
    """Which documents support or contradict something. Project documents."""

    CHRONOLOGY = "chronology"
    """What happened and when. Correspondence and events."""

    QUANTUM = "quantum"
    """Amounts, costs and valuation."""

    DEFINITION = "definition"
    """What does a defined term mean."""

    GENERAL = "general"
    """Everything else."""


@dataclass(frozen=True)
class ExtractedDate:
    raw: str
    position: int


@dataclass(frozen=True)
class ExtractedAmount:
    raw: str
    currency: str | None
    position: int


@dataclass
class QueryAnalysis:
    """The structured reading of a question."""

    text: str
    intent: QueryIntent
    clause_references: list[str] = field(default_factory=list)
    dates: list[ExtractedDate] = field(default_factory=list)
    amounts: list[ExtractedAmount] = field(default_factory=list)
    defined_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    lexical_query: str = ""
    intent_scores: dict[str, int] = field(default_factory=dict)

    @property
    def has_clause_reference(self) -> bool:
        return bool(self.clause_references)

    @property
    def should_use_clause_lookup(self) -> bool:
        """Whether exact clause retrieval should run.

        Exact lookup is cheap and precise. Whenever a clause number is present
        it runs, regardless of intent — the text of the named clause is
        relevant to any question that names it.
        """
        return self.has_clause_reference

    @property
    def should_search_knowledge_base(self) -> bool:
        """Whether the standard-form knowledge base is worth reading.

        Evidence and chronology questions are about what happened on *this*
        project; the standard form has nothing to say about that, and including
        it spends context budget on provisions nobody asked about.
        """
        return self.intent not in (
            QueryIntent.EVIDENCE_SEARCH,
            QueryIntent.CHRONOLOGY,
        )

    def describe(self) -> str:
        parts = [f"intent={self.intent.value}"]
        if self.clause_references:
            parts.append(f"clauses={','.join(self.clause_references)}")
        if self.dates:
            parts.append(f"dates={len(self.dates)}")
        if self.amounts:
            parts.append(f"amounts={len(self.amounts)}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_CLAUSE_KEYWORD_RE = re.compile(
    r"\b(?:sub-?clauses?|clauses?|articles?|sections?|paragraphs?)\s+"
    r"(\d{1,3}(?:\.\d{1,3}){0,4})\b",
    re.IGNORECASE,
)

#: A bare dotted number. Requires a dot, so plain integers ("28 days") are out.
_BARE_CLAUSE_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){1,4})(?![\w.])")

#: Units that disqualify a preceding number from being a clause reference.
_UNIT_AFTER_RE = re.compile(
    r"^\s*(?:%|m\b|km\b|mm\b|cm\b|kg\b|t\b|million|billion|thousand|"
    r"days?\b|weeks?\b|months?\b|years?\b|hours?\b)",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4}"
    r")\b",
    re.IGNORECASE,
)

_AMOUNT_RE = re.compile(
    r"(?:(?P<cur>USD|EUR|GBP|AED|SAR|PKR|INR|AUD|CAD|\$|€|£)\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}|\d{4,})"
    r"(?:\s?(?P<scale>million|billion|thousand|m\b|bn\b|k\b))?",
    re.IGNORECASE,
)

#: FIDIC defined terms. Capitalised in the contract and meaning something
#: specific; matched case-sensitively so "notice" in ordinary prose is not
#: mistaken for the defined "Notice".
_DEFINED_TERMS = (
    "Notice", "Claim", "Works", "Site", "Contract", "Contractor", "Employer",
    "Engineer", "Variation", "Taking-Over Certificate", "Performance Security",
    "Time for Completion", "Defects Notification Period", "Contract Price",
    "Provisional Sum", "Accepted Contract Amount", "Final Payment Certificate",
    "Interim Payment Certificate", "Statement", "Programme", "Exceptional Event",
    "Force Majeure", "Dispute", "DAAB", "Party", "Sub-Contractor",
)

_STOPWORDS = frozenset(
    """a an the is are was were be been being do does did what which who whom
    whose when where why how this that these those there here it its of in on
    at to for with from by as and or but if then than so can could should would
    may might must shall will i we you they he she them us our your their""".split()
)

#: Keyword → intent. Weighted so a specific signal outranks a generic one.
_INTENT_KEYWORDS: dict[QueryIntent, tuple[tuple[str, int], ...]] = {
    QueryIntent.NOTICE_COMPLIANCE: (
        ("notice", 3), ("notified", 3), ("notification", 3), ("time bar", 5),
        ("time-barred", 5), ("within the period", 4), ("in time", 3),
        ("late", 2), ("28 days", 3), ("condition precedent", 5), ("served", 2),
    ),
    QueryIntent.ENTITLEMENT: (
        ("entitled", 4), ("entitlement", 4), ("valid", 3), ("succeed", 3),
        ("basis for", 3), ("support the claim", 4), ("does the contract allow", 4),
        ("can the contractor claim", 4), ("grounds", 2), ("merit", 2),
    ),
    QueryIntent.EVIDENCE_SEARCH: (
        ("which documents", 5), ("what evidence", 5), ("supporting", 3),
        ("contradict", 4), ("show that", 2), ("prove", 3), ("records", 2),
        ("correspondence about", 4), ("find", 2), ("any document", 4),
    ),
    QueryIntent.CHRONOLOGY: (
        ("timeline", 5), ("chronology", 5), ("sequence of events", 5),
        ("what happened", 4), ("when did", 4), ("history", 2), ("order of", 2),
    ),
    QueryIntent.QUANTUM: (
        ("how much", 4), ("amount", 3), ("cost", 3), ("quantum", 5),
        ("valuation", 4), ("value of", 3), ("sum", 2), ("price", 2),
        ("payment", 2),
    ),
    QueryIntent.DEFINITION: (
        ("what does", 2), ("define", 4), ("definition", 5), ("meaning of", 4),
        ("what is meant by", 5), ("means", 2),
    ),
    QueryIntent.CLAUSE_LOOKUP: (
        ("what does clause", 5), ("require", 3), ("provide", 2), ("say", 2),
        ("state", 2), ("under clause", 4), ("obligation", 3), ("provision", 3),
    ),
}


def extract_clause_references(text: str) -> list[str]:
    """Extract clause numbers, keyword-introduced or bare.

    Order is preserved and duplicates removed, so the first mention leads —
    typically the clause the question is actually about.
    """
    found: list[str] = []
    consumed: list[tuple[int, int]] = []

    for match in _CLAUSE_KEYWORD_RE.finditer(text):
        number = normalise_clause_number(match.group(1))
        consumed.append(match.span())
        if number and number not in found:
            found.append(number)

    for match in _BARE_CLAUSE_RE.finditer(text):
        start, end = match.span()
        if any(s <= start < e for s, e in consumed):
            continue
        if _UNIT_AFTER_RE.match(text[end : end + 12]):
            continue
        number = normalise_clause_number(match.group(1))
        if number and number not in found:
            found.append(number)

    return found


def extract_dates(text: str) -> list[ExtractedDate]:
    return [ExtractedDate(raw=m.group(0), position=m.start()) for m in _DATE_RE.finditer(text)]


#: Bare four-digit numbers in this range are years, not sums. Without this a
#: question mentioning "14 March 2026" reports 2026 as a monetary amount.
_YEAR_MIN = 1900
_YEAR_MAX = 2100


def extract_amounts(text: str) -> list[ExtractedAmount]:
    """Extract monetary amounts.

    Requires a currency marker, a scale word, a thousands separator or a
    decimal. A bare "28" is a period and a bare "2026" is a year; neither is a
    sum. Numbers falling inside a detected date are skipped outright, since a
    date component is never an amount however it is written.
    """
    date_spans = [(m.start(), m.end()) for m in _DATE_RE.finditer(text)]
    amounts: list[ExtractedAmount] = []

    for match in _AMOUNT_RE.finditer(text):
        start = match.start()
        if any(s <= start < e for s, e in date_spans):
            continue

        currency = match.group("cur")
        number = match.group("num")
        scale = match.group("scale")

        if not currency and not scale:
            digits = number.replace(",", "")
            if "," not in number and "." not in number:
                if len(digits) < 4:
                    continue
                if digits.isdigit() and _YEAR_MIN <= int(digits) <= _YEAR_MAX:
                    continue

        amounts.append(
            ExtractedAmount(
                raw=match.group(0).strip(),
                currency=currency.upper() if currency else None,
                position=start,
            )
        )
    return amounts


def extract_defined_terms(text: str) -> list[str]:
    """Find capitalised FIDIC defined terms.

    Case-sensitive on purpose. "Notice" is a defined term with a specific
    contractual meaning; "notice" in ordinary prose is not, and conflating them
    is how a search for correspondence turns into a search for the definitions
    clause.
    """
    found: list[str] = []
    for term in _DEFINED_TERMS:
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, text) and term not in found:
            found.append(term)
    return found


def classify_intent(text: str) -> tuple[QueryIntent, dict[str, int]]:
    """Classify the question, returning the intent and every score.

    Scores are returned so a surprising classification is explainable rather
    than mysterious — it goes into AI observability alongside the answer.
    """
    lowered = " " + text.lower().strip() + " "
    scores: dict[str, int] = {}

    for intent, keywords in _INTENT_KEYWORDS.items():
        score = 0
        for phrase, weight in keywords:
            if phrase in lowered:
                score += weight
        if score:
            scores[intent.value] = score

    # A clause number present with no other strong signal is a clause lookup.
    if extract_clause_references(text):
        scores[QueryIntent.CLAUSE_LOOKUP.value] = (
            scores.get(QueryIntent.CLAUSE_LOOKUP.value, 0) + 2
        )

    if not scores:
        return QueryIntent.GENERAL, {}

    best = max(scores.items(), key=lambda item: (item[1], item[0]))
    return QueryIntent(best[0]), scores


def extract_keywords(text: str) -> list[str]:
    """Content words, for the lexical query. Order preserved, duplicates removed."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
    keywords: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in _STOPWORDS or len(lowered) < 3:
            continue
        if lowered not in keywords:
            keywords.append(lowered)
    return keywords


def build_lexical_query(analysis_keywords: Sequence[str], defined_terms: Sequence[str]) -> str:
    """Build the string handed to PostgreSQL full-text search.

    Defined terms lead, because they are the highest-signal tokens in a
    contract question and `ts_rank_cd` rewards proximity to the query terms.
    """
    ordered: list[str] = []
    for term in defined_terms:
        for part in term.lower().split():
            if part not in ordered:
                ordered.append(part)
    for keyword in analysis_keywords:
        if keyword not in ordered:
            ordered.append(keyword)
    return " ".join(ordered)


def analyse(text: str) -> QueryAnalysis:
    """Run the full analysis over a question.

    Raises nothing: an unparseable question degrades to ``GENERAL`` intent with
    keyword extraction, which is still a usable search.
    """
    cleaned = (text or "").strip()
    intent, scores = classify_intent(cleaned)
    keywords = extract_keywords(cleaned)
    defined_terms = extract_defined_terms(cleaned)

    return QueryAnalysis(
        text=cleaned,
        intent=intent,
        clause_references=extract_clause_references(cleaned),
        dates=extract_dates(cleaned),
        amounts=extract_amounts(cleaned),
        defined_terms=defined_terms,
        keywords=keywords,
        lexical_query=build_lexical_query(keywords, defined_terms),
        intent_scores=scores,
    )


def suggested_document_types(intent: QueryIntent) -> frozenset[str]:
    """Document types worth prioritising for an intent.

    A hint, not a hard filter. Narrowing to these and finding nothing is worse
    than searching wider, so the retrieval service uses this to weight rather
    than to exclude.
    """
    if intent is QueryIntent.NOTICE_COMPLIANCE:
        return frozenset({"notice", "letter", "email", "engineers_instruction", "response"})
    if intent is QueryIntent.EVIDENCE_SEARCH:
        return frozenset(
            {"daily_report", "progress_report", "photograph", "site_record",
             "measurement_sheet", "timesheet", "programme", "updated_programme"}
        )
    if intent is QueryIntent.CHRONOLOGY:
        return frozenset({"letter", "email", "notice", "meeting_minutes", "daily_report"})
    if intent is QueryIntent.QUANTUM:
        return frozenset(
            {"payment_record", "invoice", "measurement_sheet", "bill_of_quantities",
             "cost_claim", "payment_claim"}
        )
    if intent in (QueryIntent.CLAUSE_LOOKUP, QueryIntent.DEFINITION, QueryIntent.ENTITLEMENT):
        return frozenset(
            {"conditions_of_contract", "particular_conditions", "general_conditions",
             "contract_agreement", "standard_form", "amendment"}
        )
    return frozenset()


def merge_clause_references(*groups: Iterable[str]) -> list[str]:
    """Merge clause reference lists, preserving first-seen order."""
    merged: list[str] = []
    for group in groups:
        for clause in group:
            if clause and clause not in merged:
                merged.append(clause)
    return merged
