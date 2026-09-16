"""Page images for the document viewer.

Rendered on first request and cached on disk beside the media store, keyed by
version, page and scale. A version's file never changes after upload (a new
upload is a new version), so the cache never needs invalidating.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

from claimiq.core.domain.errors import NotFoundError, ValidationError
from claimiq.ingestion.domain.layout import FileKind
from claimiq.ingestion.providers.pages import render_page_image, sniff_file_kind

#: Scales the viewer may request. Bounded, so a client cannot fill the disk with
#: arbitrary-resolution renders.
ALLOWED_SCALES = (1.0, 1.5, 2.0)


def cache_path(version_id, page_number: int, scale: float) -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / "cache"
        / "pages"
        / str(version_id)
        / f"p{page_number:05d}@{scale:.1f}.png"
    )


def file_kind_of(version) -> FileKind:
    return sniff_file_kind(
        version.file.path,
        filename=version.original_filename,
        content_type=version.content_type,
    )


def page_image_path(version, page_number: int, scale: float = 1.5) -> Path:
    """Return a PNG of ``page_number`` of ``version``, rendering it if needed.

    Raises:
        ValidationError: unsupported scale or page number.
        NotFoundError: the file has no page images (DOCX, text) or the page
            does not exist. The viewer shows extracted text instead.
    """
    if scale not in ALLOWED_SCALES:
        raise ValidationError(
            "Unsupported render scale.", details={"allowed": list(ALLOWED_SCALES)}
        )
    if page_number < 1:
        raise ValidationError("Page numbers start at 1.")
    if version.page_count and page_number > version.page_count:
        raise NotFoundError(
            f"This document has {version.page_count} pages.",
            details={"page_number": page_number, "page_count": version.page_count},
        )

    target = cache_path(version.pk, page_number, scale)
    if target.exists():
        return target

    kind = file_kind_of(version)
    if not kind.has_page_images:
        raise NotFoundError(
            "This document has no page images; its extracted text is shown instead.",
            details={"reason": "no_page_image", "file_kind": kind.value},
        )

    image = render_page_image(version.file.path, kind, page_number, scale=scale)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(target)
    return target
