"""Claim analysis endpoints.

Served at the paths the SPA already calls:

- ``POST /api/v1/ai/analysis/``              start (or return the active run)
- ``GET  /api/v1/ai/analysis/``              list, filterable by ``claim``
- ``GET  /api/v1/ai/analysis/{id}/``         one run, with strands and findings
- ``POST /api/v1/ai/analysis/{id}/cancel/``  stop before the next strand
- ``POST /api/v1/ai/findings/{id}/review/``  accept, reject or amend a finding
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import (
    AI_ANALYSE,
    AI_OVERRIDE,
    CLAIM_VIEW,
    ORG_VIEW_AI_OBSERVABILITY,
)
from claimiq.analysis.domain.strands import STRAND_LABELS, Strand
from claimiq.analysis.models import AIFinding, AnalysisStrandResult, ClaimAnalysis, FindingReview
from claimiq.analysis.services.claim_analysis import (
    ADVISORY,
    dispatch_analysis,
    is_stalled,
    request_cancellation,
    review_finding,
    review_outcome,
    start_analysis,
)
from claimiq.claims.models import Claim
from claimiq.core.api.pagination import StandardPagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError


def _uuid(value: Any, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        raise ValidationError(f"'{name}' must be a UUID.", details={"field": name}) from None


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _finding_payload(finding: AIFinding, reviews: list[FindingReview]) -> dict[str, Any]:
    outcome = review_outcome(finding, reviews)
    latest = reviews[-1] if reviews else None
    return {
        "id": str(finding.pk),
        "sequence": finding.sequence,
        # The AI's words, always. A reviewer's amendment is shown alongside,
        # never in place of it (ADR 0006).
        "statement": finding.statement,
        "effective_statement": outcome.effective_statement,
        "epistemic_status": finding.epistemic_status,
        "citations": finding.citations,
        "review": {
            "state": outcome.state.value,
            "history_length": outcome.history_length,
            "latest": (
                {
                    "action": latest.action,
                    "reason": latest.reason,
                    "amended_statement": latest.amended_statement,
                    "reviewer_email": latest.reviewer_email,
                    "reviewed_at": _iso(latest.created_at),
                }
                if latest
                else None
            ),
        },
    }


def _strand_payload(
    row: AnalysisStrandResult,
    findings: list[AIFinding],
    reviews_by_finding: dict[Any, list[FindingReview]],
    *,
    can_observe: bool,
) -> dict[str, Any]:
    try:
        label = STRAND_LABELS[Strand(row.strand)]
    except ValueError:
        label = row.strand
    return {
        "strand": row.strand,
        "label": label,
        "status": row.status,
        "uses_model": row.uses_model,
        "skip_reason": row.skip_reason,
        "summary": row.summary,
        "insufficient_evidence": row.insufficient_evidence,
        "confidence": row.confidence or None,
        "confidence_reasons": row.confidence_reasons,
        "computed": row.computed,
        "error_code": row.error_code or None,
        "error_message": row.error_message or None,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "findings": [
            _finding_payload(f, reviews_by_finding.get(f.pk, [])) for f in findings
        ],
        # Prompts, retrieval traces and source lists are internal detail,
        # visible to those permitted to inspect AI behaviour.
        "prompt_identifier": row.prompt_identifier if can_observe else None,
        "retrieval_query": row.retrieval_query if can_observe else None,
        "observability": row.observability if can_observe else None,
    }


def _analysis_summary(analysis: ClaimAnalysis) -> dict[str, Any]:
    claim = analysis.claim
    return {
        "id": str(analysis.pk),
        "claim": str(analysis.claim_id),
        "claim_reference": claim.reference,
        "claim_title": claim.title,
        "project": str(analysis.project_id),
        "status": analysis.status,
        "progress_percent": analysis.progress_percent,
        "edition_code": analysis.edition_code,
        "llm_model": analysis.llm_model,
        "cancel_requested": analysis.cancel_requested,
        "error_code": analysis.error_code or None,
        "error_message": analysis.error_message or None,
        "created_at": _iso(analysis.created_at),
        "started_at": _iso(analysis.started_at),
        "finished_at": _iso(analysis.finished_at),
    }


def _analysis_detail(analysis: ClaimAnalysis, *, can_observe: bool) -> dict[str, Any]:
    rows = list(analysis.strands.order_by("sequence"))
    findings = list(AIFinding.objects.filter(analysis=analysis).order_by("sequence"))
    reviews = FindingReview.objects.filter(finding__analysis=analysis).order_by("created_at")

    findings_by_row: dict[Any, list[AIFinding]] = defaultdict(list)
    for finding in findings:
        findings_by_row[finding.strand_result_id].append(finding)
    reviews_by_finding: dict[Any, list[FindingReview]] = defaultdict(list)
    for review in reviews:
        reviews_by_finding[review.finding_id].append(review)

    return {
        **_analysis_summary(analysis),
        "advisory": ADVISORY,
        "claim_snapshot": analysis.claim_snapshot,
        "strands": [
            _strand_payload(
                row, findings_by_row.get(row.pk, []), reviews_by_finding, can_observe=can_observe
            )
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class StartAnalysisSerializer(serializers.Serializer):
    claim = serializers.UUIDField()
    project = serializers.UUIDField(required=False, allow_null=True)
    analysis_type = serializers.ChoiceField(
        choices=["full"], required=False, allow_null=True, default="full"
    )


class ReviewSerializer(serializers.Serializer):
    action = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    amended_statement = serializers.CharField(required=False, allow_blank=True, default="")


class AnalysisViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        # Only starting a run is rate-limited. Listing and retrieving are how
        # the UI polls a run in progress, and throttling those would stall the
        # progress display long before it protected anything.
        if getattr(self, "action", None) == "create":
            self.throttle_scope = "ai"
        return super().get_throttles()

    def _queryset(self, request: Request):
        context = access_for(request)
        if context is None or context.organization_id is None:
            return ClaimAnalysis.objects.none()
        return ClaimAnalysis.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
        ).select_related("claim", "project")

    def _get(self, request: Request, pk) -> ClaimAnalysis:
        analysis = self._queryset(request).filter(pk=_uuid(pk, "id")).first()
        if analysis is None:
            raise NotFoundError("The requested analysis does not exist.")
        return analysis

    def _require(self, request: Request, project_id, permission: str):
        context = access_for(request, str(project_id))
        if context is None or not context.has(permission):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission},
            )
        return context

    def list(self, request: Request) -> Response:
        queryset = self._queryset(request).order_by("-created_at")
        if claim := request.query_params.get("claim"):
            queryset = queryset.filter(claim_id=_uuid(claim, "claim"))
        if run_status := request.query_params.get("status"):
            queryset = queryset.filter(status=run_status)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response([_analysis_summary(a) for a in page])

    def retrieve(self, request: Request, pk=None) -> Response:
        analysis = self._get(request, pk)
        context = self._require(request, analysis.project_id, CLAIM_VIEW.code)
        return Response(
            _analysis_detail(analysis, can_observe=context.has(ORG_VIEW_AI_OBSERVABILITY.code))
        )

    def create(self, request: Request) -> Response:
        serializer = StartAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        context = access_for(request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")

        claim = (
            Claim.objects.select_related("project", "claimant", "respondent")
            .filter(
                pk=data["claim"],
                project__organization_id=context.organization_id,
                project_id__in=context.accessible_project_ids,
            )
            .first()
        )
        if claim is None:
            raise NotFoundError("The requested claim does not exist.")
        if data.get("project") and data["project"] != claim.project_id:
            raise ValidationError(
                "The claim does not belong to the given project.",
                details={"field": "project"},
            )

        project_context = self._require(request, claim.project_id, AI_ANALYSE.code)

        from claimiq.ai.services.configuration import resolve_ai_settings

        # Resolved, not read from settings: the run records which models it
        # used, and that record must name what the engine will actually call.
        ai_settings = resolve_ai_settings()

        analysis, created = start_analysis(
            claim,
            user=request.user,
            llm_model=ai_settings.get("DEFAULT_LLM_MODEL") or "",
            embedding_model=ai_settings.get("DEFAULT_EMBEDDING_MODEL") or "",
        )
        dispatched = created or is_stalled(analysis)
        if dispatched:
            transaction.on_commit(lambda: dispatch_analysis(analysis))

        return Response(
            _analysis_detail(
                analysis, can_observe=project_context.has(ORG_VIEW_AI_OBSERVABILITY.code)
            ),
            status=status.HTTP_202_ACCEPTED if dispatched else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk=None) -> Response:
        analysis = self._get(request, pk)
        context = self._require(request, analysis.project_id, AI_ANALYSE.code)
        analysis = request_cancellation(analysis)
        return Response(
            _analysis_detail(analysis, can_observe=context.has(ORG_VIEW_AI_OBSERVABILITY.code))
        )


class FindingReviewView(APIView):
    """Accept, reject or amend an AI finding. Requires ``ai.override``."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, finding_id) -> Response:
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        context = access_for(request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")

        finding = (
            AIFinding.objects.select_related("analysis")
            .filter(
                pk=_uuid(finding_id, "finding_id"),
                analysis__project__organization_id=context.organization_id,
                analysis__project_id__in=context.accessible_project_ids,
            )
            .first()
        )
        if finding is None:
            raise NotFoundError("The requested finding does not exist.")

        project_context = access_for(request, str(finding.analysis.project_id))
        if project_context is None or not project_context.has(AI_OVERRIDE.code):
            raise PermissionDeniedError(
                "You do not have permission to review AI findings on this project.",
                details={"required_permission": AI_OVERRIDE.code},
            )

        review_finding(
            finding,
            user=request.user,
            action=data["action"],
            reason=data["reason"],
            amended_statement=data["amended_statement"],
        )
        reviews = list(finding.reviews.order_by("created_at"))
        return Response(_finding_payload(finding, reviews), status=status.HTTP_201_CREATED)
