"""Upload validation, before any parser touches the file.

The threat model is specific: this system ingests documents supplied by
opposing parties in commercial disputes. Attachments arrive from outside the
organisation, and the people uploading them are not evaluating them for safety.

Every check here runs **before** a parsing library opens the file, because a
parser is a large C-backed attack surface and handing it hostile input is the
thing to avoid rather than to survive.

The checks:

1. **Filename sanitisation** — path traversal, absolute paths, NTFS alternate
   data streams, Windows reserved device names, control characters.
2. **Declared type vs actual content** — a client-supplied ``Content-Type`` is
   a claim, not evidence. Magic bytes decide.
3. **Allow-list of types** — deny by default.
4. **Size limits** — checked against the real byte count.
5. **Structural red flags** — embedded JavaScript, launch actions and embedded
   files in PDFs.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath

from claimiq.core.domain.errors import (
    FileSafetyError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)


class RiskLevel(str, Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    """Processed, but flagged for an operator and recorded on the document."""
    REJECTED = "rejected"


@dataclass
class SafetyFinding:
    check: str
    level: RiskLevel
    message: str


@dataclass
class SafetyReport:
    filename: str
    safe_filename: str
    detected_type: str | None
    declared_type: str
    size_bytes: int
    findings: list[SafetyFinding] = field(default_factory=list)

    @property
    def is_rejected(self) -> bool:
        return any(f.level is RiskLevel.REJECTED for f in self.findings)

    @property
    def is_suspicious(self) -> bool:
        return any(f.level is RiskLevel.SUSPICIOUS for f in self.findings)

    @property
    def risk_level(self) -> RiskLevel:
        if self.is_rejected:
            return RiskLevel.REJECTED
        if self.is_suspicious:
            return RiskLevel.SUSPICIOUS
        return RiskLevel.SAFE

    def rejection_reasons(self) -> list[str]:
        return [f.message for f in self.findings if f.level is RiskLevel.REJECTED]


#: Leading magic bytes by MIME type. Content decides the type, not the client.
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    # OOXML and legacy Office share container formats; refined below.
    (b"PK\x03\x04", "application/zip"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
    (b"{\\rtf", "application/rtf"),
)

#: Types that must never be accepted, whatever the extension claims.
DANGEROUS_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "Windows executable"),
    (b"\x7fELF", "Linux executable"),
    (b"\xca\xfe\xba\xbe", "Mach-O / Java class"),
    (b"#!/", "Script with a shebang"),
    (b"<?php", "PHP source"),
)

#: Reserved device names on Windows. A file called ``CON.pdf`` can hang or
#: misdirect I/O on a Windows host.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_CHARS_RE = re.compile(r'[<>:"|?*\\/]')
_COLLAPSE_RE = re.compile(r"[_\s]{2,}")

MAX_FILENAME_LENGTH = 200

#: PDF structures that execute or embed content. Legitimate contract documents
#: do not carry them.
_PDF_ACTIVE_MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"/JavaScript", "embedded JavaScript"),
    (b"/JS", "embedded JavaScript"),
    (b"/Launch", "launch action"),
    (b"/EmbeddedFile", "embedded file"),
    (b"/OpenAction", "automatic open action"),
)


def sanitise_filename(filename: str) -> str:
    """Return a filename safe to store on disk.

    Strips any directory component (from both POSIX and Windows separators),
    control characters and shell-hostile characters; neutralises Windows
    reserved device names; and truncates while preserving the extension.

    >>> sanitise_filename("../../etc/passwd")
    'passwd'
    >>> sanitise_filename("C:\\\\Windows\\\\system32\\\\evil.pdf")
    'evil.pdf'
    >>> sanitise_filename("report.pdf:hidden")
    'report.pdf_hidden'
    """
    if not filename:
        return "unnamed"

    normalised = unicodedata.normalize("NFKC", filename)

    # Strip directory components under both path flavours. An upload arriving
    # from a Windows client carries backslashes that PurePosixPath ignores.
    name = PureWindowsPath(PurePosixPath(normalised).name).name
    name = _CONTROL_CHARS_RE.sub("", name)
    name = _UNSAFE_CHARS_RE.sub("_", name)
    name = name.strip(" .")

    if not name:
        return "unnamed"

    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""

    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"{stem}_file"

    stem = _COLLAPSE_RE.sub("_", stem).strip("_") or "unnamed"

    if extension:
        extension = extension[:16]
        allowance = MAX_FILENAME_LENGTH - len(extension) - 1
        return f"{stem[:allowance]}.{extension}"
    return stem[:MAX_FILENAME_LENGTH]


def detect_content_type(header: bytes) -> str | None:
    """Identify a MIME type from leading bytes. None when unrecognised."""
    for signature, mime_type in MAGIC_SIGNATURES:
        if header.startswith(signature):
            return mime_type
    # Plain text has no signature; infer it from decodability.
    if header and _looks_like_text(header):
        return "text/plain"
    return None


def _looks_like_text(header: bytes) -> bool:
    if b"\x00" in header:
        return False
    try:
        header.decode("utf-8")
    except UnicodeDecodeError:
        try:
            header.decode("utf-16")
        except UnicodeDecodeError:
            return False
    return True


def detect_dangerous_content(header: bytes) -> str | None:
    """Return a description if the header matches an executable format."""
    for signature, description in DANGEROUS_SIGNATURES:
        if header.startswith(signature):
            return description
    return None


def _types_are_compatible(declared: str, detected: str) -> bool:
    """True if a declared type is consistent with the detected one.

    OOXML files are ZIP containers and legacy Office files are OLE compound
    documents, so magic bytes cannot distinguish a ``.docx`` from a ``.xlsx``.
    That is a real limit of signature detection, accepted here: both are on the
    allow-list, and the parser will reject a genuine mismatch. The check that
    matters — "is this actually an executable renamed to .pdf" — is unaffected.
    """
    if declared == detected:
        return True
    container_backed = {
        "application/zip": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        },
        "application/x-ole-storage": {
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        },
    }
    if detected in container_backed and declared in container_backed[detected]:
        return True
    # Text-ish declarations over detected plain text.
    if detected == "text/plain" and declared in {
        "text/plain", "text/csv", "message/rfc822", "text/html",
    }:
        return True
    return False


def inspect_pdf_structure(header: bytes) -> list[SafetyFinding]:
    """Flag active content in a PDF.

    Operates on the sampled header, so this detects markers near the start of
    the file rather than proving their absence. It is a signal for the operator,
    not a guarantee — which is why these are SUSPICIOUS rather than REJECTED:
    blocking on them would reject genuine documents produced by tools that add
    an ``/OpenAction``, and the ingestion pipeline never executes PDF content.
    """
    findings: list[SafetyFinding] = []
    seen: set[str] = set()
    for marker, description in _PDF_ACTIVE_MARKERS:
        if marker in header and description not in seen:
            seen.add(description)
            findings.append(
                SafetyFinding(
                    check="pdf_active_content",
                    level=RiskLevel.SUSPICIOUS,
                    message=(
                        f"PDF contains {description}. The document is processed "
                        f"as text only and its active content is never executed, "
                        f"but the file is flagged for review."
                    ),
                )
            )
    return findings


def validate_upload(
    *,
    filename: str,
    declared_content_type: str,
    size_bytes: int,
    header: bytes,
    allowed_types: frozenset[str],
    max_bytes: int,
) -> SafetyReport:
    """Validate an upload. Returns a report; does not raise.

    Use :func:`enforce_safety` to convert a rejecting report into an error.
    Separating the two lets the caller record the report against the document
    even when the upload is refused — an attempted upload of an executable is
    exactly the event an audit trail should retain.

    Args:
        filename: Client-supplied filename.
        declared_content_type: Client-supplied MIME type. Treated as a claim.
        size_bytes: Actual byte count, measured server-side.
        header: First few KB of the file.
        allowed_types: Permitted MIME types.
        max_bytes: Maximum accepted size.
    """
    safe_filename = sanitise_filename(filename)
    detected = detect_content_type(header)
    report = SafetyReport(
        filename=filename,
        safe_filename=safe_filename,
        detected_type=detected,
        declared_type=declared_content_type,
        size_bytes=size_bytes,
    )

    if safe_filename != filename:
        report.findings.append(
            SafetyFinding(
                check="filename_sanitised",
                level=RiskLevel.SAFE,
                message=f"Filename normalised for storage: {safe_filename!r}.",
            )
        )

    if size_bytes <= 0:
        report.findings.append(
            SafetyFinding("empty_file", RiskLevel.REJECTED, "The file is empty.")
        )
        return report

    if size_bytes > max_bytes:
        report.findings.append(
            SafetyFinding(
                "size_limit",
                RiskLevel.REJECTED,
                f"File is {size_bytes / 1_048_576:.1f} MB; the limit is "
                f"{max_bytes / 1_048_576:.0f} MB.",
            )
        )

    dangerous = detect_dangerous_content(header)
    if dangerous is not None:
        report.findings.append(
            SafetyFinding(
                "executable_content",
                RiskLevel.REJECTED,
                f"File content is a {dangerous}, regardless of its extension.",
            )
        )
        return report

    if detected is None:
        report.findings.append(
            SafetyFinding(
                "unrecognised_content",
                RiskLevel.REJECTED,
                "File content does not match any supported document format.",
            )
        )
        return report

    if not _types_are_compatible(declared_content_type, detected):
        report.findings.append(
            SafetyFinding(
                "type_mismatch",
                RiskLevel.REJECTED,
                f"File was declared as {declared_content_type!r} but its content "
                f"is {detected!r}.",
            )
        )
        return report

    if declared_content_type not in allowed_types:
        report.findings.append(
            SafetyFinding(
                "type_not_allowed",
                RiskLevel.REJECTED,
                f"Files of type {declared_content_type!r} are not accepted.",
            )
        )
        return report

    if detected == "application/pdf":
        report.findings.extend(inspect_pdf_structure(header))

    return report


def enforce_safety(report: SafetyReport) -> None:
    """Raise the appropriate typed error when ``report`` rejects the upload."""
    if not report.is_rejected:
        return

    checks = {f.check for f in report.findings if f.level is RiskLevel.REJECTED}
    reasons = report.rejection_reasons()

    if "size_limit" in checks:
        raise PayloadTooLargeError(reasons[0], details={"filename": report.safe_filename})

    if checks & {"type_not_allowed", "type_mismatch", "unrecognised_content"}:
        raise UnsupportedMediaTypeError(
            reasons[0],
            details={
                "filename": report.safe_filename,
                "declared_type": report.declared_type,
                "detected_type": report.detected_type,
            },
        )

    raise FileSafetyError(
        reasons[0],
        details={"filename": report.safe_filename, "reasons": reasons},
    )
