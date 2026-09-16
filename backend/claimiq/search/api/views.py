"""Search: hybrid retrieval without generation.

``POST /api/v1/search/`` runs the same retrieval pipeline the AI uses — clause
lookup, lexical, vector, fusion — and returns the passages themselves. Useful
on its own, and the honest way to answer "what does the record say about X"
without a model in between.

Every result carries its source layer: a project document, or the standard
form for the project's declared edition. The two are never merged into one
undifferentiated list (docs/PHASE7.md, D3).
"""
from __future__ import annotations

from django.conf import settings
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import DOCUMENT_VIEW
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus
from claimiq.projects.models import Project
from claimiq.search.domain.scope import combined_scope, knowledge_base_scope, project_scope

SCOPE_DOCUMENTS = "documents"
SCOPE_KNOWLEDGE = "knowledge"

LAYER_PROJECT = "project_document"
LAYER_STANDARD_FORM = "standard_form"


class SearchSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=2, max_length=500)
    project = serializers.UUIDField()
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=[SCOPE_DOCUMENTS, SCOPE_KNOWLEDGE]),
        required=False,
        allow_empty=True,
    )
    document_types = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    page_size = serializers.IntegerField(min_value=1, max_value=50, default=20)


def edition_label(code: str | None) -> str | None:
    if not code:
        return None
    return DEFAULT_REGISTRY.get_edition(code).label if DEFAULT_REGISTRY.has_edition(code) else code


class SearchView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "search"

    def post(self, request: Request) -> Response:
        serializer = SearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        context = access_for(request, data["project"])
        if context is None or context.organization_id is None:
            raise NotFoundError("The requested project does not exist.")
        context.require(DOCUMENT_VIEW.code)
        project = Project.objects.filter(
            pk=data["project"], organization_id=context.organization_id
        ).first()
        if project is None or project.pk not in context.accessible_project_ids:
            raise NotFoundError("The requested project does not exist.")

        requested = set(data.get("scopes") or [SCOPE_DOCUMENTS, SCOPE_KNOWLEDGE])
        notes: list[str] = []
        edition = project.contract_edition or None

        if SCOPE_KNOWLEDGE in requested:
            if not edition:
                requested.discard(SCOPE_KNOWLEDGE)
                notes.append(
                    "The standard form was not searched: this project has not declared "
                    "its governing contract edition."
                )
            elif not KnowledgeBase.objects.filter(
                organization_id=context.organization_id,
                edition_code=edition,
                status=KnowledgeBaseStatus.PUBLISHED,
            ).exists():
                notes.append(
                    f"No published knowledge base exists for {edition_label(edition)}, so "
                    f"no standard-form text could be found."
                )

        document_types = data.get("document_types") or None
        if requested == {SCOPE_DOCUMENTS, SCOPE_KNOWLEDGE}:
            scope = combined_scope(
                organization_id=context.organization_id,
                project_id=project.pk,
                edition=edition,
                accessible_project_ids=context.accessible_project_ids,
                document_type_codes=document_types,
            )
        elif requested == {SCOPE_DOCUMENTS}:
            scope = project_scope(
                organization_id=context.organization_id,
                project_id=project.pk,
                accessible_project_ids=context.accessible_project_ids,
                document_type_codes=document_types,
            )
        elif requested == {SCOPE_KNOWLEDGE}:
            scope = knowledge_base_scope(
                organization_id=context.organization_id,
                edition=edition,
                accessible_project_ids=context.accessible_project_ids,
            )
        else:
            return Response(
                {
                    "query": data["query"],
                    "project": str(project.pk),
                    "edition_code": edition,
                    "edition_label": edition_label(edition),
                    "scopes_searched": [],
                    "count": 0,
                    "results": [],
                    "notes": notes or ["Nothing was selected to search."],
                }
            )

        from claimiq.search.services.retrieval import build_default_service

        service = build_default_service()
        # A search results list, unlike a model's context, should not be capped
        # at a handful of passages per document.
        service.config = {
            **settings.RETRIEVAL_SETTINGS,
            "FINAL_CONTEXT_CHUNKS": data["page_size"],
            "MAX_CHUNKS_PER_DOCUMENT": data["page_size"],
        }
        result = service.retrieve(data["query"], scope)

        results = []
        for fused in result.context.chunks:
            chunk = fused.chunk
            results.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "layer": LAYER_STANDARD_FORM if chunk.is_knowledge_base else LAYER_PROJECT,
                    "document_id": chunk.document_id,
                    "knowledge_base_id": chunk.document_id if chunk.is_knowledge_base else None,
                    "document_title": chunk.document_title,
                    "page_number": chunk.page_number,
                    "clause_number": chunk.clause_number,
                    "edition_code": chunk.edition,
                    "edition_label": edition_label(chunk.edition),
                    "text": chunk.text,
                    "score": round(fused.fused_score, 5),
                    "matched_by": sorted(m.value for m in fused.methods),
                }
            )

        return Response(
            {
                "query": data["query"],
                "project": str(project.pk),
                "edition_code": edition,
                "edition_label": edition_label(edition),
                "scopes_searched": sorted(requested),
                "intent": result.analysis.intent.value,
                "vector_search_ran": result.trace.vector_search_ran,
                "count": len(results),
                "results": results,
                "notes": notes + list(result.trace.notes),
            }
        )
