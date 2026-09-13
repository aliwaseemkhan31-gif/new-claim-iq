"""AI endpoints."""
from __future__ import annotations

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import AI_QUERY
from claimiq.ai.services.answering import build_default_answering_service
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.core.logging import get_logger
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.projects.models import Project
from claimiq.search.domain.scope import combined_scope, project_scope

logger = get_logger("ai.api")


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=3, max_length=4000)
    project = serializers.UUIDField()
    include_knowledge_base = serializers.BooleanField(default=True)
    document_types = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )


class AskView(APIView):
    """Answer a question about a project, grounded in its documents.

    Long-running: generation on CPU can take minutes. Kept synchronous for now
    because the frontend shows a spinner and a job round-trip would add
    complexity without changing the wait. Moving it to the `ai` queue is a
    contained change — the service is already provider-injected.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "ai"

    def post(self, request: Request) -> Response:
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        project_id = data["project"]
        context = access_for(request, project_id)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")
        context.require(AI_QUERY.code)

        project = Project.objects.filter(
            pk=project_id, organization_id=context.organization_id
        ).first()
        if project is None or project.pk not in context.accessible_project_ids:
            raise NotFoundError("The requested project does not exist.")

        scope = self._build_scope(project, context, data)

        service = build_default_answering_service()
        record = service.ask(data["question"], scope)

        return Response(
            {
                "answer": {
                    "summary": record.answer.summary,
                    "confidence": record.answer.confidence.value,
                    "insufficient_evidence": record.answer.insufficient_evidence,
                    "findings": [
                        {
                            "statement": f.statement,
                            "status": f.status.value,
                            "citations": [
                                {
                                    "ref": c.ref,
                                    "quotation": c.quotation,
                                    "rendered": next(
                                        (
                                            s.render_citation()
                                            for s in record.sources
                                            if s.ref == c.ref
                                        ),
                                        None,
                                    ),
                                }
                                for c in f.citations
                            ],
                        }
                        for f in record.answer.findings
                    ],
                    "missing_information": record.answer.missing_information,
                    "caveats": record.answer.caveats,
                },
                "sources": [
                    {
                        "ref": s.ref,
                        "document_id": s.document_id,
                        "document_title": s.document_title,
                        "page_number": s.page_number,
                        "clause_number": s.clause_number,
                        "edition": s.edition,
                        "is_knowledge_base": s.is_knowledge_base,
                        "rendered": s.render_citation(),
                        "excerpt": s.text[:400],
                    }
                    for s in record.sources
                ],
                "trace": record.as_observability_record()
                if context.has("org.ai.observe")
                else None,
            }
        )

    def _build_scope(self, project: Project, context, data: dict):
        """Build the retrieval scope.

        Knowledge-base retrieval is refused when the project has not declared
        its governing edition. Refused rather than defaulted, with an
        actionable message — this is the whole point of ADR 0004, surfaced at
        the API boundary where a user can act on it.
        """
        document_types = data.get("document_types") or None

        if not data["include_knowledge_base"]:
            return project_scope(
                organization_id=context.organization_id,
                project_id=project.pk,
                accessible_project_ids=context.accessible_project_ids,
                document_type_codes=document_types,
            )

        if not project.contract_edition:
            raise ValidationError(
                "This project has not declared which conditions of contract "
                "govern it, so the knowledge base cannot be searched. Set the "
                "contract edition on the project, or ask without the knowledge "
                "base.",
                details={
                    "project": str(project.pk),
                    "available_editions": [e.code for e in DEFAULT_REGISTRY.editions()],
                },
            )

        return combined_scope(
            organization_id=context.organization_id,
            project_id=project.pk,
            edition=project.contract_edition,
            accessible_project_ids=context.accessible_project_ids,
            document_type_codes=document_types,
        )


class ModelStatusView(APIView):
    """Which AI models this installation has, and which are selected."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        from django.conf import settings

        from claimiq.ai.domain.model_registry import DEFAULT_MODEL_REGISTRY
        from claimiq.ai.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider

        ai_settings = settings.AI_SETTINGS
        profile = ai_settings["HARDWARE_PROFILE"]
        base_url = ai_settings["OLLAMA_BASE_URL"]

        llm_provider = OllamaLLMProvider(base_url, 10)
        embedding_provider = OllamaEmbeddingProvider(base_url, 10)
        reachable = llm_provider.is_available()

        discovered_llm = list(llm_provider.list_models()) if reachable else []
        discovered_embedding = list(embedding_provider.list_models()) if reachable else []

        return Response(
            {
                "runtime_reachable": reachable,
                "hardware_profile": profile,
                "configured": {
                    "llm": ai_settings.get("DEFAULT_LLM_MODEL") or None,
                    "embedding": ai_settings.get("DEFAULT_EMBEDDING_MODEL") or None,
                    "reranker": ai_settings.get("DEFAULT_RERANKER_MODEL") or None,
                },
                "available": {
                    "llm": [m.name for m in discovered_llm],
                    "embedding": [m.name for m in discovered_embedding],
                },
                "recommended": {
                    role: [
                        {"name": s.name, "label": s.label(), "vram_gb": s.estimated_vram_gb}
                        for s in DEFAULT_MODEL_REGISTRY.recommendations_for(role, profile)
                    ]
                    for role in ("llm", "embedding", "reranker")
                },
            }
        )
