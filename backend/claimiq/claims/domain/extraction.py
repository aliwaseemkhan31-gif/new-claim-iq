"""Draft a claim from the text of a claim document.

A claims consultant receives a claim as paper: a bound submission, a letter, or
a photograph of one taken on a phone. Retyping it into a form is the least
valuable part of the job and the easiest place to introduce a transcription
error. This reads what the document says and proposes a claim from it.

What it is not:

**It is not a claim.** Nothing here is saved. The output is a draft for a
person to correct and accept, which is the only point at which a claim exists.
Extraction is the one place a model touches the record directly, so the review
is the control, not a formality.

**It does not decide anything.** No assessment, no entitlement, no view on
merit. It reads fields off a page.

**It does not fill in gaps.** A field the document does not state comes back
absent, never inferred from the others. A plausible date invented to complete a
form is worse than a blank one, because a blank is obviously missing and an
invented date is not. Every value carries the words it was read from, so a
reviewer can check it against the page rather than trusting it.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: Fields a claim document can be read for. Order is the order a reviewer reads
#: them in, which is the order the form shows.
EXTRACTABLE_FIELDS: Tuple[str, ...] = (
    "title",
    "reference",
    "claim_type",
    "contractual_basis",
    "claimant",
    "respondent",
    "event_date",
    "awareness_date",
    "notice_date",
    "submission_date",
    "amount_claimed",
    "currency",
    "time_claimed_days",
    "description",
)

FIELD_LABELS: Mapping[str, str] = {
    "title": "Title",
    "reference": "Reference",
    "claim_type": "Type of claim",
    "contractual_basis": "Clauses relied on",
    "claimant": "Claimant",
    "respondent": "Respondent",
    "event_date": "Event date",
    "awareness_date": "Awareness date",
    "notice_date": "Notice date",
    "submission_date": "Submission date",
    "amount_claimed": "Amount claimed",
    "currency": "Currency",
    "time_claimed_days": "Time claimed (days)",
    "description": "The claimant's account",
}

#: Claim type codes, mirroring ClaimType on the model. A value outside this set
#: is dropped with a note rather than coerced to "other": guessing the type
#: changes which elements the claim must establish.
CLAIM_TYPES: Tuple[str, ...] = (
    "eot",
    "variation",
    "cost",
    "delay",
    "disruption",
    "acceleration",
    "payment",
    "compensation_event",
    "other",
)

#: Currency codes seen on the contracts this is used with, plus the majors.
#: Used only to recognise a symbol or name; an unrecognised code is kept as
#: given, upper-cased, for the reviewer to correct.
#: ISO 4217 alphabetic codes. A three-letter string is accepted as a currency
#: only when it is one of these — see :func:`normalise_currency` for why.
ISO_4217_CODES: frozenset = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND
    BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU
    CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP
    GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES
    KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD
    MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR
    PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD
    SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS
    UAH UGX USD UYI UYU UYW UZS VED VES VND VUV WST XAF XCD XCG XDR XOF XPF
    YER ZAR ZMW ZWG
    """.split()
)

_CURRENCY_WORDS: Mapping[str, str] = {
    "rs": "PKR",
    "rs.": "PKR",
    "rupees": "PKR",
    "pkr": "PKR",
    "pak rupees": "PKR",
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "dollars": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}

#: A clause number. The trailing boundary is a negative lookahead for a
#: digit rather than \b, because OCR runs the next word onto the number:
#: "CLAUSE 53.3OF CONTRACT" gave up "53" under a word-boundary anchor.
_CLAUSE_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){0,3}(?!\d)")
#: A clause number introduced by the word for one. Preferred over any
#: number on the line: "CLAIM-1 (UNDER CLAUSE 53.3OF CONTRACT)" holds two
#: numbers and only one of them is a clause.
_CLAUSE_KEYWORD_RE = re.compile(
    r"(?:sub-?clause|clauses|clause|cl\.?)\s*(?:nos?\.?)?\s*(\d{1,3}(?:\.\d{1,3}){0,3})(?!\d)",
    re.IGNORECASE,
)
#: One money amount as written. Spaces are deliberately not allowed inside
#: it: a table row reaches this as "28,529,843 32,972,199 61,502,043", and a
#: pattern that spanned the gaps concatenated three columns into a single
#: twenty-four digit number that appears nowhere on the page.
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")

#: A multiplier word written against the figure, on either side: "9.828 Mn",
#: "9.828 (Mn)", "Amount in Mn 9.828". Both forms appear as column headings
#: and as inline units on the cost summaries this reads.
_MULTIPLIER_WORD = r"(?:mn|million|bn|billion)"
_MULTIPLIER_AFTER_RE = re.compile(
    r"^[ \t]*\(?(" + _MULTIPLIER_WORD + r")\b", re.IGNORECASE
)
_MULTIPLIER_BEFORE_RE = re.compile(
    r"\b(" + _MULTIPLIER_WORD + r")\)?[ \t]*$", re.IGNORECASE
)

#: How a date may be written on a claim document.
#:
#: Day-first before month-first throughout: these are FIDIC contracts on
#: projects outside the United States, and reading 03/04/2024 as 3 April is
#: right far more often than 4 March. A two-digit year is accepted because
#: scanned correspondence and fax headers carry them, and Python maps 69-99 to
#: the 1900s and 00-68 to the 2000s, which is correct for construction records.
_DATE_FORMATS: Tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%d-%b-%Y",
    "%d %B, %Y",
    # Added after a real submission: two-digit years, dotted and spaced
    # separators, an ISO timestamp, and the no-space form a fax header prints.
    "%d/%m/%y",
    "%d-%m-%y",
    "%d.%m.%y",
    "%d %b %y",
    "%d %B %y",
    "%d%b%Y",
    "%d%b%y",
    "%Y/%m/%d",
    "%Y.%m.%d",
    # Deliberately no month-only format. "March 2024" would parse to the 1st,
    # and a day this parser invented could move a notice period across its
    # deadline. A date that cannot be read is reported as unreadable.
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


@dataclass(frozen=True)
class DraftField:
    """One field read off the document.

    Attributes:
        name: Field name, from :data:`EXTRACTABLE_FIELDS`.
        value: The normalised value, or None when the document does not state
            it. None is a first-class answer.
        quote: The words the value was read from, verbatim from the document.
        quote_verified: Whether the value can be confirmed against the
            document: a quotation was given and those words are on the page.
            False where the quotation is absent or is not in the document, and
            marks a field to check against the page. The value may still be
            right; it is unconfirmed, not wrong.
        note: Why a value was dropped or adjusted, where that happened.
    """

    name: str
    value: Any = None
    quote: str = ""
    quote_verified: bool = True
    note: str = ""

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.name, self.name)

    @property
    def found(self) -> bool:
        return self.value not in (None, "", (), [])


@dataclass
class ClaimDraft:
    """A proposed claim, for a person to correct and accept.

    Never saved as it stands. :meth:`as_form` renders it into the shape the
    claim form takes, which is where a reviewer meets it.
    """

    fields: List[DraftField] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    """What the reader should know: an ambiguity, several candidate amounts, a
    document that looks like something other than a claim."""

    def get(self, name: str) -> Optional[DraftField]:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def value(self, name: str) -> Any:
        found = self.get(name)
        return found.value if found is not None else None

    @property
    def found_fields(self) -> List[DraftField]:
        return [f for f in self.fields if f.found]

    @property
    def missing_fields(self) -> List[DraftField]:
        return [f for f in self.fields if not f.found]

    @property
    def unverified_fields(self) -> List[DraftField]:
        return [f for f in self.fields if f.found and not f.quote_verified]

    def summary(self) -> str:
        return "{} of {} field(s) read from the document; {} could not be confirmed against it.".format(
            len(self.found_fields), len(self.fields), len(self.unverified_fields)
        )

    def as_form(self) -> Dict[str, Any]:
        """The draft in the shape of the claim form."""
        return {f.name: f.value for f in self.fields}


# ---------------------------------------------------------------------------
# Normalisation
#
# The model returns what the page says. These turn that into what the form
# takes, and refuse rather than guess where they cannot.
# ---------------------------------------------------------------------------


#: What a model writes when it means "the document does not say".
#:
#: Asked for JSON null, qwen2.5:3b returns the four-character string "null"
#: often enough that taking it literally filled a claim with a claimant called
#: "null" and a currency of "NUL".
_ABSENT_TOKENS = frozenset(
    {
        "null",
        "none",
        "nil",
        "n/a",
        "na",
        "-",
        "--",
        "not stated",
        "not recorded",
        "not specified",
        "not provided",
        "not available",
        "unknown",
        "unspecified",
    }
)


def normalise_text(value: Any) -> Optional[str]:
    """Text as the document gives it, or None where it gives nothing."""
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    if not text or text.lower().strip(".") in _ABSENT_TOKENS:
        return None
    return text


def normalise_date(value: Any) -> Tuple[Optional[date], str]:
    """Parse a date as written on a claim document.

    Returns:
        ``(date, note)``. The note is non-empty when a value was present and
        could not be read, which the reviewer needs to see — a silently dropped
        date reads as a document that did not state one.
    """
    text = normalise_text(value)
    if not text:
        return None, ""

    cleaned = text.replace(",", ", ").replace("  ", " ").strip()
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cleaned, flags=re.IGNORECASE)
    cleaned = _WS_RE.sub(" ", cleaned)

    import datetime as _dt

    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(cleaned, fmt).date(), ""
        except ValueError:
            continue
    return None, "{!r} could not be read as a date.".format(text)


def _attached_multiplier(text: str, start: int, end: int) -> Decimal:
    """The multiplier word written against the figure at ``text[start:end]``.

    The word has to touch the figure. Searching the whole string for
    "million" read the figure-then-words-in-brackets form that nearly every
    claim letter uses — "Rs. 84,565,309 (Rupees Eighty Four Million Five
    Hundred Sixty Five Thousand Three Hundred Nine Only)" — as eighty-four
    trillion, and returned it with an empty note, so nothing marked it for
    the reviewer. The words in brackets restate the figure; they do not
    scale it.
    """
    match = (
        _MULTIPLIER_AFTER_RE.match(text[end:])
        or _MULTIPLIER_BEFORE_RE.search(text[:start])
    )
    if not match:
        return Decimal(1)
    word = match.group(1).lower()
    return Decimal(1_000_000_000) if word in ("bn", "billion") else Decimal(1_000_000)


def normalise_amount(value: Any) -> Tuple[Optional[Decimal], str]:
    """Parse a money amount, which a claim document writes many ways.

    "Rs. 84,565,309", "84,565,309.00", "9.828 Mn" all appear on the documents
    this reads. A multiplier word is honoured only when it is unambiguous.
    """
    if value is None:
        return None, ""
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return None, "{!r} could not be read as an amount.".format(value)
        return (amount, "") if amount > 0 else (None, "A claimed amount of {} is not a claim.".format(amount))

    text = normalise_text(value)
    if not text:
        return None, ""

    # Match the number itself rather than stripping non-digits: a currency
    # prefix carries punctuation, and stripping all but digits and dots
    # turns "Rs. 84,565,309" into 0.84565309.
    matches = list(_AMOUNT_RE.finditer(text))
    if not matches:
        return None, "{!r} could not be read as an amount.".format(text)
    distinct = []
    spans = {}
    for match in matches:
        candidate = match.group(0)
        if candidate not in distinct:
            distinct.append(candidate)
            spans[candidate] = match.span()
    if len(distinct) > 1:
        # A row of a summary table, not one amount. Which of them is
        # claimed is the reviewer's to say; picking one here would put a
        # figure on the claim that nobody chose.
        return None, (
            "Several amounts appear in {!r}: {}. Which is claimed is not clear from this text.".format(text, ", ".join(distinct))
        )
    multiplier = _attached_multiplier(text, *spans[distinct[0]])
    digits = re.sub(r"[,\s]", "", distinct[0])
    try:
        amount = Decimal(digits) * multiplier
    except InvalidOperation:
        return None, "{!r} could not be read as an amount.".format(text)
    if amount <= 0:
        return None, "A claimed amount of {} is not a claim.".format(amount)
    return amount, ""


def normalise_currency(value: Any) -> Optional[str]:
    """Read a currency, or report none.

    Only a recognised word or a real ISO 4217 code is accepted. Taking the
    first three letters of whatever was supplied fabricates currencies: a
    claim summary reading "Total Amount (Mn)" yielded "MN", which is not a
    currency and which then travelled into the claim record beside a figure,
    making the figure look checked when it was not. A currency this cannot
    read is better reported as absent.
    """
    text = normalise_text(value)
    if not text:
        return None
    lowered = text.lower().strip()
    if lowered in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[lowered]
    letters = re.sub(r"[^A-Za-z]", "", text).upper()
    if letters in ISO_4217_CODES:
        return letters
    return None


def normalise_days(value: Any) -> Tuple[Optional[int], str]:
    if value is None:
        return None, ""
    text = normalise_text(value)
    if not text:
        return None, ""
    match = re.search(r"-?\d+", text.replace(",", ""))
    if match is None:
        return None, "{!r} could not be read as a number of days.".format(text)
    days = int(match.group())
    if days <= 0:
        return None, "A claim for {} day(s) is not a claim for time.".format(days)
    return days, ""


def normalise_clauses(value: Any) -> Tuple[Tuple[str, ...], str]:
    """Pull clause numbers out of whatever the model returned.

    Accepts a list or a string such as "Clauses 53.1 and 53.3", because small
    models return both against the same prompt.
    """
    if value is None:
        return (), ""
    if isinstance(value, (list, tuple)):
        parts = [normalise_text(v) or "" for v in value]
        text = " ".join(parts)
    else:
        text = normalise_text(value) or ""
    if not text:
        return (), ""

    # Where the text says which numbers are clauses, believe it. Falling
    # back to every number on the line only when nothing does.
    matches = _CLAUSE_KEYWORD_RE.findall(text)
    if matches:
        # A list carries the keyword once: "Clauses 44.1 and 53.1". Numbers
        # with a part after the dot are clause numbers wherever they sit; a
        # bare integer is as likely to be the claim number, so it is taken
        # only where the word "clause" introduces it.
        matches = matches + [
            m.group() for m in _CLAUSE_RE.finditer(text) if "." in m.group()
        ]
    else:
        matches = [m.group() for m in _CLAUSE_RE.finditer(text)]

    found: List[str] = []
    for number in matches:
        if number not in found:
            found.append(number)
    if not found:
        return (), "No clause number could be read from {!r}.".format(text)
    return tuple(found), ""


#: The label a document uses for a claim type, against the code for it. A
#: model returns the label as often as the code, and they name the same thing.
_CLAIM_TYPE_LABELS: Mapping[str, str] = {
    "extension_of_time": "eot",
    "extension_of_time_claim": "eot",
    "eot_claim": "eot",
    "time_extension": "eot",
    "additional_cost": "cost",
    "cost_claim": "cost",
    "additional_payment": "payment",
    "prolongation": "delay",
    "prolongation_cost": "cost",
    "variation_claim": "variation",
    "disruption_claim": "disruption",
    "acceleration_claim": "acceleration",
    "payment_claim": "payment",
    "compensation_event_claim": "compensation_event",
}


def normalise_claim_type(value: Any) -> Tuple[Optional[str], str]:
    text = normalise_text(value)
    if not text:
        return None, ""
    code = text.strip().lower().replace(" ", "_").replace("-", "_")
    if code in CLAIM_TYPES:
        return code, ""
    if code in _CLAIM_TYPE_LABELS:
        return _CLAIM_TYPE_LABELS[code], ""
    # Deliberately not coerced to "other": the type decides which elements the
    # claim must establish, so a wrong one is worse than none.
    return None, "{!r} is not a claim type this system recognises.".format(text)


# ---------------------------------------------------------------------------
# Building a draft
# ---------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Normalised form for checking a quote against the document."""
    folded = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub("", folded).lower()


def quote_appears(quote: str, source_text: str) -> bool:
    """Whether a quote occurs in the document text.

    Spacing is ignored. The text comes from OCR of a photograph, which moves
    spaces without losing characters, and a reviewer checking a field against
    the page does not care where the spaces fell.
    """
    cleaned = (quote or "").strip().strip("\"'“”")
    if len(cleaned) < 8:
        # Too short to prove anything either way; not reported as unverified.
        return True
    return _fold(cleaned) in _fold(source_text or "")


_NORMALISERS = {
    "title": lambda v: (normalise_text(v), ""),
    "reference": lambda v: (normalise_text(v), ""),
    "claimant": lambda v: (normalise_text(v), ""),
    "respondent": lambda v: (normalise_text(v), ""),
    "description": lambda v: (normalise_text(v), ""),
    "currency": lambda v: (normalise_currency(v), ""),
    "claim_type": normalise_claim_type,
    "contractual_basis": normalise_clauses,
    "event_date": normalise_date,
    "awareness_date": normalise_date,
    "notice_date": normalise_date,
    "submission_date": normalise_date,
    "amount_claimed": normalise_amount,
    "time_claimed_days": normalise_days,
}


def build_draft(payload: Mapping[str, Any], *, source_text: str = "") -> ClaimDraft:
    """Turn a model response into a draft, normalising and checking as it goes.

    Args:
        payload: The model's structured response. A field may be absent, null,
            a bare value, or an object with ``value`` and ``quote``; small
            models produce all four against the same prompt, and rejecting the
            response over its shape would discard a usable read.
        source_text: The document text the model was given, used to check each
            quote. Empty skips the check rather than failing every field.

    Returns:
        A draft with one entry per extractable field, missing ones included.
    """
    notes: List[str] = []
    for raw_note in payload.get("notes") or ():
        note = normalise_text(raw_note)
        if note:
            notes.append(note)

    # Quotes arrive either beside each value or gathered into one object.
    # Both shapes are produced by the models this runs against.
    quotes = payload.get("quotes")
    if not isinstance(quotes, Mapping):
        quotes = {}

    fields: List[DraftField] = []
    for name in EXTRACTABLE_FIELDS:
        raw = payload.get(name)
        quote = normalise_text(quotes.get(name)) or ""
        if isinstance(raw, Mapping):
            quote = normalise_text(raw.get("quote")) or quote
            raw = raw.get("value")

        normaliser = _NORMALISERS[name]
        value, note = normaliser(raw)

        verified = True
        if value not in (None, "", ()):
            if not quote:
                # No quotation, nothing to check it against. Reported as
                # unconfirmed rather than trusted: a model that skips the
                # quote is the one whose value most needs checking.
                verified = False
            elif source_text:
                verified = quote_appears(quote, source_text)

        fields.append(
            DraftField(
                name=name,
                value=value,
                quote=quote,
                quote_verified=verified,
                note=note,
            )
        )

    return ClaimDraft(fields=fields, notes=notes)


def draft_payload(draft: ClaimDraft) -> Dict[str, Any]:
    """Render a draft for the API, values in the form's own types."""
    rendered: List[Dict[str, Any]] = []
    for item in draft.fields:
        value = item.value
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        rendered.append(
            {
                "name": item.name,
                "label": item.label,
                "value": value,
                "found": item.found,
                "quote": item.quote,
                "quote_verified": item.quote_verified,
                "note": item.note,
            }
        )
    return {
        "summary": draft.summary(),
        "fields": rendered,
        "notes": list(draft.notes),
        "found": len(draft.found_fields),
        "missing": len(draft.missing_fields),
        "unverified": len(draft.unverified_fields),
    }
