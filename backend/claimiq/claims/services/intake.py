"""Notices and claim bundles, read on arrival and filed in one step.

The flow for both is the same: upload the file (stored and indexed like any
project document), read it — the model where one is configured, the page's own
patterns always — and return a proposal. A person corrects the proposal and
saves it; only then is anything filed on a claim.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import List, Optional, Sequence

from django.db import transaction
from django.utils import timezone

from claimiq.claims.domain.intake import (
    DETAILED_PARTICULARS,
    KIND_LABELS,
    KIND_TO_ELEMENT,
    PageText,
    build_notice_reading,
    heuristic_segments,
    merge_segments,
    page_digest,
    reading_payload,
    segment_payload,
)
from claimiq.claims.models import Claim, ClaimDocument, ClaimType
from claimiq.claims.services.claim_files import (
    ROLE_CLAIM,
    ROLE_NOTICE,
    Attachment,
    _issue_for,
    attach,
)
from claimiq.core.domain.errors import ProcessingError, ValidationError
from claimiq.documents.models import Document, DocumentPage

logger = logging.getLogger("claimiq.claims.intake")

#: Pages of a notice read. A notice is a letter; its date is on page one.
NOTICE_PAGES = 4
NOTICE_PROMPT_CHARS = 9_000
#: Pages a bundle is split over, and how many go to the model at once. The
#: digest is the start of each page, so a window this size fits the context
#: with room for the answer.
BUNDLE_MAX_PAGES = 400
BUNDLE_WINDOW = 30
#: Pages of the claim letter read for the claim's own fields.
LETTER_PAGES = 15


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


def receive(request, *, document_type: str, permission_check) -> Document:
    """The document a reading is for: a new upload, or one already in the project.

    Multipart ``file`` and ``project`` upload a new document; ``document``
    names one already there. ``permission_check(project_id, permission)``
    raises when the user may not act.
    """
    from claimiq.accounts.domain.permissions import DOCUMENT_UPLOAD, DOCUMENT_VIEW
    from claimiq.core.api.permissions import access_for
    from claimiq.core.domain.errors import NotFoundError
    from claimiq.documents.services.upload import upload_document
    from claimiq.projects.models import Project

    data = request.data
    context = access_for(request)
    if context is None or context.organization_id is None:
        raise NotFoundError("The requested project does not exist.")

    if data.get("document"):
        document = (
            Document.objects.filter(
                pk=data["document"],
                project__organization_id=context.organization_id,
                deleted_at__isnull=True,
            )
            .select_related("project", "current_version")
            .first()
        )
        if document is None or document.project_id not in context.accessible_project_ids:
            raise NotFoundError("That document does not exist.")
        permission_check(document.project_id, DOCUMENT_VIEW.code)
        return document

    project = Project.objects.filter(
        pk=data.get("project"), organization_id=context.organization_id
    ).first()
    if project is None or project.pk not in context.accessible_project_ids:
        raise NotFoundError("The requested project does not exist.")
    permission_check(project.pk, DOCUMENT_UPLOAD.code)
    upload = request.FILES.get("file")
    if upload is None:
        raise ValidationError("Attach the PDF to read.", details={"field": "file"})
    result = upload_document(
        project=project,
        upload=upload,
        document_type=document_type,
        user=request.user,
        title=str(data.get("title") or ""),
        allow_duplicate=str(data.get("allow_duplicate", "")).lower() in ("1", "true", "yes", "on"),
    )
    return Document.objects.select_related("project", "current_version").get(pk=result.document.pk)


def document_summary(document: Document) -> dict:
    version = document.current_version
    return {
        "id": str(document.pk),
        "title": document.title,
        "project": str(document.project_id),
        "document_type": document.document_type,
        "page_count": version.page_count if version else None,
        "processing_status": version.processing_status if version else None,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def page_texts(document: Document, *, max_pages: int) -> List[PageText]:
    """The document's page text, from ingestion or, failing that, OCR now."""
    version = document.current_version
    if version is None:
        return []
    rows = list(
        DocumentPage.objects.filter(version=version, page_number__lte=max_pages)
        .order_by("page_number")
        .values_list("page_number", "text")
    )
    if rows and any((text or "").strip() for _, text in rows):
        return [PageText(n, text or "") for n, text in rows]

    # Not processed yet — ingestion runs beside the request — or processed
    # with no text. Read the stored file directly rather than make the person
    # wait: the PDF's own text layer where it has one, OCR for the rest.
    from claimiq.ingestion.domain.layout import FileKind
    from claimiq.ingestion.providers.extraction import PdfPlumberExtractor, build_ocr_provider
    from claimiq.ingestion.providers.pages import page_count, sniff_file_kind

    path = version.file.path
    kind = sniff_file_kind(path)
    total = page_count(path, kind) or 1
    wanted = list(range(1, min(total, max_pages) + 1))

    texts = {}
    if kind == FileKind.PDF:
        try:
            for page in PdfPlumberExtractor().extract(path, page_numbers=wanted):
                if len((page.text or "").strip()) >= 40:
                    texts[page.page_number] = page.text
        except Exception:  # noqa: BLE001 - fall through to OCR
            texts = {}
    scanned = [n for n in wanted if n not in texts]
    if scanned:
        for page in build_ocr_provider().extract(path, page_numbers=scanned):
            texts[page.page_number] = page.text or ""
    return [PageText(n, texts.get(n, "")) for n in wanted]


def _model() -> tuple:
    """The configured model and a provider for it, or ``(None, None)``."""
    from claimiq.ai.providers.ollama import OllamaLLMProvider
    from claimiq.ai.services.configuration import resolve_ai_settings

    ai = resolve_ai_settings()
    model = ai.get("DRAFTING_LLM_MODEL") or ai.get("DEFAULT_LLM_MODEL") or ""
    if not model:
        return None, None
    provider = OllamaLLMProvider(
        ai["OLLAMA_BASE_URL"],
        ai["OLLAMA_TIMEOUT_SECONDS"],
        max_output_tokens=ai.get("LLM_MAX_OUTPUT_TOKENS"),
        context_tokens=ai.get("LLM_CONTEXT_TOKENS"),
    )
    return model, provider


def _ask(prompt_key: str, schema: dict, **variables) -> tuple:
    """One structured model call. Returns ``(payload, model, note)``.

    Never raises for a model failure: the deterministic reading stands on its
    own, and the note says why the model's is missing.
    """
    from claimiq.ai.domain.prompts import get_prompt
    from claimiq.ai.domain.providers import GenerationRequest

    model, provider = _model()
    if model is None:
        return {}, None, "No language model is configured, so the page was read by pattern only."
    prompt = get_prompt(prompt_key)
    started = time.time()
    try:
        result = provider.generate(
            model,
            GenerationRequest(
                prompt=prompt.render(**variables),
                system_prompt=prompt.system,
                temperature=0.0,
                json_schema=schema,
                seed=1,
            ),
        )
        payload = json.loads(result.text)
    except Exception as exc:  # noqa: BLE001 - any model failure falls back
        logger.warning("intake.model_failed", extra={"prompt": prompt_key, "error": type(exc).__name__})
        return {}, model, (
            "The model could not read this document ({}), so it was read by pattern "
            "only. Check every value.".format(type(exc).__name__)
        )
    logger.info("intake.model_read", extra={"prompt": prompt_key, "seconds": round(time.time() - started, 1)})
    return (payload if isinstance(payload, dict) else {}), model, ""


def _joined(pages: Sequence[PageText]) -> str:
    return "\n\n".join(f"--- page {p.page_number} ---\n{p.text}" for p in pages)


def read_notice(document: Document) -> dict:
    """Propose what a notice letter says. Writes nothing."""
    from claimiq.ai.domain.prompts import NOTICE_EXTRACTION_SCHEMA

    pages = page_texts(document, max_pages=NOTICE_PAGES)
    text = _joined(pages)
    if not text.strip():
        raise ProcessingError(
            "No text could be read from this file. A clearer scan usually fixes it.",
            details={"document": str(document.pk)},
        )
    payload, model, note = _ask(
        "notice_extraction", NOTICE_EXTRACTION_SCHEMA, document_text=text[:NOTICE_PROMPT_CHARS]
    )
    reading = build_notice_reading(payload, text=text, first_page=pages[0].text if pages else "")
    if note:
        reading.notes.insert(0, note)
    result = reading_payload(reading)
    result["model"] = model
    result["pages_read"] = len(pages)
    result["suggested_claims"] = _matching_claims(document.project, reading, text)
    return result


def _matching_claims(project, reading, text: str) -> List[dict]:
    """Claims in the project this notice plausibly belongs to, best first."""
    scored = []
    folded = " ".join(text.lower().split())
    clauses = set(reading.value("clauses") or ())
    claim_ref = (reading.value("claim_reference") or "").lower()
    for claim in Claim.objects.filter(project=project, deleted_at__isnull=True):
        score = 0
        reasons = []
        if claim.reference and (
            claim.reference.lower() in folded or (claim_ref and claim_ref == claim.reference.lower())
        ):
            score += 3
            reasons.append("its reference appears in the letter")
        basis = {str(c) for c in (claim.contractual_basis or [])}
        if clauses & basis:
            score += 1
            reasons.append("it relies on the same clause")
        words = [w for w in claim.title.lower().split() if len(w) > 5]
        hits = sum(1 for w in words if w in folded)
        if words and hits >= max(2, len(words) // 3):
            score += 2
            reasons.append("its title's words appear in the letter")
        if score:
            scored.append((score, claim, reasons))
    scored.sort(key=lambda item: -item[0])
    return [
        {"id": str(c.pk), "title": c.title, "reference": c.reference, "why": "; ".join(r)}
        for _, c, r in scored[:5]
    ]


def read_bundle(document: Document) -> dict:
    """Split a claim bundle into its documents and read the claim letter. Writes nothing."""
    from claimiq.ai.domain.prompts import BUNDLE_SEGMENTATION_SCHEMA

    pages = page_texts(document, max_pages=BUNDLE_MAX_PAGES)
    if not any(p.text.strip() for p in pages):
        raise ProcessingError(
            "No text could be read from this file. A clearer scan usually fixes it.",
            details={"document": str(document.pk)},
        )

    proposed: List[dict] = []
    notes: List[str] = []
    model = None
    for start in range(0, len(pages), BUNDLE_WINDOW):
        window = pages[start : start + BUNDLE_WINDOW]
        payload, model, note = _ask(
            "bundle_segmentation",
            BUNDLE_SEGMENTATION_SCHEMA,
            pages=page_digest(window),
            first_page=window[0].page_number,
            last_page=window[-1].page_number,
        )
        if note and note not in notes:
            notes.append(note)
        proposed.extend(payload.get("segments") or [])

    segments, merge_notes = merge_segments(proposed, pages) if proposed else (heuristic_segments(pages), [])
    if not proposed:
        for segment in segments:
            segment.source = "pattern"
    notes.extend(merge_notes)

    letter = next((s for s in segments if s.kind == "claim_letter"), None)
    draft = None
    if letter is not None:
        draft = _draft_from_letter(
            [p for p in pages if letter.first_page <= p.page_number <= letter.last_page][:LETTER_PAGES]
        )
    else:
        notes.append(
            "No claim letter could be told apart from the rest. Mark the pages that are "
            "the claim letter before saving."
        )

    return {
        "pages_read": len(pages),
        "model": model,
        "segments": [segment_payload(s) for s in segments],
        "kinds": [{"code": code, "label": label, "element": KIND_TO_ELEMENT.get(code)} for code, label in KIND_LABELS.items()],
        "draft": draft,
        "notes": notes,
    }


def _draft_from_letter(pages: Sequence[PageText]) -> Optional[dict]:
    """The claim's own fields, read from its letter with the claim reader."""
    from claimiq.ai.domain.prompts import CLAIM_EXTRACTION_SCHEMA
    from claimiq.claims.domain.extraction import build_draft, draft_payload
    from claimiq.claims.services.drafting import MAX_PROMPT_CHARS

    text = _joined(pages)
    if not text.strip():
        return None
    payload, _, note = _ask("claim_extraction", CLAIM_EXTRACTION_SCHEMA, document_text=text[:MAX_PROMPT_CHARS])
    draft = build_draft(payload, source_text=text)
    if note:
        draft.notes.insert(0, note)
    return draft_payload(draft)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def _date(value, field_name: str) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError("Use a date in the form YYYY-MM-DD.", details={"field": field_name}) from None


_CLAIM_TYPES = {c.value for c in ClaimType}


def create_claim(project, fields: dict, *, user) -> Claim:
    """A claim created from what a notice or claim letter says."""
    title = str(fields.get("title") or "").strip()
    if not title:
        raise ValidationError("Give the claim a title.", details={"field": "title"})
    claim_type = fields.get("claim_type") or "other"
    if claim_type not in _CLAIM_TYPES:
        claim_type = "other"
    amount = fields.get("amount_claimed")
    days = fields.get("time_claimed_days")
    basis = fields.get("contractual_basis") or []
    if isinstance(basis, str):
        basis = [c.strip() for c in basis.split(",") if c.strip()]
    return Claim.objects.create(
        project=project,
        title=title[:512],
        reference=str(fields.get("reference") or "")[:64],
        claim_type=claim_type,
        description=str(fields.get("description") or ""),
        event_date=_date(fields.get("event_date"), "event_date"),
        awareness_date=_date(fields.get("awareness_date"), "awareness_date"),
        amount_claimed=amount if amount not in (None, "") else None,
        currency=str(fields.get("currency") or "")[:3],
        time_claimed_days=int(days) if days not in (None, "") else None,
        contractual_basis=[str(c) for c in basis],
        created_by=user,
    )


@transaction.atomic
def save_notice(document: Document, data: dict, *, user) -> dict:
    """File a read notice: on a claim, on a new claim, or on the register alone."""
    from claimiq.correspondence.models import (
        Correspondence,
        CorrespondenceDirection,
        CorrespondenceKind,
        Notice,
    )

    project = document.project
    letter_date = _date(data.get("letter_date"), "letter_date")
    received = _date(data.get("received_date"), "received_date")
    clause = str(data.get("clause_number") or "").strip()
    if letter_date is None:
        raise ValidationError(
            "The date on the letter is what the deadline is checked against. Give it.",
            details={"field": "letter_date"},
        )
    if not clause:
        raise ValidationError(
            "Name the clause the notice is given under.", details={"field": "clause_number"}
        )
    obligation = (
        Notice.Obligation.DETAILED_CLAIM
        if data.get("notice_kind") == DETAILED_PARTICULARS
        else Notice.Obligation.NOTICE_OF_CLAIM
    )

    claim = None
    if data.get("claim"):
        claim = Claim.objects.filter(pk=data["claim"], project=project, deleted_at__isnull=True).first()
        if claim is None:
            raise ValidationError("That claim is not in this project.", details={"field": "claim"})
    elif data.get("new_claim"):
        new = dict(data["new_claim"])
        new.setdefault("contractual_basis", [clause])
        claim = create_claim(project, new, user=user)

    if claim is not None:
        attach(
            claim,
            document,
            Attachment(
                role=ROLE_NOTICE,
                sent_date=letter_date,
                received_date=received,
                clause_number=clause,
                obligation=obligation,
                confirmed=bool(data.get("confirmed", True)),
                note=str(data.get("note") or ""),
            ),
            user=user,
        )

    item = (
        Correspondence.objects.filter(project=project, document=document, deleted_at__isnull=True)
        .order_by("created_at")
        .first()
    )
    if item is None:
        item = Correspondence.objects.create(
            project=project,
            document=document,
            kind=CorrespondenceKind.NOTICE,
            direction=CorrespondenceDirection.OUTBOUND,
            subject=document.title,
            created_by=user,
        )
    item.subject = str(data.get("subject") or item.subject or document.title)[:512]
    item.reference = str(data.get("reference") or item.reference or "")[:128]
    item.sender_raw = str(data.get("sender") or "")[:255]
    item.recipient_raw = str(data.get("recipient") or "")[:255]
    item.sent_date = letter_date
    item.received_date = received
    item.summary = str(data.get("event_description") or item.summary or "")
    item.clause_references = list(dict.fromkeys([clause, *(data.get("clauses") or [])]))
    item.is_confirmed = True
    item.save()

    if claim is None:
        Notice.objects.update_or_create(
            correspondence=item,
            clause_number=clause,
            obligation=obligation,
            defaults={
                "project": project,
                "edition_code": project.contract_edition or "",
                "is_confirmed_notice": bool(data.get("confirmed", True)),
                "notes": str(data.get("note") or ""),
                "updated_by": user,
            },
        )

    if data.get("title") and document.title != data["title"]:
        document.title = str(data["title"])[:512]
        document.save(update_fields=["title", "updated_at"])
    if document.document_date is None:
        document.document_date = letter_date
        document.save(update_fields=["document_date", "updated_at"])

    return {"claim": str(claim.pk) if claim else None, "correspondence": str(item.pk)}


@transaction.atomic
def link_notice(notice, claim: Claim, *, user) -> None:
    """Put an unlinked notice on a claim, as if it had been filed there."""
    item = notice.correspondence
    if item.document_id is None:
        notice.claim = claim
        notice.save(update_fields=["claim", "updated_at"])
        return
    attach(
        claim,
        item.document,
        Attachment(
            role=ROLE_NOTICE,
            sent_date=item.sent_date,
            received_date=item.received_date,
            clause_number=notice.clause_number,
            obligation=notice.obligation or "notice_of_claim",
            confirmed=notice.is_confirmed_notice,
            note=notice.notes,
        ),
        user=user,
    )
    # Filing it updates this assertion in place when its obligation was
    # recorded; an older one with none is left behind unlinked, and goes.
    notice.refresh_from_db()
    if notice.claim_id is None:
        notice.delete()


@transaction.atomic
def apply_bundle(document: Document, data: dict, *, user) -> dict:
    """File a split bundle: the letter as the claim, each part as what it is."""
    from claimiq.claims.models import Evidence, EvidenceRelevance
    from claimiq.correspondence.models import (
        Correspondence,
        CorrespondenceDirection,
        CorrespondenceKind,
        Notice,
    )

    project = document.project
    if data.get("claim"):
        claim = Claim.objects.filter(pk=data["claim"], project=project, deleted_at__isnull=True).first()
        if claim is None:
            raise ValidationError("That claim is not in this project.", details={"field": "claim"})
    elif data.get("new_claim"):
        claim = create_claim(project, dict(data["new_claim"]), user=user)
    else:
        raise ValidationError("Choose the claim this bundle is for, or create one.", details={"field": "claim"})

    segments = [s for s in (data.get("segments") or []) if s.get("include", True)]
    letter = next((s for s in segments if s.get("kind") == "claim_letter"), None)
    sent = _date(data.get("letter_date") or (letter or {}).get("date"), "letter_date")
    clause = str(data.get("clause_number") or "").strip()

    filed = {"claim": str(claim.pk), "letter": False, "notices": 0, "evidence": 0}
    if letter is not None and sent and clause:
        attach(
            claim,
            document,
            Attachment(role=ROLE_CLAIM, sent_date=sent, clause_number=clause, note=_pages(letter)),
            user=user,
        )
        filed["letter"] = True
    else:
        # Filed without a date it cannot be checked, but it is still the claim.
        ClaimDocument.objects.get_or_create(
            claim=claim, document=document, role=ROLE_CLAIM,
            defaults={"note": _pages(letter) if letter else "", "created_by": user},
        )

    for segment in segments:
        kind = segment.get("kind")
        first, last = int(segment["first_page"]), int(segment["last_page"])
        title = (segment.get("title") or KIND_LABELS.get(kind, "Document")).strip()[:480]
        label = f"{title} (pp. {first}–{last})" if last > first else f"{title} (p. {first})"
        if kind == "claim_letter":
            continue
        if kind == "notice" and segment.get("date"):
            item = Correspondence.objects.create(
                project=project,
                document=document,
                subject=label,
                kind=CorrespondenceKind.NOTICE,
                direction=CorrespondenceDirection.OUTBOUND,
                sent_date=_date(segment.get("date"), "date"),
                summary=f"Pages {first}–{last} of {document.title}.",
                created_by=user,
            )
            Notice.objects.create(
                project=project,
                correspondence=item,
                claim=claim,
                clause_number=str(segment.get("clause_number") or clause or ""),
                obligation=Notice.Obligation.NOTICE_OF_CLAIM,
                edition_code=project.contract_edition or "",
                # Read out of a bundle, not filed by a person: confirm it on
                # the Notices register before relying on it.
                is_confirmed_notice=False,
                created_by=user,
            )
            filed["notices"] += 1
        element = segment.get("element") or KIND_TO_ELEMENT.get(kind)
        if not element:
            continue
        issue = _issue_for(claim, element, user)
        if Evidence.objects.filter(
            claim=claim, document=document, issue=issue, page_number=first, deleted_at__isnull=True
        ).exists():
            continue
        Evidence.objects.create(
            project=project,
            claim=claim,
            issue=issue,
            document=document,
            page_number=first,
            title=label,
            description=f"{KIND_LABELS.get(kind, kind)}, pages {first}–{last} of the claim bundle.",
            relevance=EvidenceRelevance.SUPPORTS,
            reviewed_by=user,
            reviewed_at=timezone.now(),
            created_by=user,
        )
        filed["evidence"] += 1

    if filed["evidence"]:
        ClaimDocument.objects.get_or_create(
            claim=claim, document=document, role=ClaimDocument.Role.SUPPORTING,
            defaults={"note": "Annexures within the claim bundle.", "created_by": user},
        )
    return filed


def _pages(segment: Optional[dict]) -> str:
    if not segment:
        return ""
    first, last = segment.get("first_page"), segment.get("last_page")
    return f"Claim letter: pages {first}–{last}" if last != first else f"Claim letter: page {first}"
