"""Knowledge base endpoints.

- ``GET  /api/v1/knowledge/editions/``                   registered editions and their build state
- ``GET  /api/v1/knowledge/bases/``                      knowledge bases in the organization
- ``POST /api/v1/knowledge/bases/``                      upload a standard form (multipart)
- ``GET  /api/v1/knowledge/bases/{id}/``                 detail, processing and validation report
- ``POST /api/v1/knowledge/bases/{id}/replace-source/``   rebuild from a new file
- ``POST /api/v1/knowledge/bases/{id}/validate/``         re-run validation
- ``POST /api/v1/knowledge/bases/{id}/publish/``          make retrievable
- ``POST /api/v1/knowledge/bases/{id}/unpublish/``        withdraw from retrieval
- ``GET  /api/v1/knowledge/bases/{id}/clauses/``          clause index, or one clause's text
- ``GET  /api/v1/knowledge/bases/{id}/chunks/``           chunks, including quarantined
- ``GET  /api/v1/knowledge/bases/{id}/pages/{n}/``        source page text
- ``GET  /api/v1/knowledge/bases/{id}/pages/{n}/image/``  source page image

Reading is open to every organization member: standard forms are reference
material. Changing anything requires ``org.kb.manage``.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from django.db.models import Count, Min
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import ORG_MANAGE_KB
from claimiq.core.api.pagination import LargePagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.documents.api.viewer import (
    original_file_response,
    page_image_response,
    page_payload,
    processing_payload,
)
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseChunk, KnowledgeBaseStatus
from claimiq.knowledge.services import build


def _org_context(request: Request):
    context = access_for(request)
    if context is None or context.organization_id is None:
        raise PermissionDeniedError("You are not a member of an active organization.")
    return context


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _clause_key(number: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.\s]+", number) if p)


def kb_summary(kb: KnowledgeBase) -> dict[str, Any]:
    report = kb.validation_report or {}
    version = kb.source_document.current_version if kb.source_document_id else None
    label = (
        DEFAULT_REGISTRY.get_edition(kb.edition_code).label
        if DEFAULT_REGISTRY.has_edition(kb.edition_code)
        else kb.edition_code
    )
    return {
        "id": str(kb.pk),
        "name": kb.name,
        "description": kb.description,
        "edition_code": kb.edition_code,
        "edition_label": label,
        "form_code": kb.form_code,
        "status": kb.status,
        "is_retrievable": kb.status == KnowledgeBaseStatus.PUBLISHED,
        "chunk_count": kb.chunk_count,
        "retained_chunks": report.get("retained_chunks"),
        "quarantined_chunks": report.get("quarantined_chunks"),
        "embedded_chunks": report.get("embedded_chunks"),
        "is_publishable": report.get("is_publishable"),
        "build_error": kb.build_error or None,
        "validated_at": _iso(kb.validated_at),
        "published_at": _iso(kb.published_at),
        "published_by": kb.published_by.email if kb.published_by_id else None,
        "created_at": _iso(kb.created_at),
        "updated_at": _iso(kb.updated_at),
        "source": (
            {
                "document_id": str(kb.source_document_id),
                "filename": version.original_filename,
                "page_count": version.page_count,
                "processing_status": version.processing_status,
                "processing_error": version.processing_error or None,
                "extraction_method": version.extraction_method,
                "extraction_quality": version.extraction_quality,
                "file_size_bytes": version.file_size_bytes,
            }
            if version
            else None
        ),
    }


class EditionCatalogView(APIView):
    """Registered editions, and whether this organization can retrieve each."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        context = _org_context(request)
        bases = {
            kb.edition_code: kb
            for kb in KnowledgeBase.objects.filter(organization_id=context.organization_id)
        }
        return Response(
            {
                "forms": [
                    {"code": f.code, "name": f.name, "publisher": f.publisher}
                    for f in DEFAULT_REGISTRY.forms()
                ],
                "editions": [
                    {
                        "code": e.code,
                        "label": e.label,
                        "form_code": e.form_code,
                        "year": e.year,
                        "notes": e.notes,
                        "topics": [
                            {"topic": t.topic, "clause_number": t.clause_number, "title": t.title}
                            for t in e.topic_map.values()
                        ],
                        "knowledge_base": (
                            {
                                "id": str(bases[e.code].pk),
                                "status": bases[e.code].status,
                                "is_retrievable": bases[e.code].status
                                == KnowledgeBaseStatus.PUBLISHED,
                            }
                            if e.code in bases
                            else None
                        ),
                    }
                    for e in DEFAULT_REGISTRY.editions()
                ],
            }
        )


class KnowledgeBaseViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_throttles(self):
        if getattr(self, "action", None) in ("create", "replace_source"):
            self.throttle_scope = "upload"
        return super().get_throttles()

    def _get(self, request: Request, pk) -> tuple[KnowledgeBase, Any]:
        context = _org_context(request)
        try:
            kb_id = UUID(str(pk))
        except ValueError:
            raise NotFoundError("The requested knowledge base does not exist.") from None
        kb = (
            KnowledgeBase.objects.select_related("source_document__current_version", "published_by")
            .filter(pk=kb_id, organization_id=context.organization_id)
            .first()
        )
        if kb is None:
            raise NotFoundError("The requested knowledge base does not exist.")
        return kb, context

    def list(self, request: Request) -> Response:
        context = _org_context(request)
        queryset = KnowledgeBase.objects.select_related(
            "source_document__current_version", "published_by"
        ).filter(organization_id=context.organization_id)
        if edition := request.query_params.get("edition"):
            queryset = queryset.filter(edition_code=edition)
        return Response(
            {
                "can_manage": context.has(ORG_MANAGE_KB.code),
                "results": [kb_summary(kb) for kb in queryset.order_by("edition_code")],
            }
        )

    def retrieve(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        return Response(
            {
                **kb_summary(kb),
                "can_manage": context.has(ORG_MANAGE_KB.code),
                "validation_report": kb.validation_report or None,
                "processing": processing_payload(kb.source_document) if kb.source_document_id else None,
            }
        )

    def create(self, request: Request) -> Response:
        context = _org_context(request)
        context.require(ORG_MANAGE_KB.code)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("A 'file' is required.", details={"field": "file"})
        edition_code = (request.data.get("edition_code") or "").strip()
        if not edition_code:
            raise ValidationError("An 'edition_code' is required.", details={"field": "edition_code"})
        kb = build.create_knowledge_base(
            organization_id=context.organization_id,
            user=request.user,
            edition_code=edition_code,
            upload=upload,
            name=request.data.get("name", "") or "",
            description=request.data.get("description", "") or "",
        )
        kb.refresh_from_db()
        return Response(kb_summary(kb), status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        context.require(ORG_MANAGE_KB.code)
        build.delete_knowledge_base(kb, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="replace-source")
    def replace_source(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        context.require(ORG_MANAGE_KB.code)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("A 'file' is required.", details={"field": "file"})
        build.replace_source(kb, user=request.user, upload=upload)
        kb.refresh_from_db()
        return Response(kb_summary(kb), status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def validate(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        context.require(ORG_MANAGE_KB.code)
        if kb.status in (KnowledgeBaseStatus.PROCESSING, KnowledgeBaseStatus.DRAFT):
            raise ValidationError("The source document has not finished processing.")
        if not kb.chunk_count and kb.source_document_id:
            build.build_from_source(kb)
        else:
            build.validate(kb, actor=request.user)
        kb.refresh_from_db()
        return Response({**kb_summary(kb), "validation_report": kb.validation_report or None})

    @action(detail=True, methods=["post"])
    def publish(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        context.require(ORG_MANAGE_KB.code)
        build.publish(kb, user=request.user)
        return Response(kb_summary(kb))

    @action(detail=True, methods=["post"])
    def unpublish(self, request: Request, pk=None) -> Response:
        kb, context = self._get(request, pk)
        context.require(ORG_MANAGE_KB.code)
        build.unpublish(kb, user=request.user)
        return Response(kb_summary(kb))

    @action(detail=True, methods=["get"])
    def clauses(self, request: Request, pk=None) -> Response:
        kb, _context = self._get(request, pk)
        chunks = KnowledgeBaseChunk.objects.filter(knowledge_base=kb, is_quarantined=False)

        if number := request.query_params.get("number"):
            rows = chunks.filter(clause_number=number.strip()).order_by("sequence")
            if not rows.exists():
                raise NotFoundError(
                    f"Clause {number} is not in this knowledge base.",
                    details={"clause_number": number},
                )
            return Response(
                {
                    "clause_number": number,
                    "edition_code": kb.edition_code,
                    "title": next((r.clause_title for r in rows if r.clause_title), ""),
                    "chunks": [
                        {"id": str(r.pk), "page_number": r.page_number, "text": r.text}
                        for r in rows
                    ],
                }
            )

        index = (
            chunks.exclude(clause_number="")
            .values("clause_number")
            .annotate(title=Min("clause_title"), first_page=Min("page_number"), chunks=Count("id"))
            .order_by()
        )
        return Response(
            {
                "edition_code": kb.edition_code,
                "results": sorted(
                    (
                        {
                            "clause_number": row["clause_number"],
                            "title": row["title"],
                            "page_number": row["first_page"],
                            "chunks": row["chunks"],
                        }
                        for row in index
                    ),
                    key=lambda row: _clause_key(row["clause_number"]),
                ),
            }
        )

    @action(detail=True, methods=["get"])
    def chunks(self, request: Request, pk=None) -> Response:
        kb, _context = self._get(request, pk)
        queryset = KnowledgeBaseChunk.objects.filter(knowledge_base=kb).order_by("sequence")
        quarantined = request.query_params.get("quarantined")
        if quarantined in ("true", "false"):
            queryset = queryset.filter(is_quarantined=quarantined == "true")
        if clause := request.query_params.get("clause"):
            queryset = queryset.filter(clause_number=clause)
        if page := request.query_params.get("page_number"):
            if page.isdigit():
                queryset = queryset.filter(page_number=int(page))
        paginator = LargePagination()
        rows = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            [
                {
                    "id": str(c.pk),
                    "sequence": c.sequence,
                    "clause_number": c.clause_number or None,
                    "clause_title": c.clause_title or None,
                    "page_number": c.page_number,
                    "text": c.text,
                    "char_count": c.char_count,
                    "has_embedding": bool(c.embedding_model),
                    "is_quarantined": c.is_quarantined,
                    "quarantine_reason": c.quarantine_reason or None,
                }
                for c in rows
            ]
        )

    def _source(self, kb: KnowledgeBase):
        if not kb.source_document_id:
            raise NotFoundError("This knowledge base has no source document.")
        return kb.source_document

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_number>\d+)")
    def page(self, request: Request, pk=None, page_number: str = "1") -> Response:
        kb, _context = self._get(request, pk)
        payload = page_payload(self._source(kb), int(page_number))
        payload["knowledge_base_id"] = str(kb.pk)
        payload["edition_code"] = kb.edition_code
        return Response(payload)

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_number>\d+)/image")
    def page_image(self, request: Request, pk=None, page_number: str = "1"):
        kb, _context = self._get(request, pk)
        return page_image_response(self._source(kb), int(page_number), request)

    @action(detail=True, methods=["get"])
    def file(self, request: Request, pk=None):
        kb, _context = self._get(request, pk)
        return original_file_response(self._source(kb))
