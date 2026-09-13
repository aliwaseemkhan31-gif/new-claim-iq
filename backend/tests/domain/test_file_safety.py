"""Tests for upload safety validation.

Threat model: documents arrive from opposing parties in commercial disputes.
Every check must hold before a parsing library sees the bytes.
"""
from __future__ import annotations

import pytest

from claimiq.core.domain.errors import (
    FileSafetyError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from claimiq.ingestion.domain.file_safety import (
    RiskLevel,
    detect_content_type,
    detect_dangerous_content,
    enforce_safety,
    inspect_pdf_structure,
    sanitise_filename,
    validate_upload,
)

PDF_HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>"
ALLOWED = frozenset({"application/pdf", "image/png", "text/plain"})
MAX_BYTES = 10 * 1024 * 1024


def check(
    *,
    filename: str = "contract.pdf",
    declared: str = "application/pdf",
    size: int = 1024,
    header: bytes = PDF_HEADER,
):
    return validate_upload(
        filename=filename,
        declared_content_type=declared,
        size_bytes=size,
        header=header,
        allowed_types=ALLOWED,
        max_bytes=MAX_BYTES,
    )


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("../../../windows/system32/config", "config"),
        ("C:\\Windows\\system32\\evil.pdf", "evil.pdf"),
        ("/absolute/path/doc.pdf", "doc.pdf"),
        ("normal.pdf", "normal.pdf"),
        ("with spaces.pdf", "with spaces.pdf"),
    ],
)
def test_path_traversal_is_stripped(raw: str, expected: str) -> None:
    assert sanitise_filename(raw) == expected


def test_windows_path_separators_are_handled() -> None:
    """An upload from a Windows client carries backslashes PurePosixPath ignores."""
    assert sanitise_filename(r"..\..\secret\file.pdf") == "file.pdf"


def test_alternate_data_stream_suffix_is_neutralised() -> None:
    assert sanitise_filename("report.pdf:hidden") == "report.pdf_hidden"


def test_control_characters_are_removed() -> None:
    assert "\x00" not in sanitise_filename("evil\x00.pdf")
    assert "\n" not in sanitise_filename("line\nbreak.pdf")


@pytest.mark.parametrize("reserved", ["CON.pdf", "nul.txt", "COM1.pdf", "lpt9.pdf"])
def test_windows_reserved_names_are_neutralised(reserved: str) -> None:
    result = sanitise_filename(reserved)
    stem = result.rsplit(".", 1)[0].lower()
    assert stem not in {"con", "nul", "com1", "lpt9"}


def test_empty_filename_gets_a_placeholder() -> None:
    assert sanitise_filename("") == "unnamed"
    assert sanitise_filename("   ") == "unnamed"
    assert sanitise_filename("...") == "unnamed"


def test_long_filename_is_truncated_keeping_the_extension() -> None:
    result = sanitise_filename("a" * 500 + ".pdf")
    assert len(result) <= 200
    assert result.endswith(".pdf")


# ---------------------------------------------------------------------------
# Content-type detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (PDF_HEADER, "application/pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"PK\x03\x04" + b"\x00" * 8, "application/zip"),
        (b"Plain text content here.", "text/plain"),
    ],
)
def test_content_type_is_detected_from_bytes(header: bytes, expected: str) -> None:
    assert detect_content_type(header) == expected


def test_unrecognised_binary_returns_none() -> None:
    assert detect_content_type(b"\x00\x01\x02\x03\xfe\xff") is None


@pytest.mark.parametrize(
    ("header", "fragment"),
    [
        (b"MZ\x90\x00", "Windows"),
        (b"\x7fELF\x02\x01", "Linux"),
        (b"#!/bin/bash\necho hi", "shebang"),
        (b"<?php system($_GET[0]); ?>", "PHP"),
    ],
)
def test_executable_formats_are_detected(header: bytes, fragment: str) -> None:
    detected = detect_dangerous_content(header)
    assert detected is not None and fragment in detected


# ---------------------------------------------------------------------------
# Validation outcomes
# ---------------------------------------------------------------------------


def test_valid_pdf_is_accepted() -> None:
    report = check()
    assert report.is_rejected is False
    assert report.risk_level is RiskLevel.SAFE
    assert report.detected_type == "application/pdf"


def test_executable_renamed_as_pdf_is_rejected() -> None:
    """The central check: content decides, not the extension or declared type."""
    report = check(filename="invoice.pdf", header=b"MZ\x90\x00" + b"\x00" * 60)
    assert report.is_rejected is True
    assert any(f.check == "executable_content" for f in report.findings)

    with pytest.raises(FileSafetyError):
        enforce_safety(report)


def test_declared_type_that_contradicts_content_is_rejected() -> None:
    report = check(declared="application/pdf", header=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    assert report.is_rejected is True
    assert any(f.check == "type_mismatch" for f in report.findings)

    with pytest.raises(UnsupportedMediaTypeError):
        enforce_safety(report)


def test_disallowed_type_is_rejected() -> None:
    report = validate_upload(
        filename="notes.txt",
        declared_content_type="text/plain",
        size_bytes=100,
        header=b"hello world",
        allowed_types=frozenset({"application/pdf"}),
        max_bytes=MAX_BYTES,
    )
    assert report.is_rejected is True
    assert any(f.check == "type_not_allowed" for f in report.findings)


def test_oversized_file_raises_payload_too_large() -> None:
    report = check(size=MAX_BYTES + 1)
    assert report.is_rejected is True
    with pytest.raises(PayloadTooLargeError):
        enforce_safety(report)


def test_empty_file_is_rejected() -> None:
    report = check(size=0)
    assert report.is_rejected is True
    assert any(f.check == "empty_file" for f in report.findings)


def test_unrecognised_content_is_rejected() -> None:
    report = check(header=b"\x00\x01\x02\xfe\xff\x00")
    assert report.is_rejected is True
    assert any(f.check == "unrecognised_content" for f in report.findings)


def test_ooxml_declared_as_docx_over_zip_content_is_accepted() -> None:
    """OOXML is a ZIP container; signature detection cannot go finer."""
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    report = validate_upload(
        filename="letter.docx",
        declared_content_type=docx,
        size_bytes=2048,
        header=b"PK\x03\x04" + b"\x00" * 20,
        allowed_types=frozenset({docx}),
        max_bytes=MAX_BYTES,
    )
    assert report.is_rejected is False


def test_traversal_filename_is_recorded_as_sanitised() -> None:
    report = check(filename="../../etc/passwd.pdf")
    assert report.safe_filename == "passwd.pdf"
    assert any(f.check == "filename_sanitised" for f in report.findings)


# ---------------------------------------------------------------------------
# PDF active content
# ---------------------------------------------------------------------------


def test_pdf_javascript_is_flagged_but_not_rejected() -> None:
    """Blocking would reject genuine documents; the pipeline never executes them."""
    header = PDF_HEADER + b"\n/JavaScript (app.alert('x'))"
    report = check(header=header)
    assert report.is_rejected is False
    assert report.is_suspicious is True
    assert report.risk_level is RiskLevel.SUSPICIOUS


def test_pdf_launch_action_is_flagged() -> None:
    findings = inspect_pdf_structure(PDF_HEADER + b"\n/Launch /F (cmd.exe)")
    assert findings and findings[0].level is RiskLevel.SUSPICIOUS


def test_clean_pdf_has_no_active_content_findings() -> None:
    assert inspect_pdf_structure(PDF_HEADER) == []


def test_duplicate_markers_are_reported_once() -> None:
    findings = inspect_pdf_structure(PDF_HEADER + b"/JavaScript /JavaScript /JS")
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def test_enforce_is_a_noop_for_a_safe_report() -> None:
    enforce_safety(check())


def test_enforce_is_a_noop_for_a_suspicious_report() -> None:
    """Suspicious means flagged for review, not blocked."""
    enforce_safety(check(header=PDF_HEADER + b"/OpenAction"))


def test_report_is_returned_rather_than_raised_so_it_can_be_audited() -> None:
    """An attempted executable upload is exactly what an audit trail should keep."""
    report = check(header=b"MZ\x90\x00")
    assert report.filename == "contract.pdf"
    assert report.rejection_reasons()
