"""Extraction quality assessment.

The purpose is not a dashboard number. It is to mark a document whose citations
deserve caution, so a badly-OCR'd scan is visibly less reliable rather than
silently trusted alongside a clean digital contract.

This matters more here than in a general document system. A citation is the
mechanism by which the AI's output is checkable, and a citation into garbled
text is worse than no citation: it looks like evidence. So the score feeds the
UI and the analysis prompts, not just an admin page.

Scoring is a weighted average of independent signals, each of which degrades
gracefully when its input is unavailable — a digital PDF has no OCR confidence,
and that absence must not be read as poor quality.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


class QualityBand(str, Enum):
    GOOD = "good"
    """Extraction is reliable. Citations can be trusted as read."""

    ACCEPTABLE = "acceptable"
    """Usable, with some loss. Citations are generally sound."""

    POOR = "poor"
    """Significant extraction loss. Citations should be verified against the
    original before being relied on."""

    UNUSABLE = "unusable"
    """Too little text recovered to support retrieval or citation."""


@dataclass(frozen=True)
class PageQuality:
    """Per-page inputs to the assessment."""

    page_number: int
    character_count: int
    is_content_page: bool = True
    ocr_confidence: float | None = None
    """None for digitally-extracted pages, which have no OCR confidence."""

    was_ocr: bool = False


@dataclass
class QualityFinding:
    code: str
    message: str
    severity: str  # "info" | "warning" | "error"


@dataclass
class QualityReport:
    score: float
    band: QualityBand
    findings: list[QualityFinding] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)
    page_count: int = 0
    empty_page_count: int = 0
    ocr_page_count: int = 0

    @property
    def is_usable(self) -> bool:
        return self.band is not QualityBand.UNUSABLE

    @property
    def needs_verification(self) -> bool:
        """True when citations into this document should be checked by hand."""
        return self.band in (QualityBand.POOR, QualityBand.UNUSABLE)

    def summary(self) -> str:
        return (
            f"{self.band.value} ({self.score:.2f}): {self.page_count} page(s), "
            f"{self.empty_page_count} empty, {self.ocr_page_count} OCR'd"
        )


#: Below this many characters a page is treated as having yielded nothing.
#: A page of a contract with fewer than 40 characters is a blank, a divider or
#: a failed extraction — not a page with very little text on it.
MIN_USEFUL_PAGE_CHARS = 40

#: Band thresholds.
BAND_GOOD = 0.85
BAND_ACCEPTABLE = 0.65
BAND_POOR = 0.35

#: Signal weights. Text recovery dominates because everything else is
#: downstream of it: perfect clause detection over text that was never
#: extracted is meaningless.
WEIGHTS = {
    "text_recovery": 0.45,
    "ocr_confidence": 0.20,
    "clause_coverage": 0.20,
    "text_integrity": 0.15,
}

_ALPHA_RE = re.compile(r"[^\W\d_]", re.UNICODE)
#: Runs of single characters separated by spaces — a classic OCR failure on
#: poor scans ("T h e C o n t r a c t o r").
_SPACED_LETTERS_RE = re.compile(r"(?:\b\w\s){6,}")

#: Characters that indicate a decoding or scanning failure: the Unicode
#: replacement character, and control codes other than tab/newline/carriage
#: return. Built programmatically rather than written as a regex literal so
#: that no control character appears in this source file.
_CORRUPTION_CHARS = frozenset(
    [chr(0xFFFD)]
    + [chr(code) for code in range(0x00, 0x09)]
    + [chr(0x0B), chr(0x0C)]
    + [chr(code) for code in range(0x0E, 0x20)]
)


def _corruption_count(text: str) -> int:
    return sum(1 for char in text if char in _CORRUPTION_CHARS)


def text_recovery_signal(pages: Sequence[PageQuality]) -> float:
    """Share of content pages that yielded usable text."""
    content = [p for p in pages if p.is_content_page]
    if not content:
        return 0.0
    useful = sum(1 for p in content if p.character_count >= MIN_USEFUL_PAGE_CHARS)
    return useful / len(content)


def ocr_confidence_signal(pages: Sequence[PageQuality]) -> float | None:
    """Mean OCR confidence over OCR'd pages, or None if none were OCR'd.

    Returning None rather than 1.0 matters: a digital document has no OCR
    confidence, and scoring that as perfect would let the signal inflate an
    otherwise mediocre extraction.
    """
    scored = [p.ocr_confidence for p in pages if p.was_ocr and p.ocr_confidence is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def clause_coverage_signal(
    detected_clauses: Iterable[str],
    expected_clauses: Iterable[str] = (),
) -> float | None:
    """Proportion of expected clauses that were detected.

    Returns None when no expectation is available — most documents are not
    standard forms and have no clause skeleton to compare against. Absence of
    an expectation is not evidence of poor extraction.
    """
    expected = {c.strip() for c in expected_clauses if c and c.strip()}
    if not expected:
        return None
    detected: set[str] = set()
    for clause in detected_clauses:
        clause = clause.strip()
        if not clause:
            continue
        detected.add(clause)
        detected.add(clause.split(".")[0])
    return len(expected & detected) / len(expected)


def text_integrity_signal(texts: Sequence[str]) -> float:
    """How much of the recovered text looks like language rather than noise.

    Three penalties, each targeting a distinct OCR failure mode: a low
    alphabetic ratio (page furniture, tables read as digit soup), spaced-out
    single letters, and replacement/control characters.

    Severity is measured as the **proportion of characters affected**, not the
    number of matches. The spaced-letter pattern is greedy, so a page that is
    entirely garbled produces one enormous match — counting matches would score
    that as a single minor blemish.
    """
    joined = "\n".join(t for t in texts if t)
    if not joined.strip():
        return 0.0

    length = len(joined)
    alpha = len(_ALPHA_RE.findall(joined))
    alpha_ratio = alpha / length
    # 0.55+ alphabetic is normal prose; below that the page is mostly
    # punctuation, digits or artefacts.
    score = min(1.0, alpha_ratio / 0.55)

    spaced_chars = sum(len(match) for match in _SPACED_LETTERS_RE.findall(joined))
    if spaced_chars:
        score *= max(0.0, 1.0 - spaced_chars / length)

    corrupt_ratio = _corruption_count(joined) / length
    if corrupt_ratio:
        # Corruption is weighted heavily: replacement characters mean bytes
        # were lost outright, not merely misread.
        score *= max(0.0, 1.0 - corrupt_ratio * 4)

    return max(0.0, min(1.0, score))


def band_for(score: float) -> QualityBand:
    if score >= BAND_GOOD:
        return QualityBand.GOOD
    if score >= BAND_ACCEPTABLE:
        return QualityBand.ACCEPTABLE
    if score >= BAND_POOR:
        return QualityBand.POOR
    return QualityBand.UNUSABLE


def assess_quality(
    pages: Sequence[PageQuality],
    *,
    texts: Sequence[str] = (),
    detected_clauses: Iterable[str] = (),
    expected_clauses: Iterable[str] = (),
) -> QualityReport:
    """Score an extraction.

    Args:
        pages: Per-page statistics.
        texts: Extracted page texts, for the integrity signal.
        detected_clauses: Clause numbers the detector found.
        expected_clauses: Clause numbers the document should contain, where a
            skeleton is available.

    Returns:
        A report with the score, band, contributing signals and findings.
        Signals that do not apply are excluded from the weighted average and
        the remaining weights are renormalised, so an inapplicable signal
        neither helps nor harms.
    """
    if not pages:
        return QualityReport(
            score=0.0,
            band=QualityBand.UNUSABLE,
            findings=[
                QualityFinding(
                    "no_pages", "No pages were extracted from the document.", "error"
                )
            ],
        )

    content_pages = [p for p in pages if p.is_content_page]
    empty = [p for p in content_pages if p.character_count < MIN_USEFUL_PAGE_CHARS]
    ocr_pages = [p for p in pages if p.was_ocr]

    signals: dict[str, float] = {"text_recovery": text_recovery_signal(pages)}

    ocr_signal = ocr_confidence_signal(pages)
    if ocr_signal is not None:
        signals["ocr_confidence"] = ocr_signal

    coverage = clause_coverage_signal(detected_clauses, expected_clauses)
    if coverage is not None:
        signals["clause_coverage"] = coverage

    if texts:
        signals["text_integrity"] = text_integrity_signal(texts)

    total_weight = sum(WEIGHTS[name] for name in signals)
    weighted = (
        sum(WEIGHTS[name] * value for name, value in signals.items()) / total_weight
        if total_weight
        else 0.0
    )

    # A weighted average lets a strong signal mask a fatal weakness. A scan
    # with 300 characters per page scores full marks for text recovery even
    # when every one of those characters is garbled — and the resulting "good"
    # rating is precisely the outcome this assessment exists to prevent, since
    # it means citations into unreadable text are presented as trustworthy.
    #
    # So the score is additionally capped by its weakest *critical* signal. A
    # document is only as reliable as its worst dimension.
    critical = [
        signals[name]
        for name in ("ocr_confidence", "text_integrity")
        if name in signals
    ]
    if critical:
        ceiling = 0.5 + 0.5 * min(critical)
        score = min(weighted, ceiling)
    else:
        score = weighted

    report = QualityReport(
        score=round(score, 3),
        band=band_for(score),
        signals={k: round(v, 3) for k, v in signals.items()},
        page_count=len(pages),
        empty_page_count=len(empty),
        ocr_page_count=len(ocr_pages),
    )

    if not content_pages:
        report.findings.append(
            QualityFinding(
                "no_content_pages",
                "Every page was classified as front or back matter. The document "
                "may be a contents volume, or page classification may have "
                "misfired.",
                "warning",
            )
        )

    if empty and content_pages:
        share = len(empty) / len(content_pages)
        if share >= 0.5:
            report.findings.append(
                QualityFinding(
                    "majority_pages_empty",
                    f"{len(empty)} of {len(content_pages)} content pages "
                    f"({share:.0%}) yielded no usable text. The document is "
                    f"probably a scan that OCR could not read.",
                    "error",
                )
            )
        elif share >= 0.15:
            report.findings.append(
                QualityFinding(
                    "pages_empty",
                    f"{len(empty)} of {len(content_pages)} content pages "
                    f"({share:.0%}) yielded no usable text.",
                    "warning",
                )
            )

    if ocr_signal is not None and ocr_signal < 0.70:
        report.findings.append(
            QualityFinding(
                "low_ocr_confidence",
                f"Mean OCR confidence is {ocr_signal:.0%}. Citations into this "
                f"document should be verified against the original.",
                "warning",
            )
        )

    if coverage is not None and coverage < 0.60:
        report.findings.append(
            QualityFinding(
                "low_clause_coverage",
                f"Only {coverage:.0%} of expected clauses were detected. The "
                f"clause structure is incomplete, so clause-scoped retrieval "
                f"will miss provisions.",
                "warning",
            )
        )

    integrity = signals.get("text_integrity")
    if integrity is not None and integrity < 0.60:
        report.findings.append(
            QualityFinding(
                "degraded_text",
                "Recovered text shows signs of OCR degradation (spaced letters, "
                "replacement characters, or a low proportion of alphabetic "
                "content).",
                "warning",
            )
        )

    if report.band is QualityBand.UNUSABLE:
        report.findings.append(
            QualityFinding(
                "unusable_extraction",
                "Too little usable text was recovered for retrieval or citation. "
                "Re-scan the source at a higher resolution, or supply a "
                "digital original.",
                "error",
            )
        )

    return report
