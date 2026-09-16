"""Tests for extraction layout helpers."""
from __future__ import annotations

import pytest

from claimiq.ingestion.domain.layout import (
    FileKind,
    assemble_ocr_lines,
    detect_file_kind,
    paginate_text,
    render_table_text,
)

# ---------------------------------------------------------------------------
# File kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"%PDF-1.7\n", FileKind.PDF),
        (b"\x89PNG\r\n\x1a\n....", FileKind.IMAGE),
        (b"\xff\xd8\xff\xe0JFIF", FileKind.IMAGE),
        (b"II*\x00\x08\x00", FileKind.IMAGE),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00", FileKind.LEGACY_OFFICE),
    ],
)
def test_kind_comes_from_the_header(header, expected):
    assert detect_file_kind("anything.bin", "application/octet-stream", header) is expected


def test_renamed_file_is_judged_by_content_not_name():
    """A Word 97 file renamed .pdf must not reach the PDF parser."""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert detect_file_kind("contract.pdf", "application/pdf", ole) is FileKind.LEGACY_OFFICE


def test_zip_containers_are_distinguished_by_members():
    assert detect_file_kind("a.docx", "", b"PK\x03\x04", ["word/document.xml"]) is FileKind.DOCX
    assert detect_file_kind("a.xlsx", "", b"PK\x03\x04", ["xl/workbook.xml"]) is FileKind.XLSX
    assert detect_file_kind("a.pptx", "", b"PK\x03\x04", ["ppt/slides/s1.xml"]) is FileKind.UNSUPPORTED


def test_plain_text_needs_a_textual_declaration_and_no_nul_bytes():
    assert detect_file_kind("notes.txt", "text/plain", b"Minutes of meeting") is FileKind.TEXT
    assert detect_file_kind("mail.eml", "message/rfc822", b"From: a") is FileKind.TEXT
    assert detect_file_kind("notes.txt", "text/plain", b"bin\x00ary") is FileKind.UNSUPPORTED
    assert detect_file_kind("blob", "application/octet-stream", b"hello") is FileKind.UNSUPPORTED


def test_only_pdf_and_image_have_page_images():
    assert FileKind.PDF.has_page_images and FileKind.IMAGE.has_page_images
    assert not FileKind.DOCX.has_page_images and not FileKind.TEXT.has_page_images


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_short_text_is_one_page():
    assert paginate_text("One.\n\nTwo.") == ["One.\n\nTwo."]


def test_empty_text_has_no_pages():
    assert paginate_text("") == []
    assert paginate_text("\n\n  \n") == []


def test_pages_break_between_paragraphs_not_inside_them():
    paragraphs = [f"Paragraph {i} " + "x" * 280 for i in range(6)]
    pages = paginate_text("\n\n".join(paragraphs), max_chars=700)
    assert len(pages) == 3
    assert all(len(p) <= 700 for p in pages)
    assert "".join(pages).count("Paragraph") == 6
    for page in pages:
        for part in page.split("\n\n"):
            assert part.startswith("Paragraph")


def test_overlong_paragraph_splits_at_a_sentence_boundary():
    sentence = "The Contractor shall give notice within twenty eight days. "
    pages = paginate_text(sentence * 20, max_chars=400)
    assert len(pages) > 1
    assert all(len(p) <= 400 for p in pages)
    assert pages[0].endswith(".")


def test_unreasonably_small_page_size_is_refused():
    with pytest.raises(ValueError):
        paginate_text("text", max_chars=10)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_keeps_empty_cells_in_position():
    rendered = render_table_text([["Item", "Qty", "Rate"], ["Excavation", None, "814.92"]])
    assert rendered == "Item | Qty | Rate\nExcavation |  | 814.92"


def test_table_drops_empty_rows_and_collapses_whitespace():
    rendered = render_table_text([[None, ""], ["Asphaltic\nBase  Course", 338.69]])
    assert rendered == "Asphaltic Base Course | 338.69"


# ---------------------------------------------------------------------------
# OCR line assembly
# ---------------------------------------------------------------------------


def box(x, y, w=40, h=12):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_boxes_read_top_to_bottom_then_left_to_right():
    boxes = [
        (box(200, 50), "Contractor", 0.9),
        (box(10, 10), "Sub-Clause", 0.95),
        (box(10, 52), "the", 0.97),
        (box(120, 11), "20.2.1", 0.99),
    ]
    text, confidence = assemble_ocr_lines(boxes)
    assert text == "Sub-Clause 20.2.1\nthe Contractor"
    assert confidence == pytest.approx((0.9 + 0.95 + 0.97 + 0.99) / 4)


def test_slightly_skewed_boxes_stay_on_one_line():
    text, _ = assemble_ocr_lines([(box(10, 100), "Notice", 0.9), (box(80, 105), "given", 0.9)])
    assert text == "Notice given"


def test_empty_detections_yield_no_text_and_no_confidence():
    assert assemble_ocr_lines([]) == ("", None)
    assert assemble_ocr_lines([(box(0, 0), "   ", 0.5)]) == ("", None)
