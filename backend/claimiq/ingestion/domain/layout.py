"""Layout helpers for extraction: file-kind detection, pagination, OCR line
assembly and table rendering.

Pure functions over bytes, strings and coordinates, so the decisions that shape
extracted text — which engine a file needs, where a pseudo-page breaks, what
order OCR boxes read in — are testable without a PDF library or a model.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple


class FileKind(str, Enum):
    """How a stored file must be read."""

    PDF = "pdf"
    IMAGE = "image"
    """PNG, JPEG or TIFF. Every page is scanned by definition."""

    DOCX = "docx"
    XLSX = "xlsx"
    TEXT = "text"

    LEGACY_OFFICE = "legacy_office"
    """Word 97–2003 / Excel 97–2003 (OLE compound files). Stored, not extracted:
    no offline reader exists without a system dependency."""

    UNSUPPORTED = "unsupported"

    @property
    def has_page_images(self) -> bool:
        """Whether a page can be shown as an image in the viewer."""
        return self in (FileKind.PDF, FileKind.IMAGE)


_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_TIFF = (b"II*\x00", b"MM\x00*")
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_TEXT_EXTENSIONS = frozenset({".txt", ".csv", ".eml", ".md"})


def _extension(filename: str) -> str:
    match = re.search(r"(\.[A-Za-z0-9]{1,5})$", filename or "")
    return match.group(1).lower() if match else ""


def detect_file_kind(
    filename: str,
    content_type: str = "",
    header: bytes = b"",
    zip_names: Sequence[str] = (),
) -> FileKind:
    """Decide how a file is read, from its content first and its name last.

    The header decides wherever it can. A client-supplied name or content type
    is only consulted for plain text, which has no signature — trusting an
    extension is how a renamed file reaches the wrong parser.

    Args:
        filename: Original filename.
        content_type: Declared or detected MIME type.
        header: The first bytes of the file.
        zip_names: Member names, when the file is a ZIP container (DOCX/XLSX).
    """
    if header.startswith(b"%PDF"):
        return FileKind.PDF
    if header.startswith(_PNG) or header.startswith(_JPEG) or header[:4] in _TIFF:
        return FileKind.IMAGE
    if header.startswith(_OLE):
        return FileKind.LEGACY_OFFICE
    if header.startswith(b"PK"):
        if any(name.startswith("word/") for name in zip_names):
            return FileKind.DOCX
        if any(name.startswith("xl/") for name in zip_names):
            return FileKind.XLSX
        return FileKind.UNSUPPORTED

    looks_textual = bool(header) and b"\x00" not in header
    declared_text = (content_type or "").startswith(("text/", "message/"))
    if looks_textual and (declared_text or _extension(filename) in _TEXT_EXTENSIONS):
        return FileKind.TEXT
    return FileKind.UNSUPPORTED


#: Target size of a pseudo-page for formats without pagination. Close to a page
#: of contract prose, so page-based citations stay a similar granularity.
DEFAULT_PAGE_CHARS = 3500


def _split_long(paragraph: str, max_chars: int) -> List[str]:
    """Split one over-long paragraph at sentence, then word, boundaries."""
    parts: List[str] = []
    remaining = paragraph
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind(". "), window.rfind("; "))
        if cut < max_chars // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        else:
            cut += 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def paginate_text(text: str, max_chars: int = DEFAULT_PAGE_CHARS) -> List[str]:
    """Split running text into pseudo-pages on paragraph boundaries.

    DOCX, spreadsheets and plain text have no pages, but citations and the
    viewer are page-based. Breaking on paragraphs keeps a clause's text
    together wherever it fits on one pseudo-page.
    """
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")]
    pages: List[str] = []
    current: List[str] = []
    size = 0
    for paragraph in paragraphs:
        if not paragraph:
            continue
        for piece in _split_long(paragraph, max_chars):
            if current and size + len(piece) + 2 > max_chars:
                pages.append("\n\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + 2
    if current:
        pages.append("\n\n".join(current))
    return pages


def render_table_text(rows: Iterable[Sequence[Optional[object]]]) -> str:
    """Render table rows as pipe-separated lines.

    Cell whitespace is collapsed and fully empty rows dropped. A cell that was
    genuinely empty stays an empty column, so values keep their column position
    — a quantity shifted into the rate column is a wrong number, not a
    formatting blemish.
    """
    lines: List[str] = []
    for row in rows:
        cells = ["" if cell is None else re.sub(r"\s+", " ", str(cell)).strip() for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


#: One OCR detection: four corner points, recognised text, confidence.
OCRBox = Tuple[Sequence[Sequence[float]], str, float]


def assemble_ocr_lines(boxes: Sequence[OCRBox]) -> Tuple[str, Optional[float]]:
    """Order OCR detections into reading order and join them into lines.

    Detectors return text boxes in no guaranteed order. Boxes are grouped into
    a line when they overlap vertically by at least half the shorter box, then
    read left to right; lines are read top to bottom.

    Returns:
        ``(text, mean_confidence)``. Confidence is None when nothing was read.
    """
    items = []
    for points, text, confidence in boxes:
        if not text or not str(text).strip() or not points:
            continue
        ys = [float(p[1]) for p in points]
        xs = [float(p[0]) for p in points]
        items.append((min(ys), max(ys), min(xs), str(text).strip(), float(confidence)))

    if not items:
        return "", None

    items.sort(key=lambda item: ((item[0] + item[1]) / 2, item[2]))

    lines: List[List[tuple]] = []
    for item in items:
        top, bottom = item[0], item[1]
        placed = False
        if lines:
            line = lines[-1]
            line_top = min(i[0] for i in line)
            line_bottom = max(i[1] for i in line)
            overlap = min(bottom, line_bottom) - max(top, line_top)
            shorter = min(bottom - top, line_bottom - line_top) or 1.0
            if overlap >= 0.5 * shorter:
                line.append(item)
                placed = True
        if not placed:
            lines.append([item])

    rendered = [" ".join(i[3] for i in sorted(line, key=lambda i: i[2])) for line in lines]
    confidences = [i[4] for i in items]
    return "\n".join(rendered), sum(confidences) / len(confidences)
