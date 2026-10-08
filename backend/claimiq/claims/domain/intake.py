"""Reading notices and claim bundles as they arrive.

Two jobs a claims team does by hand on every submission:

- **A notice letter** is read for the date it bears, who it is from and to,
  the clause it is given under, and whether it is the notice or the detailed
  particulars that follow. Those are exactly the facts the deadline check
  needs, and they are on the first page.
- **A claim bundle** — the covering letter with its annexures in one PDF — is
  split into the documents it is made of, so the letter is filed as the claim
  and each annexure as evidence of what it proves.

A model does the reading; this module decides what to believe. Every value a
model returns is normalised, checked against the page it says it came from,
and backed by a deterministic reading where one is possible, so a model that
is wrong, slow or absent leaves the person with a usable proposal rather than
nothing. Nothing here writes: every reading is a proposal a person confirms.

Pure stdlib; must run on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from claimiq.claims.domain.extraction import (
    normalise_clauses,
    normalise_date,
    normalise_text,
    quote_appears,
)

# ---------------------------------------------------------------------------
# Notices
# ---------------------------------------------------------------------------

NOTICE_OF_CLAIM = "notice_of_claim"
DETAILED_PARTICULARS = "detailed_particulars"
OTHER_LETTER = "other"
NOTICE_KINDS = (NOTICE_OF_CLAIM, DETAILED_PARTICULARS, OTHER_LETTER)

NOTICE_FIELD_LABELS = {
    "letter_date": "Date on the letter",
    "received_date": "Date received",
    "reference": "Letter reference",
    "subject": "Subject",
    "sender": "From",
    "recipient": "To",
    "clauses": "Given under clause",
    "notice_kind": "What it is",
    "event_description": "Event notified",
    "event_date": "Date of the event",
    "claim_reference": "Claim reference",
}

_DATE_FIELDS = ("letter_date", "received_date", "event_date")

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

#: A date written in any of the ways a letter writes one.
DATE_PATTERN = re.compile(
    r"\b(?:"
    r"\d{1,2}(?:st|nd|rd|th)?[\s\-./]*(?:" + _MONTHS + r")\.?[\s\-.,/]*\d{2,4}"
    r"|(?:" + _MONTHS + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b",
    re.IGNORECASE,
)

#: "Date: 12 June 1997", "Dated 12.06.1997", "Our Ref ... Date ...".
_LABELLED_DATE = re.compile(r"\bdated?\s*[:\-]?\s*", re.IGNORECASE)
_RECEIVED_DATE = re.compile(r"\breceived\b[^\n]{0,30}?", re.IGNORECASE)
_REFERENCE = re.compile(
    r"\b(?:our\s+ref(?:erence)?|ref(?:erence)?\.?\s*(?:no\.?)?|letter\s+no\.?)\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-&.() ]{2,40}?)(?=\s{2,}|\s+date\b|\s*$|\n)",
    re.IGNORECASE | re.MULTILINE,
)
_CLAUSE_MENTION = re.compile(
    r"\b(?:sub-?\s*clauses?|clauses?)\s+(\d{1,3}(?:\.\d{1,3}){0,3})", re.IGNORECASE
)
_PARTICULARS = re.compile(
    r"detailed\s+particulars|fully\s+detailed\s+claim|particularised\s+claim|"
    r"interim\s+particulars|final\s+particulars|account\s+giving\s+detailed",
    re.IGNORECASE,
)
_NOTICE = re.compile(
    r"notice\s+of\s+(?:claim|delay|intention)|intention\s+to\s+claim|"
    r"hereby\s+(?:give|gives|notify|notifies)|notice\s+(?:is|under)|notification",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReadField:
    """One fact read off a document, and how far it can be trusted."""

    name: str
    value: Any = None
    quote: str = ""
    verified: bool = True
    source: str = "model"
    """``model`` when the model read it and the page bears it out,
    ``pattern`` when it was read deterministically from the page,
    ``none`` when nothing was found."""
    note: str = ""

    @property
    def found(self) -> bool:
        return self.value not in (None, "", (), [])


@dataclass
class NoticeReading:
    fields: Dict[str, ReadField] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    dates_on_page: List[Tuple[str, Optional[date]]] = field(default_factory=list)
    """Every date written on the first page, so a wrong reading can be
    corrected by picking the right one rather than typing it."""

    def value(self, name: str) -> Any:
        item = self.fields.get(name)
        return item.value if item else None


def read_date(raw: Any) -> Tuple[Optional[date], str]:
    """A date as written, including one OCR has run together ("12September 1998")."""
    text = normalise_text(raw)
    if not text:
        return None, ""
    value, note = normalise_date(text)
    if value is None:
        spaced = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", text)
        value, note = normalise_date(spaced)
    return value, note


def dates_in(text: str) -> List[Tuple[str, Optional[date]]]:
    """Each date written in ``text``, as written and as understood."""
    found: List[Tuple[str, Optional[date]]] = []
    seen = set()
    for match in DATE_PATTERN.finditer(text or ""):
        raw = " ".join(match.group(0).split())
        if raw.lower() in seen:
            continue
        seen.add(raw.lower())
        value, _ = read_date(raw)
        found.append((raw, value))
    return found


def labelled_date(text: str, label: "re.Pattern[str]") -> Tuple[Optional[date], str]:
    """The first date that follows ``label`` closely, with the words read."""
    for match in label.finditer(text or ""):
        window = text[match.end() : match.end() + 40]
        found = DATE_PATTERN.search(window)
        if found and found.start() <= 12:
            raw = " ".join(found.group(0).split())
            value, _ = read_date(raw)
            if value:
                return value, " ".join(text[match.start() : match.end() + found.end()].split())
    return None, ""


_FUTURE_PARTICULARS = re.compile(
    r"[^.]*\b(?:will|shall|to)\s+(?:follow|be\s+(?:submitted|sent|provided))[^.]*\.?"
    r"|[^.]*\bin\s+due\s+course[^.]*\.?",
    re.IGNORECASE,
)


def notice_kind_from_text(text: str) -> Optional[str]:
    """Notice or particulars, read from the words the letter uses.

    A notice routinely promises that particulars will follow; that promise
    does not make it the particulars. Sentences about what is still to come
    are set aside before looking.
    """
    head = _FUTURE_PARTICULARS.sub(" ", (text or "")[:3000])
    if _PARTICULARS.search(head):
        return DETAILED_PARTICULARS
    if _NOTICE.search(head):
        return NOTICE_OF_CLAIM
    return None


def clauses_in(text: str) -> Tuple[str, ...]:
    """Clause numbers the text cites, in order, once each."""
    return tuple(dict.fromkeys(m.group(1) for m in _CLAUSE_MENTION.finditer(text or "")))


_REF_LABEL = re.compile(r"^\s*(?:our\s+|your\s+)?ref(?:erence)?\.?\s*(?:no\.?)?\s*[:\-]?\s*", re.IGNORECASE)


def _normalise_reference(value: Any) -> Optional[str]:
    """The reference itself, without the "Our Ref:" label a model often keeps."""
    text = normalise_text(value)
    if not text:
        return None
    return _REF_LABEL.sub("", text).strip(" .,:") or None


def _normalise_kind(value: Any) -> Optional[str]:
    text = (normalise_text(value) or "").lower().replace(" ", "_").replace("-", "_")
    if text in NOTICE_KINDS:
        return text
    if "particular" in text or "detailed" in text:
        return DETAILED_PARTICULARS
    if "notice" in text or "intention" in text:
        return NOTICE_OF_CLAIM
    return OTHER_LETTER if text else None


def build_notice_reading(payload: Mapping[str, Any], *, text: str, first_page: str = "") -> NoticeReading:
    """Turn a model's reading of a letter into a checked proposal.

    Args:
        payload: The model's JSON, or ``{}`` where no model was available.
        text: The letter's text, which every quote is checked against.
        first_page: The first page alone, where a letter's own date and
            reference are; used for the deterministic reading.
    """
    payload = payload if isinstance(payload, Mapping) else {}
    quotes = payload.get("quotes") if isinstance(payload.get("quotes"), Mapping) else {}
    head = first_page or text[:2500]
    reading = NoticeReading(dates_on_page=dates_in(head))
    for raw_note in payload.get("notes") or ():
        note = normalise_text(raw_note)
        if note:
            reading.notes.append(note)

    for name in NOTICE_FIELD_LABELS:
        raw = payload.get(name)
        quote = normalise_text(quotes.get(name)) or ""
        note = ""
        if name in _DATE_FIELDS:
            value, note = read_date(raw)
        elif name == "clauses":
            value, note = normalise_clauses(raw)
            value = tuple(value) if value else ()
        elif name == "notice_kind":
            value = _normalise_kind(raw)
        elif name == "reference":
            value = _normalise_reference(raw)
        else:
            value = normalise_text(raw)

        verified = True
        # A classification has no words to quote; the rest must be on the page.
        if value not in (None, "", ()) and name != "notice_kind":
            verified = bool(quote) and quote_appears(quote, text)
            if name in _DATE_FIELDS and value and not verified:
                # A date the page does not bear is not used as it stands: the
                # deadline turns on it. Kept, marked, for the person to check.
                note = note or "Not found on the page as quoted; check it against the letter."
        reading.fields[name] = ReadField(
            name=name,
            value=value,
            quote=quote,
            verified=verified,
            source="model" if value not in (None, "", ()) else "none",
            note=note,
        )

    _fill_from_page(reading, text=text, head=head)
    return reading


def _fill_from_page(reading: NoticeReading, *, text: str, head: str) -> None:
    """Deterministic readings where the model left a gap or could not be confirmed."""

    def weak(name: str) -> bool:
        item = reading.fields.get(name)
        return item is None or not item.found or not item.verified

    if weak("letter_date"):
        value, quote = labelled_date(head, _LABELLED_DATE)
        if value is None and reading.dates_on_page:
            # The first date on the page is the letter's own in nearly every
            # format; offered as a reading, not asserted.
            raw, first = reading.dates_on_page[0]
            value, quote = first, raw
        if value is not None:
            reading.fields["letter_date"] = ReadField(
                "letter_date", value, quote, True, "pattern",
                "Read from the top of the letter. Check it is the letter's own date.",
            )
    if weak("received_date"):
        value, quote = labelled_date(text, _RECEIVED_DATE)
        if value is not None:
            reading.fields["received_date"] = ReadField(
                "received_date", value, quote, True, "pattern", "Read from a receipt mark."
            )
    if weak("clauses"):
        clauses = clauses_in(text[:4000])
        if clauses:
            reading.fields["clauses"] = ReadField(
                "clauses", clauses, "", True, "pattern",
                "Clause numbers cited in the letter. Keep the one it is given under.",
            )
    if weak("notice_kind"):
        kind = notice_kind_from_text(text)
        if kind:
            reading.fields["notice_kind"] = ReadField(
                "notice_kind", kind, "", True, "pattern", "Read from the words the letter uses."
            )
    if weak("reference"):
        match = _REFERENCE.search(head)
        if match:
            reading.fields["reference"] = ReadField(
                "reference", match.group(1).strip(" .,:"), match.group(0).strip(), True, "pattern"
            )


def reading_payload(reading: NoticeReading) -> Dict[str, Any]:
    fields = []
    for name, label in NOTICE_FIELD_LABELS.items():
        item = reading.fields.get(name) or ReadField(name, source="none")
        value = item.value
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = list(value)
        fields.append(
            {
                "name": name,
                "label": label,
                "value": value,
                "quote": item.quote,
                "verified": item.verified,
                "source": item.source,
                "note": item.note,
            }
        )
    return {
        "fields": fields,
        "notes": list(reading.notes),
        "dates_on_page": [
            {"text": raw, "value": value.isoformat() if value else None}
            for raw, value in reading.dates_on_page
        ],
    }


# ---------------------------------------------------------------------------
# Claim bundles
# ---------------------------------------------------------------------------

#: What each kind of bundle document proves, by the evidence element it
#: addresses. ``None`` where it proves nothing by itself, or is not evidence.
KIND_TO_ELEMENT: Dict[str, Optional[str]] = {
    "claim_letter": None,
    "notice": "notice",
    "correspondence": "causation",
    "programme": "time_impact",
    "site_record": "event",
    "minutes": "causation",
    "invoice_cost": "cost_impact",
    "measurement": "quantum",
    "calculation": "quantum",
    "photograph": "event",
    "drawing": "event",
    "contract_extract": "responsibility",
    "report": "causation",
    "other": None,
}

KIND_LABELS = {
    "claim_letter": "Claim letter",
    "notice": "Notice",
    "correspondence": "Letter / correspondence",
    "programme": "Programme / time analysis",
    "site_record": "Site record / diary / progress report",
    "minutes": "Meeting minutes",
    "invoice_cost": "Invoice / cost record",
    "measurement": "Measurement sheet",
    "calculation": "Calculation of time or money",
    "photograph": "Photograph",
    "drawing": "Drawing",
    "contract_extract": "Contract extract",
    "report": "Report",
    "other": "Other",
}

#: Heading words that start a new document in a bundle.
_ANNEX = re.compile(
    r"^\W*(?:annex(?:ure)?|appendix|attachment|enclosure|exhibit|schedule|tab)\s*[-–:.]?\s*[A-Z0-9]{0,4}\b",
    re.IGNORECASE | re.MULTILINE,
)
_LETTER_START = re.compile(
    r"\b(?:our\s+ref|your\s+ref|ref(?:erence)?\s*(?:no\.?)?\s*[:\-])|\bdear\s+(?:sir|sirs|madam|mr|ms)",
    re.IGNORECASE,
)
_PAGE_ONE = re.compile(r"\bpage\s+1\s+of\s+\d+\b|\b1\s*/\s*\d+\s*$", re.IGNORECASE | re.MULTILINE)

#: Keyword evidence for each kind, strongest first. Used when no model is
#: available and to check a model's labels.
_KIND_WORDS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("claim_letter", re.compile(r"claim\s+for\s+(?:an\s+)?(?:extension|additional|payment)|particularised\s+claim|we\s+(?:hereby\s+)?claim|this\s+letter\s+constitutes", re.I)),
    ("notice", re.compile(r"notice\s+of\s+(?:claim|delay|intention)|intention\s+to\s+claim|hereby\s+give\s+notice", re.I)),
    ("minutes", re.compile(r"minutes\s+of\s+(?:the\s+)?(?:meeting|progress)|meeting\s+no\.?\s*\d|attendees|present\s*:", re.I)),
    ("programme", re.compile(r"programme|baseline|critical\s+path|gantt|time\s+impact\s+analysis|activity\s+id", re.I)),
    ("site_record", re.compile(r"daily\s+(?:report|diary|record)|site\s+diary|progress\s+report|weather|labour\s+on\s+site", re.I)),
    ("invoice_cost", re.compile(r"\binvoice\b|receipt|payment\s+certificate|cost\s+(?:record|breakdown|summary)|amount\s+due", re.I)),
    ("measurement", re.compile(r"measurement\s+sheet|quantity|bill\s+of\s+quantities|\bboq\b", re.I)),
    ("calculation", re.compile(r"calculation|quantum|overheads?\s+(?:calc|claim)|hudson|emden|eichleay|evaluation\s+of", re.I)),
    ("photograph", re.compile(r"photo(?:graph)?\s*(?:no|\d)|photographs?\s+taken", re.I)),
    ("drawing", re.compile(r"drawing\s+no|dwg\.?\s*no|scale\s+1\s*:", re.I)),
    ("contract_extract", re.compile(r"conditions\s+of\s+(?:contract|particular\s+application)|sub-?clause\s+\d+\.\d+\s+[A-Z]", re.I)),
    ("report", re.compile(r"\breport\b|investigation|findings", re.I)),
    ("correspondence", re.compile(r"dear\s+sirs?|yours\s+(?:faithfully|sincerely)", re.I)),
)


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass
class Segment:
    first_page: int
    last_page: int
    kind: str
    title: str = ""
    date_text: str = ""
    date_value: Optional[date] = None
    source: str = "model"

    @property
    def pages(self) -> int:
        return self.last_page - self.first_page + 1


def _page_date(text: str) -> Tuple[str, Optional[date]]:
    """The date a document bears: beside "Date:" first, else the first one near the top."""
    value, quote = labelled_date(text[:1200], _LABELLED_DATE)
    if value is not None:
        found = DATE_PATTERN.search(quote)
        return (" ".join(found.group(0).split()) if found else quote), value
    dates = dates_in(text[:800])
    return (dates[0][0], dates[0][1]) if dates else ("", None)


def guess_kind(text: str) -> str:
    for kind, pattern in _KIND_WORDS:
        if pattern.search(text or ""):
            return kind
    return "other"


def _title_from(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = " ".join(line.split()).strip(" -–:")
        if 6 <= len(cleaned) <= 120 and re.search(r"[A-Za-z]{3}", cleaned):
            return cleaned
    return ""


def starts_document(text: str) -> bool:
    head = (text or "")[:600]
    return bool(_ANNEX.search(head) or _LETTER_START.search(head) or _PAGE_ONE.search(head))


def heuristic_segments(pages: Sequence[PageText]) -> List[Segment]:
    """Split a bundle without a model: new document at a letterhead or annex heading."""
    segments: List[Segment] = []
    for page in pages:
        if not segments or starts_document(page.text):
            kind = guess_kind(page.text[:2500])
            raw_date, value = _page_date(page.text)
            segments.append(
                Segment(
                    first_page=page.page_number,
                    last_page=page.page_number,
                    kind=kind,
                    title=_title_from(page.text),
                    date_text=raw_date,
                    date_value=value,
                    source="pattern",
                )
            )
        else:
            segments[-1].last_page = page.page_number
    _mark_claim_letter(segments, pages)
    return segments


def _mark_claim_letter(segments: List[Segment], pages: Sequence[PageText]) -> None:
    """Make sure one segment is the claim letter, where one can be told."""
    if any(s.kind == "claim_letter" for s in segments) or not segments:
        return
    text_of = {p.page_number: p.text for p in pages}
    for segment in segments:
        body = " ".join(text_of.get(n, "") for n in range(segment.first_page, segment.last_page + 1))
        if re.search(r"\bclaim\b", body, re.I) and re.search(r"dear\s+sirs?|we\s+refer|herewith", body, re.I):
            segment.kind = "claim_letter"
            return


def merge_segments(
    proposed: Sequence[Mapping[str, Any]], pages: Sequence[PageText]
) -> Tuple[List[Segment], List[str]]:
    """Turn a model's split into a clean cover of every page.

    Models skip pages, overlap ranges and invent page numbers. Ranges are
    clipped to the bundle, sorted, de-overlapped, and any page left uncovered
    is given a document of its own read from the page, so the result always
    accounts for every page exactly once.
    """
    notes: List[str] = []
    numbers = [p.page_number for p in pages]
    if not numbers:
        return [], notes
    low, high = min(numbers), max(numbers)
    text_of = {p.page_number: p.text for p in pages}

    clean: List[Segment] = []
    for item in proposed or ():
        if not isinstance(item, Mapping):
            continue
        try:
            first = int(item.get("first_page"))
            last = int(item.get("last_page", first))
        except (TypeError, ValueError):
            continue
        first, last = max(first, low), min(last, high)
        if last < first:
            continue
        kind = str(item.get("kind") or "other")
        if kind not in KIND_LABELS:
            kind = guess_kind(text_of.get(first, ""))
        raw_date = normalise_text(item.get("date")) or ""
        value, _ = read_date(raw_date) if raw_date else (None, "")
        title = normalise_text(item.get("title")) or ""
        page_text = text_of.get(first, "")
        # A title or date the page does not bear is dropped, not shown.
        if title and not quote_appears(title, page_text):
            title = ""
        if raw_date and not quote_appears(raw_date, " ".join(text_of.get(n, "") for n in range(first, last + 1))):
            raw_date, value = "", None
        if not raw_date:
            raw_date, value = _page_date(page_text)
        clean.append(Segment(first, last, kind, title or _title_from(page_text), raw_date, value))

    clean.sort(key=lambda s: (s.first_page, s.last_page))
    merged: List[Segment] = []
    covered_to = low - 1
    for segment in clean:
        if segment.first_page <= covered_to:
            segment.first_page = covered_to + 1
            if segment.first_page > segment.last_page:
                continue
        if segment.first_page > covered_to + 1:
            merged.extend(_fill(covered_to + 1, segment.first_page - 1, pages))
        merged.append(segment)
        covered_to = segment.last_page
    if covered_to < high:
        if merged:
            notes.append(
                f"Pages {covered_to + 1}–{high} were not placed by the model and have been "
                f"split by their headings. Check them."
            )
        merged.extend(_fill(covered_to + 1, high, pages))
    merged = _join_continuations(merged, text_of)
    _mark_claim_letter(merged, pages)
    return merged, notes


#: Kinds a model gives a page it could not place, which on a page that does
#: not start a document usually means "more of the document before".
_LOOSE_KINDS = frozenset({"correspondence", "other"})


def _join_continuations(segments: List[Segment], text_of: Mapping[int, str]) -> List[Segment]:
    """Join a page that does not start a document onto the one before it.

    A model reading page by page splits a two-page letter in two when the
    second page has no heading of its own. A page with no letterhead, annex
    heading or page-one marker is a continuation; where it was labelled as the
    same kind, or as a loose kind, it is joined to what precedes it.
    """
    joined: List[Segment] = []
    for segment in segments:
        previous = joined[-1] if joined else None
        if (
            previous is not None
            and previous.last_page + 1 == segment.first_page
            and not starts_document(text_of.get(segment.first_page, ""))
            and (segment.kind == previous.kind or segment.kind in _LOOSE_KINDS)
        ):
            previous.last_page = segment.last_page
            continue
        joined.append(segment)
    return joined


def _fill(first: int, last: int, pages: Sequence[PageText]) -> List[Segment]:
    part = [p for p in pages if first <= p.page_number <= last]
    return heuristic_segments(part) if part else []


def segment_payload(segment: Segment) -> Dict[str, Any]:
    return {
        "first_page": segment.first_page,
        "last_page": segment.last_page,
        "pages": segment.pages,
        "kind": segment.kind,
        "kind_label": KIND_LABELS.get(segment.kind, segment.kind),
        "title": segment.title,
        "date_text": segment.date_text,
        "date": segment.date_value.isoformat() if segment.date_value else None,
        "element": KIND_TO_ELEMENT.get(segment.kind),
        "source": segment.source,
    }


def page_digest(pages: Sequence[PageText], *, chars: int = 350) -> str:
    """The start of each page, for a model to split the bundle from."""
    lines = []
    for page in pages:
        snippet = " ".join((page.text or "").split())[:chars] or "(no text on this page)"
        lines.append(f"PAGE {page.page_number}: {snippet}")
    return "\n".join(lines)
