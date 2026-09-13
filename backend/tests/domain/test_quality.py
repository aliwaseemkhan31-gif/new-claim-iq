"""Tests for extraction quality assessment.

The purpose of the score is to mark a document whose citations deserve caution.
A citation into garbled text is worse than no citation, because it looks like
evidence.
"""
from __future__ import annotations

import pytest

from claimiq.ingestion.domain.quality import (
    MIN_USEFUL_PAGE_CHARS,
    PageQuality,
    QualityBand,
    assess_quality,
    band_for,
    clause_coverage_signal,
    ocr_confidence_signal,
    text_integrity_signal,
    text_recovery_signal,
)

CLEAN_TEXT = (
    "The claiming Party shall give a Notice to the Engineer describing the event "
    "or circumstance giving rise to the Claim, as soon as practicable and no "
    "later than 28 days after becoming aware of it."
)


def page(
    number: int = 1,
    *,
    chars: int = 800,
    content: bool = True,
    confidence: float | None = None,
    ocr: bool = False,
) -> PageQuality:
    return PageQuality(
        page_number=number,
        character_count=chars,
        is_content_page=content,
        ocr_confidence=confidence,
        was_ocr=ocr,
    )


# ---------------------------------------------------------------------------
# Individual signals
# ---------------------------------------------------------------------------


def test_text_recovery_counts_only_content_pages() -> None:
    pages = [page(1), page(2), page(3, chars=0, content=False)]
    assert text_recovery_signal(pages) == 1.0


def test_text_recovery_penalises_empty_pages() -> None:
    assert text_recovery_signal([page(1), page(2, chars=0)]) == 0.5


def test_near_empty_page_counts_as_empty() -> None:
    assert text_recovery_signal([page(1, chars=MIN_USEFUL_PAGE_CHARS - 1)]) == 0.0


def test_ocr_confidence_is_none_for_a_digital_document() -> None:
    """None, not 1.0 — a perfect score would inflate a mediocre extraction."""
    assert ocr_confidence_signal([page(1), page(2)]) is None


def test_ocr_confidence_averages_ocr_pages_only() -> None:
    pages = [page(1, confidence=0.9, ocr=True), page(2, confidence=0.7, ocr=True), page(3)]
    assert ocr_confidence_signal(pages) == pytest.approx(0.8)


def test_clause_coverage_is_none_without_an_expectation() -> None:
    """Most documents are not standard forms; absence of a skeleton is not a defect."""
    assert clause_coverage_signal(["1.1", "20.2"], []) is None


def test_clause_coverage_measures_against_the_skeleton() -> None:
    assert clause_coverage_signal(["1", "20"], ["1", "20", "21", "22"]) == 0.5


def test_subclause_satisfies_its_parent_in_coverage() -> None:
    assert clause_coverage_signal(["20.2.1"], ["20"]) == 1.0


def test_text_integrity_accepts_normal_prose() -> None:
    assert text_integrity_signal([CLEAN_TEXT]) > 0.85


def test_text_integrity_penalises_spaced_out_letters() -> None:
    """A classic OCR failure on poor scans."""
    garbled = "T h e C o n t r a c t o r s h a l l g i v e a N o t i c e t o t h e"
    assert text_integrity_signal([garbled]) < text_integrity_signal([CLEAN_TEXT])


def test_text_integrity_penalises_replacement_characters() -> None:
    corrupt = CLEAN_TEXT + "�" * 40
    assert text_integrity_signal([corrupt]) < text_integrity_signal([CLEAN_TEXT])


def test_text_integrity_penalises_digit_soup() -> None:
    """A table read as numbers with no words recovered."""
    assert text_integrity_signal(["12 34 56 78 90 11 22 33 44 55 66 77 88 99"]) < 0.6


def test_text_integrity_of_nothing_is_zero() -> None:
    assert text_integrity_signal([]) == 0.0
    assert text_integrity_signal(["   "]) == 0.0


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, QualityBand.GOOD),
        (0.85, QualityBand.GOOD),
        (0.84, QualityBand.ACCEPTABLE),
        (0.65, QualityBand.ACCEPTABLE),
        (0.64, QualityBand.POOR),
        (0.35, QualityBand.POOR),
        (0.34, QualityBand.UNUSABLE),
        (0.0, QualityBand.UNUSABLE),
    ],
)
def test_band_thresholds(score: float, expected: QualityBand) -> None:
    assert band_for(score) is expected


# ---------------------------------------------------------------------------
# Full assessment
# ---------------------------------------------------------------------------


def test_clean_digital_document_scores_well() -> None:
    pages = [page(i) for i in range(1, 21)]
    report = assess_quality(pages, texts=[CLEAN_TEXT] * 20)
    assert report.band is QualityBand.GOOD
    assert report.needs_verification is False
    assert report.findings == []


def test_failed_scan_is_unusable_and_says_why() -> None:
    pages = [page(i, chars=0, ocr=True, confidence=0.2) for i in range(1, 11)]
    report = assess_quality(pages, texts=[""] * 10)

    assert report.band is QualityBand.UNUSABLE
    assert report.is_usable is False
    assert report.needs_verification is True
    codes = {f.code for f in report.findings}
    assert "majority_pages_empty" in codes
    assert "unusable_extraction" in codes


def test_poor_scan_flags_citations_for_verification() -> None:
    pages = [page(i, chars=300, ocr=True, confidence=0.55) for i in range(1, 11)]
    report = assess_quality(pages, texts=["T h e C o n t r a c t o r s h a l l"] * 10)
    assert report.needs_verification is True
    assert any(f.code == "low_ocr_confidence" for f in report.findings)


def test_full_text_recovery_cannot_mask_garbled_content() -> None:
    """The failure mode the weakest-link ceiling exists to prevent.

    Every page has plenty of characters, so text recovery scores 1.0 — but the
    characters are garbage. A plain weighted average rated this GOOD, which
    would present citations into unreadable text as trustworthy.
    """
    pages = [page(i, chars=300, ocr=True, confidence=0.55) for i in range(1, 11)]
    report = assess_quality(pages, texts=["T h e C o n t r a c t o r s h a l l"] * 10)

    assert report.signals["text_recovery"] == 1.0
    assert report.band is QualityBand.POOR
    assert report.needs_verification is True


def test_score_is_capped_by_the_weakest_critical_signal() -> None:
    strong = [page(i, chars=900, ocr=True, confidence=0.30) for i in range(1, 11)]
    report = assess_quality(strong, texts=[CLEAN_TEXT] * 10)
    # ceiling = 0.5 + 0.5 * 0.30 = 0.65
    assert report.score <= 0.65


def test_ceiling_does_not_penalise_a_clean_document() -> None:
    pages = [page(i) for i in range(1, 11)]
    report = assess_quality(pages, texts=[CLEAN_TEXT] * 10)
    assert report.band is QualityBand.GOOD


def test_low_clause_coverage_is_reported() -> None:
    pages = [page(i) for i in range(1, 11)]
    report = assess_quality(
        pages,
        texts=[CLEAN_TEXT] * 10,
        detected_clauses=["1"],
        expected_clauses=["1", "2", "3", "20", "21"],
    )
    assert any(f.code == "low_clause_coverage" for f in report.findings)


def test_inapplicable_signals_are_excluded_from_the_average() -> None:
    """A digital document must not be penalised for having no OCR confidence."""
    pages = [page(i) for i in range(1, 11)]
    report = assess_quality(pages, texts=[CLEAN_TEXT] * 10)
    assert "ocr_confidence" not in report.signals
    assert "clause_coverage" not in report.signals
    assert report.band is QualityBand.GOOD


def test_signals_are_reported_for_inspection() -> None:
    pages = [page(i, ocr=True, confidence=0.9) for i in range(1, 6)]
    report = assess_quality(
        pages, texts=[CLEAN_TEXT] * 5, detected_clauses=["1"], expected_clauses=["1"]
    )
    assert set(report.signals) == {
        "text_recovery",
        "ocr_confidence",
        "clause_coverage",
        "text_integrity",
    }


def test_no_pages_is_unusable() -> None:
    report = assess_quality([])
    assert report.band is QualityBand.UNUSABLE
    assert report.findings[0].code == "no_pages"


def test_all_front_matter_is_flagged() -> None:
    pages = [page(i, content=False) for i in range(1, 6)]
    report = assess_quality(pages, texts=[CLEAN_TEXT] * 5)
    assert any(f.code == "no_content_pages" for f in report.findings)


def test_partial_empty_pages_warn_without_failing() -> None:
    pages = [page(i) for i in range(1, 9)] + [page(9, chars=0), page(10, chars=0)]
    report = assess_quality(pages, texts=[CLEAN_TEXT] * 8)
    codes = {f.code for f in report.findings}
    assert "pages_empty" in codes
    assert "majority_pages_empty" not in codes


def test_counts_are_reported() -> None:
    pages = [page(1), page(2, chars=0), page(3, ocr=True, confidence=0.9)]
    report = assess_quality(pages, texts=[CLEAN_TEXT])
    assert report.page_count == 3
    assert report.empty_page_count == 1
    assert report.ocr_page_count == 1


def test_summary_is_human_readable() -> None:
    report = assess_quality([page(1)], texts=[CLEAN_TEXT])
    assert "page(s)" in report.summary()
    assert report.band.value in report.summary()
