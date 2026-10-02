"""Draft a claim from an uploaded claim document.

Reads the pages with OCR, asks the model what fields the document states, and
returns a draft for a person to correct and accept. **Nothing is written.** The
claim exists only when the reviewer saves it, which is the whole point: this is
the one place a model's output would otherwise become the record.

The pipeline used for project documents is deliberately not used here. That one
stores a document, queues a job, and takes minutes; this has to answer while
someone waits with the page in front of them. The file is read in memory, OCR'd
directly, and discarded unless the reviewer keeps it.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, List, Sequence, Tuple


from claimiq.claims.domain.extraction import ClaimDraft, build_draft
from claimiq.core.domain.errors import ProcessingError, ValidationError
from claimiq.ingestion.domain.layout import FileKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger("claimiq.claims.drafting")

#: Pages read from one upload. A claim submission runs to hundreds of pages,
#: and the fields this reads for are on the first few: the cover, the summary,
#: the letter. Reading further costs minutes and adds tables that crowd the
#: prompt without adding a field.
MAX_PAGES = 6

#: Characters of document text put in front of the model. Enough for several
#: pages of a claim submission; beyond it the prompt crowds out the answer on
#: a small local model.
MAX_PROMPT_CHARS = 12_000


def _read_pages(upload: "UploadedFile", *, max_pages: int = MAX_PAGES) -> Tuple[str, List[dict]]:
    """OCR an uploaded image or PDF into text.

    Returns:
        ``(text, pages)`` where pages carries per-page confidence, so a
        reviewer can see which page was read badly rather than wondering why a
        field is missing.
    """
    import os
    import tempfile

    from claimiq.ingestion.providers.extraction import build_ocr_provider
    from claimiq.ingestion.providers.pages import page_count, sniff_file_kind

    suffix = os.path.splitext(getattr(upload, "name", "") or "")[1] or ".bin"
    handle, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as fh:
            for chunk in upload.chunks():
                fh.write(chunk)

        kind = sniff_file_kind(path)
        if kind not in (FileKind.PDF, FileKind.IMAGE):
            raise ValidationError(
                "A claim can be drafted from a photograph, a scan or a PDF. "
                "This file is neither.",
                details={"detected": getattr(kind, "value", str(kind))},
            )

        total = page_count(path, kind) or 1
        wanted = list(range(1, min(total, max_pages) + 1))

        # Raises ProviderUnavailableError, with its own remedy, when neither
        # OCR engine is installed. Not caught: a typed failure that names the
        # fix beats a generic one raised here.
        provider = build_ocr_provider()

        started = time.time()
        extracted = provider.extract(path, page_numbers=wanted)
        logger.info(
            "claims.draft_ocr",
            extra={"pages": len(extracted), "of": total, "seconds": round(time.time() - started, 1)},
        )

        pages = [
            {
                "page_number": page.page_number,
                "characters": len(page.text or ""),
                "confidence": round(page.confidence or 0.0, 3),
            }
            for page in extracted
        ]
        text = "\n\n".join(
            "--- page {} ---\n{}".format(page.page_number, page.text or "")
            for page in extracted
        )
        return text, pages
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - a temp file that is already gone
            pass


def draft_from_upload(uploads: "Sequence[UploadedFile]") -> dict:
    """Read one or more claim documents and propose a claim from them.

    Args:
        uploads: The files a reviewer photographed or scanned. Several pages of
            one claim are read together, because the reference is on the cover
            and the amount is on the summary.

    Returns:
        The draft, the text it was read from, and what was read where. Nothing
        is persisted.

    Raises:
        ValidationError: no file, or a file that is not a page.
        ProcessingError: no OCR engine, or nothing legible on the pages.
    """
    from claimiq.ai.domain.prompts import CLAIM_EXTRACTION_SCHEMA, get_prompt
    from claimiq.ai.domain.providers import GenerationRequest
    from claimiq.ai.providers.ollama import OllamaLLMProvider

    if not uploads:
        raise ValidationError("Attach the claim document to read.")

    texts: List[str] = []
    pages: List[dict] = []
    budget = MAX_PAGES
    for upload in uploads:
        if budget <= 0:
            break
        text, read = _read_pages(upload, max_pages=budget)
        budget -= max(len(read), 1)
        if text.strip():
            texts.append(text)
        pages.extend(read)

    document_text = "\n\n".join(texts).strip()
    if not document_text:
        raise ProcessingError(
            "Nothing could be read from the pages supplied. A sharper "
            "photograph, or a straight-on scan, usually fixes it.",
            details={"pages_read": len(pages)},
        )

    from claimiq.ai.services.configuration import resolve_ai_settings

    ai = resolve_ai_settings()
    # Drafting may use a heavier model than interactive answering: it runs once
    # per claim, and a field read wrongly here is retyped by hand anyway. The
    # resolver already falls back to the answering model when no drafting model
    # is chosen.
    model = ai.get("DRAFTING_LLM_MODEL") or ai.get("DEFAULT_LLM_MODEL") or ""
    if not model:
        raise ProcessingError(
            "No language model is configured, so a claim cannot be drafted "
            "from a document on this installation.",
            details={"remedy": "Configure a model in Administration."},
        )

    prompt = get_prompt("claim_extraction")
    rendered = prompt.render(document_text=document_text[:MAX_PROMPT_CHARS])
    provider = OllamaLLMProvider(
        ai["OLLAMA_BASE_URL"],
        ai["OLLAMA_TIMEOUT_SECONDS"],
        max_output_tokens=ai.get("LLM_MAX_OUTPUT_TOKENS"),
        context_tokens=ai.get("LLM_CONTEXT_TOKENS"),
    )

    started = time.time()
    result = provider.generate(
        model,
        GenerationRequest(
            prompt=rendered,
            system_prompt=prompt.system,
            temperature=0.0,
            json_schema=CLAIM_EXTRACTION_SCHEMA,
            # Reproducible: the same page drafts the same claim twice.
            seed=1,
        ),
    )
    seconds = round(time.time() - started, 1)

    import json

    try:
        payload = json.loads(result.text)
    except (TypeError, ValueError) as exc:
        raise ProcessingError(
            "The model did not return a readable draft. The claim can still be "
            "entered by hand.",
            details={"reason": type(exc).__name__},
        ) from exc
    if not isinstance(payload, dict):
        raise ProcessingError("The model did not return a readable draft.")

    draft: ClaimDraft = build_draft(payload, source_text=document_text)
    logger.info(
        "claims.drafted",
        extra={
            "model": model,
            "seconds": seconds,
            "found": len(draft.found_fields),
            "unverified": len(draft.unverified_fields),
        },
    )

    from claimiq.claims.domain.extraction import draft_payload

    return {
        "draft": draft_payload(draft),
        "document_text": document_text,
        "pages": pages,
        "model": model,
        "seconds": seconds,
    }
