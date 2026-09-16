"""Page access for stored files: kind detection and page images.

Shared by the OCR provider (which reads pages as images) and the document
viewer (which shows them). Heavy imports stay inside functions, as in
:mod:`claimiq.ingestion.providers.extraction`.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterator, Sequence

from claimiq.core.domain.errors import ExtractionError
from claimiq.ingestion.domain.layout import FileKind, detect_file_kind

HEADER_BYTES = 8192

#: PDF render scale. pdfium renders at 72 dpi per unit of scale, so 2.0 is
#: 144 dpi: enough for OCR of A4 contract text, and a readable viewer image.
DEFAULT_RENDER_SCALE = 2.0

#: Longest edge for image files shown in the viewer. Site photographs arrive at
#: 4000px+; the viewer needs a fraction of that.
MAX_IMAGE_EDGE = 2400


def sniff_file_kind(path: str, *, filename: str = "", content_type: str = "") -> FileKind:
    """Detect how the file at ``path`` must be read."""
    with open(path, "rb") as handle:
        header = handle.read(HEADER_BYTES)
    names: tuple[str, ...] = ()
    if header.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = tuple(archive.namelist()[:500])
        except zipfile.BadZipFile:
            names = ()
    return detect_file_kind(filename or Path(path).name, content_type, header, names)


def page_count(path: str, kind: FileKind) -> int:
    if kind is FileKind.PDF:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(path)
        try:
            return len(pdf)
        finally:
            pdf.close()
    if kind is FileKind.IMAGE:
        from PIL import Image

        with Image.open(path) as image:
            return int(getattr(image, "n_frames", 1) or 1)
    raise ExtractionError(
        "This file has no pages to count.", details={"file_kind": kind.value}
    )


def iter_page_images(
    path: str,
    kind: FileKind,
    page_numbers: Sequence[int] | None = None,
    *,
    scale: float = DEFAULT_RENDER_SCALE,
) -> Iterator[tuple[int, object]]:
    """Yield ``(page_number, PIL.Image)`` for the requested pages, in order.

    Pages outside the file are ignored rather than raising, so a stale
    checkpoint cannot crash a resumed stage.
    """
    if kind is FileKind.PDF:
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(path)
        except Exception as exc:  # noqa: BLE001 - pdfium raises a generic error
            raise ExtractionError(
                "The PDF could not be opened for rendering.",
                details={"reason": type(exc).__name__},
            ) from exc
        try:
            total = len(pdf)
            wanted = sorted({p for p in page_numbers if 1 <= p <= total}) if page_numbers else range(1, total + 1)
            for number in wanted:
                page = pdf[number - 1]
                try:
                    yield number, page.render(scale=scale).to_pil().convert("RGB")
                finally:
                    page.close()
        finally:
            pdf.close()
        return

    if kind is FileKind.IMAGE:
        from PIL import Image, ImageOps

        with Image.open(path) as image:
            total = int(getattr(image, "n_frames", 1) or 1)
            wanted = sorted({p for p in page_numbers if 1 <= p <= total}) if page_numbers else range(1, total + 1)
            for number in wanted:
                image.seek(number - 1)
                # Phone photographs carry their orientation in EXIF; without
                # this a portrait scan is OCR'd sideways.
                frame = ImageOps.exif_transpose(image.copy()).convert("RGB")
                yield number, frame
        return

    raise ExtractionError(
        "Page images are only available for PDF and image files.",
        details={"file_kind": kind.value},
    )


def render_page_image(path: str, kind: FileKind, page_number: int, *, scale: float) -> object:
    """Return one page as a PIL image, bounded for display."""
    for _number, image in iter_page_images(path, kind, [page_number], scale=scale):
        if kind is FileKind.IMAGE:
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        return image
    raise ExtractionError(
        f"Page {page_number} does not exist in this file.",
        details={"page_number": page_number},
    )
