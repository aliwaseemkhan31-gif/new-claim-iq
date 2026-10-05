"""AI endpoints."""
from __future__ import annotations

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import AI_QUERY
from claimiq.accounts.services.access import holds_in_any_project
from claimiq.ai.domain.citations import resolve_ref
from claimiq.ai.models import AIQuestion, AIQuestionStatus
from claimiq.ai.services.answering import AnswerRecord, build_default_answering_service
from claimiq.core.api.pagination import StandardPagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import (
    ClaimIQError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from claimiq.core.logging import get_logger
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.projects.models import Project
from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus
from claimiq.search.domain.scope import combined_scope, knowledge_base_scope, project_scope

logger = get_logger("ai.api")


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=3, max_length=4000)
    project = serializers.UUIDField(required=False, allow_null=True)
    #: Which standard form to read, when the question names no project. Never
    #: defaulted — ADR 0004 — so a question is always answered from the edition
    #: the asker chose.
    edition = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    include_knowledge_base = serializers.BooleanField(default=True)
    document_types = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("project") and not attrs.get("edition"):
            raise serializers.ValidationError(
                {
                    "project": (
                        "Name either a project to ask about, or the standard-form "
                        "edition to ask about on its own."
                    )
                }
            )
        return attrs


class AskView(APIView):
    """Answer a question, grounded in a project's documents or in a standard form.

    Two shapes of question, deliberately the same endpoint. *About a project*
    reads that project's documents, optionally alongside the form that governs
    it. *About a standard form alone* reads only the published edition named —
    what the Red Book requires, independent of any job. The second has no
    project, so it is confined to the organization by the row's own tenant
    column rather than through a project.

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

        project_id = data.get("project")
        context = access_for(request, project_id)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")

        if project_id:
            context.require(AI_QUERY.code)
            project = Project.objects.filter(
                pk=project_id, organization_id=context.organization_id
            ).first()
            if project is None or project.pk not in context.accessible_project_ids:
                raise NotFoundError("The requested project does not exist.")
            scope = self._build_scope(project, context, data)
            include_knowledge_base = data["include_knowledge_base"]
        else:
            project = None
            self._require_ai_anywhere(request, context)
            scope = self._standard_form_scope(context, data)
            # A question with no project reads nothing but the standard form,
            # whatever the caller sent for this flag.
            include_knowledge_base = True

        common = {
            "project": project,
            "organization_id": context.organization_id,
            "asked_by": request.user,
            "question": data["question"],
            "include_knowledge_base": include_knowledge_base,
            "edition_code": scope.knowledge_base_edition or "",
            "created_by": request.user,
        }

        service = build_default_answering_service()
        try:
            record = service.ask(data["question"], scope)
        except ClaimIQError as exc:
            # A failed question is part of the record too; it is never turned
            # into an answer.
            AIQuestion.objects.create(
                **common,
                status=AIQuestionStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

        payload = answer_payload(record)
        question = AIQuestion.objects.create(
            **common,
            status=AIQuestionStatus.ANSWERED,
            response=payload,
            model=record.model,
            prompt_identifier=record.prompt_identifier,
            latency_ms=record.latency_ms,
        )
        return Response(
            {
                "id": str(question.pk),
                "question": question.question,
                "project": str(question.project_id) if question.project_id else None,
                "edition_code": question.edition_code or None,
                "created_at": question.created_at.isoformat(),
                **payload,
                "trace": record.as_observability_record()
                if context.has("org.ai.observe")
                else None,
            }
        )

    def _require_ai_anywhere(self, request: Request, context) -> None:
        """Gate a question that names no project.

        `ai.query` is a project permission, and there is no project here to
        check it against. Someone who may put questions to the model on any of
        their projects may put one to a standard form; someone who may not,
        may not — the capability is the same and so is the cost of exercising
        it.
        """
        if holds_in_any_project(
            request.user,
            organization_id=context.organization_id,
            organization_role=context.organization_role,
            permission=AI_QUERY.code,
        ):
            return
        raise PermissionDeniedError(
            "You do not have permission to put questions to the AI.",
            details={"required_permission": AI_QUERY.code},
        )

    def _standard_form_scope(self, context, data: dict):
        """Build a scope that reads one published standard form and nothing else.

        The edition is validated against the registry and against what this
        organization has actually published. Retrieval over an edition with no
        published knowledge base returns nothing, which reads to a user as the
        model knowing nothing about the Red Book; the refusal says what is
        missing instead.
        """
        edition = (data.get("edition") or "").strip()
        available = list(
            KnowledgeBase.objects.filter(
                organization_id=context.organization_id,
                status=KnowledgeBaseStatus.PUBLISHED,
            ).values_list("edition_code", flat=True)
        )

        if not DEFAULT_REGISTRY.has_edition(edition):
            raise ValidationError(
                "That is not an edition this system knows.",
                details={
                    "edition": edition,
                    "available_editions": [e.code for e in DEFAULT_REGISTRY.editions()],
                },
            )

        if edition not in available:
            raise ValidationError(
                "No published knowledge base holds that edition, so there is "
                "nothing to read. Upload and publish the standard form first, "
                "or choose an edition that is published.",
                details={"edition": edition, "published_editions": sorted(available)},
            )

        return knowledge_base_scope(
            organization_id=context.organization_id,
            edition=edition,
            accessible_project_ids=context.accessible_project_ids,
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


LAYER_PROJECT = "project_document"
LAYER_STANDARD_FORM = "standard_form"


def _edition_label(code: str | None) -> str | None:
    if not code:
        return None
    return DEFAULT_REGISTRY.get_edition(code).label if DEFAULT_REGISTRY.has_edition(code) else code


def _source_payload(source) -> dict:
    return {
        "ref": source.ref,
        "layer": LAYER_STANDARD_FORM if source.is_knowledge_base else LAYER_PROJECT,
        "document_id": None if source.is_knowledge_base else source.document_id,
        "knowledge_base_id": source.document_id if source.is_knowledge_base else None,
        "document_title": source.document_title,
        "page_number": source.page_number,
        "clause_number": source.clause_number,
        "edition": source.edition,
        "edition_label": _edition_label(source.edition),
        "is_knowledge_base": source.is_knowledge_base,
        "rendered": source.render_citation(),
        "excerpt": source.text[:600],
    }


def answer_payload(record: AnswerRecord) -> dict:
    """The answer as the UI renders it and as it is stored in history.

    Every citation carries where its source lives — project document or
    standard-form edition, document or knowledge base, page — so the UI can
    open the passage. Citations are resolved through the same closed-world
    lookup grounding used, so a decorated reference ("SOURCE ID: S1") still
    reaches its source.
    """
    by_ref = {s.ref: s for s in record.sources}

    def citation(c) -> dict:
        ref = resolve_ref(c.ref, by_ref) or c.ref
        source = by_ref.get(ref)
        base = _source_payload(source) if source else {"ref": ref, "rendered": None}
        base.pop("excerpt", None)
        return {**base, "quotation": c.quotation}

    return {
        "answer": {
            "summary": record.answer.summary,
            "confidence": record.answer.confidence.value,
            "insufficient_evidence": record.answer.insufficient_evidence,
            "called_model": record.called_model,
            "findings": [
                {
                    "statement": f.statement,
                    "status": f.status.value,
                    "citations": [citation(c) for c in f.citations],
                }
                for f in record.answer.findings
            ],
            "missing_information": record.answer.missing_information,
            "caveats": record.answer.caveats,
        },
        "sources": [_source_payload(s) for s in record.sources],
        "cited_refs": list(record.grounding.resolved_refs),
        "model": record.model or None,
    }


def _question_project(request: Request, project_id):
    context = access_for(request, project_id)
    if context is None or context.organization_id is None:
        raise NotFoundError("The requested project does not exist.")
    context.require(AI_QUERY.code)
    return context


class QuestionListView(APIView):
    """Earlier questions, newest first.

    Scoped either to one project or to one standard-form edition. One of the
    two is required: an unscoped list would mix a project's confidential
    questions into a reading of the Red Book.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        project_id = request.query_params.get("project")
        edition = (request.query_params.get("edition") or "").strip()

        if project_id:
            context = _question_project(request, project_id)
            queryset = AIQuestion.objects.filter(
                project_id=project_id, project__organization_id=context.organization_id
            )
        elif edition:
            context = access_for(request)
            if context is None or context.organization_id is None:
                raise PermissionDeniedError(
                    "You are not a member of an active organization."
                )
            # Standard-form questions only: a project question that happened to
            # read the same edition belongs to its project's history, not here.
            queryset = AIQuestion.objects.filter(
                project__isnull=True,
                edition_code=edition,
                organization_id=context.organization_id,
            )
        else:
            raise ValidationError(
                "A 'project' or an 'edition' is required.",
                details={"field": "project"},
            )

        queryset = queryset.select_related("asked_by")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            [
                {
                    "id": str(q.pk),
                    "question": q.question,
                    "status": q.status,
                    "edition_code": q.edition_code or None,
                    "asked_by": q.asked_by.email if q.asked_by_id else None,
                    "created_at": q.created_at.isoformat(),
                    "summary": (q.response.get("answer") or {}).get("summary"),
                    "confidence": (q.response.get("answer") or {}).get("confidence"),
                    "insufficient_evidence": (q.response.get("answer") or {}).get(
                        "insufficient_evidence"
                    ),
                    "error_message": q.error_message or None,
                }
                for q in page
            ]
        )


class QuestionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, pk) -> Response:
        question = AIQuestion.objects.select_related("asked_by", "project").filter(pk=pk).first()
        if question is None:
            raise NotFoundError("The requested question does not exist.")

        if question.project_id:
            context = _question_project(request, question.project_id)
            if question.project.organization_id != context.organization_id:
                raise NotFoundError("The requested question does not exist.")
        else:
            # No project to check against, so the row's own tenant column is
            # the boundary. Reading a standard form is open to members.
            context = access_for(request)
            if (
                context is None
                or context.organization_id is None
                or question.organization_id != context.organization_id
            ):
                raise NotFoundError("The requested question does not exist.")

        return Response(
            {
                "id": str(question.pk),
                "project": str(question.project_id) if question.project_id else None,
                "question": question.question,
                "status": question.status,
                "asked_by": question.asked_by.email if question.asked_by_id else None,
                "created_at": question.created_at.isoformat(),
                "include_knowledge_base": question.include_knowledge_base,
                "edition_code": question.edition_code or None,
                "error_code": question.error_code or None,
                "error_message": question.error_message or None,
                **question.response,
            }
        )


class ModelStatusView(APIView):
    """Which AI models this installation has, and which are selected."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        from claimiq.ai.domain.model_registry import DEFAULT_MODEL_REGISTRY
        from claimiq.ai.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
        from claimiq.ai.services.configuration import resolve_ai_settings

        ai_settings = resolve_ai_settings()
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
