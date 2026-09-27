"""Claim endpoints."""
from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from claimiq.accounts.domain.permissions import (
    CLAIM_ASSESS,
    CLAIM_CREATE,
    CLAIM_DELETE,
    CLAIM_EDIT,
    CLAIM_VIEW,
    EVIDENCE_MANAGE,
)
from claimiq.claims.domain.chronology import EntryKind
from claimiq.claims.domain.evidence_gaps import (
    EvidenceItem,
    Relevance,
    analyse_gaps,
    element_code_for_issue_category,
)
from claimiq.claims.models import (
    AssessmentOutcome,
    Claim,
    ClaimEvent,
    ClaimIssue,
    Evidence,
)
from claimiq.claims.services.timeline import build_project_chronology
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY


class ClaimEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimEvent
        fields = (
            "id", "claim", "event_type", "occurred_on", "occurred_at_precision",
            "title", "description", "party", "clause_references",
            "source_document", "source_page", "is_ai_extracted", "confidence",
            "is_confirmed",
        )
        read_only_fields = ("id", "is_ai_extracted", "confidence")


class ClaimIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimIssue
        fields = (
            "id", "category", "title", "description", "sequence",
            "human_outcome", "human_assessment", "assessed_at",
        )
        read_only_fields = ("id", "assessed_at")


class EvidenceSerializer(serializers.ModelSerializer):
    claim_reference = serializers.CharField(source="claim.reference", read_only=True, default=None)
    claim_title = serializers.CharField(source="claim.title", read_only=True, default=None)
    document_title = serializers.CharField(source="document.title", read_only=True, default=None)

    class Meta:
        model = Evidence
        fields = (
            "id", "claim", "claim_reference", "claim_title", "issue", "title",
            "description", "document", "document_title", "page_number", "excerpt",
            "relevance", "weight", "is_ai_suggested", "reviewed_at",
        )
        read_only_fields = ("id", "is_ai_suggested", "reviewed_at")


class ClaimSerializer(serializers.ModelSerializer):
    issues = ClaimIssueSerializer(many=True, read_only=True)
    evidence_count = serializers.IntegerField(read_only=True, default=0)
    has_awareness_date = serializers.BooleanField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    claimant_name = serializers.CharField(source="claimant.name", read_only=True, default=None)
    respondent_name = serializers.CharField(source="respondent.name", read_only=True, default=None)
    # The code is the API's contract; the label is what a reader should see.
    # Without it every screen renders "eot".
    claim_type_label = serializers.CharField(source="get_claim_type_display", read_only=True)

    class Meta:
        model = Claim
        fields = (
            "id", "project", "project_name", "reference", "title", "description",
            "claimant_name", "respondent_name",
            "claim_type", "claim_type_label", "status", "claimant", "respondent",
            "event_date", "awareness_date", "notice_date", "submission_date",
            "determination_date", "amount_claimed", "amount_assessed",
            "currency", "time_claimed_days", "time_awarded_days",
            "contractual_basis", "human_outcome", "human_assessment",
            "assessed_at", "has_awareness_date", "issues", "evidence_count",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "assessed_at", "created_at", "updated_at")

    def validate_contractual_basis(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("contractual_basis must be a list of clause numbers.")
        return [str(v).strip() for v in value if str(v).strip()]


#: Ceiling on a bulk screening request. Generous against a register page,
#: and a bound on what one request can cost.
MAX_SCREENING_ROWS = 200


class ClaimViewSet(viewsets.ModelViewSet):
    """Claims within projects the user may read."""

    serializer_class = ClaimSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "claim_type", "status", "human_outcome"]
    ordering_fields = ["created_at", "event_date", "amount_claimed", "reference"]
    ordering = ["-created_at"]
    search_fields = ["title", "reference", "description"]

    def get_queryset(self) -> QuerySet[Claim]:
        from django.db.models import Count, Q

        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Claim.objects.none()
        return (
            Claim.objects.filter(
                project__organization_id=context.organization_id,
                project_id__in=context.accessible_project_ids,
            )
            .annotate(
                evidence_count=Count(
                    "evidence", filter=Q(evidence__deleted_at__isnull=True), distinct=True
                )
            )
            .prefetch_related("issues")
        )

    def _require(self, permission: str, project_id) -> None:
        context = access_for(self.request, project_id)
        if context is None or not context.has(permission):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission},
            )

    def perform_create(self, serializer) -> None:
        project_id = serializer.validated_data.get("project")
        if project_id is None:
            raise ValidationError("A 'project' is required.")
        self._require(CLAIM_CREATE.code, str(getattr(project_id, "pk", project_id)))
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer) -> None:
        self._require(CLAIM_EDIT.code, str(serializer.instance.project_id))
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Claim) -> None:
        self._require(CLAIM_DELETE.code, str(instance.project_id))
        instance.soft_delete(user=self.request.user)

    @action(detail=True, methods=["post"])
    def assess(self, request: Request, pk: str | None = None) -> Response:
        """Record a human assessment.

        Written to the human columns, which are separate from any AI finding
        and never overwrite one (ADR 0006).
        """
        claim = self.get_object()
        self._require(CLAIM_ASSESS.code, str(claim.project_id))

        outcome = request.data.get("outcome")
        valid = {c.value for c in AssessmentOutcome}
        if outcome not in valid:
            raise ValidationError(
                "An 'outcome' is required.", details={"valid": sorted(valid)}
            )

        claim.human_outcome = outcome
        claim.human_assessment = str(request.data.get("assessment", "")).strip()
        claim.assessed_by = request.user
        claim.assessed_at = timezone.now()
        claim.save(
            update_fields=[
                "human_outcome", "human_assessment", "assessed_by",
                "assessed_at", "updated_at",
            ]
        )
        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=["get", "post"])
    def issues(self, request: Request, pk: str | None = None) -> Response:
        """The claim's issues — entitlement, notice, causation, quantum… — each assessable on its own."""
        claim = self.get_object()
        if request.method == "GET":
            self._require(CLAIM_VIEW.code, str(claim.project_id))
            return Response(ClaimIssueSerializer(claim.issues.all(), many=True).data)
        self._require(CLAIM_EDIT.code, str(claim.project_id))
        serializer = ClaimIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        extra = {}
        if serializer.validated_data.get("human_outcome") or serializer.validated_data.get("human_assessment"):
            self._require(CLAIM_ASSESS.code, str(claim.project_id))
            extra = {"assessed_by": request.user, "assessed_at": timezone.now()}
        issue = serializer.save(claim=claim, created_by=request.user, **extra)
        return Response(ClaimIssueSerializer(issue).data, status=201)

    @action(detail=True, methods=["patch", "delete"], url_path=r"issues/(?P<issue_id>[^/.]+)")
    def issue_detail(self, request: Request, pk: str | None = None, issue_id: str | None = None) -> Response:
        from uuid import UUID

        claim = self.get_object()
        self._require(CLAIM_EDIT.code, str(claim.project_id))
        try:
            issue = claim.issues.filter(pk=UUID(str(issue_id))).first()
        except ValueError:
            issue = None
        if issue is None:
            raise NotFoundError("The requested issue does not exist on this claim.")
        if request.method == "DELETE":
            issue.delete()
            return Response(status=204)

        serializer = ClaimIssueSerializer(issue, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        extra = {}
        if "human_outcome" in serializer.validated_data or "human_assessment" in serializer.validated_data:
            # A determination on an issue is a human assessment, and is
            # attributed as one (ADR 0006).
            self._require(CLAIM_ASSESS.code, str(claim.project_id))
            extra = {"assessed_by": request.user, "assessed_at": timezone.now()}
        return Response(
            ClaimIssueSerializer(serializer.save(updated_by=request.user, **extra)).data
        )

    @action(detail=True, methods=["get"])
    def timeline(self, request: Request, pk: str | None = None) -> Response:
        """The claim's chronology, with conflicts and gaps surfaced."""
        claim = self.get_object()
        self._require(CLAIM_VIEW.code, str(claim.project_id))

        include_unreviewed = request.query_params.get("include_unreviewed", "true") != "false"
        kinds = None
        if raw := request.query_params.get("kinds"):
            try:
                kinds = [EntryKind(k.strip()) for k in raw.split(",") if k.strip()]
            except ValueError:
                raise ValidationError(
                    "Unknown timeline entry kind.",
                    details={"valid": [k.value for k in EntryKind]},
                ) from None

        chronology = build_project_chronology(
            project_id=claim.project_id,
            claim_id=claim.pk,
            include_unreviewed=include_unreviewed,
            kinds=kinds,
        )

        return Response(
            {
                "summary": chronology.summary(),
                "span": [d.isoformat() for d in chronology.span] if chronology.span else None,
                "unreviewed_count": chronology.unreviewed_count,
                "unsourced_count": chronology.unsourced_count,
                "entries": [_render_entry(e) for e in chronology.entries],
                "undated": [_render_entry(e) for e in chronology.undated],
                # Surfaced, not resolved. The disagreement is the finding.
                "conflicts": [
                    {
                        "title": c.title,
                        "dates": [d.isoformat() for d in c.dates],
                        "description": c.describe(),
                        "entry_ids": [e.entry_id for e in c.entries],
                    }
                    for c in chronology.conflicts
                ],
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="draft-from-document",
        parser_classes=[MultiPartParser, FormParser],
    )
    def draft_from_document(self, request: Request) -> Response:
        """Read a photographed or scanned claim and propose a claim from it.

        **Writes nothing.** The response is a draft for the person who uploaded
        it to correct and accept; the claim exists only when they save it
        through the ordinary create endpoint. This is the one place a model's
        output would otherwise become the record, so the review is the control.

        Synchronous: OCR plus one model call, on a page someone is waiting in
        front of. Minutes on a CPU-only host.
        """
        from claimiq.claims.services.drafting import draft_from_upload

        project_id = request.data.get("project")
        if not project_id:
            raise ValidationError("A 'project' is required.")
        self._require(CLAIM_CREATE.code, project_id)

        uploads = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not uploads:
            raise ValidationError("Attach the claim document to read.")

        result = draft_from_upload(uploads)
        result["project"] = str(project_id)
        return Response(result)

    @action(detail=False, methods=["get"], url_path="screening")
    def screening_summary(self, request: Request) -> Response:
        """Screening outcomes for a page of claims, for a register.

        A bounded number of queries however many claims are asked for.
        Screening each row on its own costs about seven queries and 120ms,
        which is three seconds and 170 queries to render twenty-five rows.
        """
        from claimiq.claims.services.screening import screen_many, summary_payload

        claims = list(self.filter_queryset(self.get_queryset())[:MAX_SCREENING_ROWS])
        reports = screen_many(claims)
        return Response(
            {
                "count": len(reports),
                "results": {
                    claim_id: summary_payload(report)
                    for claim_id, report in reports.items()
                },
            }
        )

    @action(detail=True, methods=["get"])
    def screening(self, request: Request, pk: str | None = None) -> Response:
        """Preliminary screening: is this claim in a fit state to work on?

        Reads the claim as recorded — no evidence assessment, no model. It runs
        the moment a claim is entered, which is the point: the evidence-gap
        engine cannot distinguish a claim nobody has worked yet from one that
        cannot be established, and this can.

        Computed on demand. A stored result goes stale the moment the missing
        date is recorded, and a stale "not assessable" is worse than none.
        """
        from claimiq.claims.services.screening import payload, screen

        claim = self.get_object()
        self._require(CLAIM_VIEW.code, str(claim.project_id))
        report = payload(screen(claim))
        report["claim"] = str(claim.id)
        return Response(report)

    @action(detail=True, methods=["get"], url_path="evidence-gaps")
    def evidence_gaps(self, request: Request, pk: str | None = None) -> Response:
        """What the claim must establish, and what the record does not.

        Derived from declared required elements matched against evidence on
        record — not asked of a model, which would invite invented gaps.
        """
        claim = self.get_object()
        self._require(CLAIM_VIEW.code, str(claim.project_id))

        items = [
            EvidenceItem(
                evidence_id=str(e.id),
                title=e.title,
                element_code=element_code_for_issue_category(
                    e.issue.category if e.issue else None
                ),
                relevance=_relevance(e.relevance),
                weight=e.weight,
                document_type=(e.document.document_type if e.document else None),
                is_reviewed=e.is_reviewed,
            )
            for e in claim.evidence.select_related("issue", "document")
        ]

        report = analyse_gaps(claim.claim_type, items)
        return Response(
            {
                "claim": str(claim.id),
                "claim_type": claim.claim_type,
                "summary": report.summary(),
                "is_complete": report.is_complete,
                "completeness_ratio": round(report.completeness_ratio, 3),
                "elements": [
                    {
                        "code": a.element.code,
                        "label": a.element.label,
                        "description": a.element.description,
                        "is_essential": a.element.is_essential,
                        "status": a.status.value,
                        "is_gap": a.is_gap,
                        "suggestion": a.suggestion(),
                        "supporting": len(a.supporting),
                        "contradicting": len(a.contradicting),
                        "unreviewed": len(a.unreviewed),
                    }
                    for a in report.assessments
                ],
                "unmatched_evidence": [
                    {"id": e.evidence_id, "title": e.title} for e in report.unmatched_evidence
                ],
            }
        )

    @action(detail=True, methods=["get"], url_path="notice-compliance")
    def notice_compliance(self, request: Request, pk: str | None = None) -> Response:
        """Assess notice timing for this claim.

        The arithmetic is deterministic and computed here, not by a model. An
        unknown awareness date yields INDETERMINATE rather than a guess — that
        date is the disputed fact in most notice arguments.
        """
        from claimiq.claims.domain.notice_compliance import (
            NoticeEvent,
            assess_notice,
            recipient_label,
            requirements_for_edition,
        )

        claim = self.get_object()
        self._require(CLAIM_VIEW.code, str(claim.project_id))

        edition = claim.project.contract_edition
        if not edition:
            raise ValidationError(
                "This project has not declared which conditions of contract "
                "govern it, so notice periods cannot be determined.",
                details={
                    "project": str(claim.project_id),
                    "available_editions": [e.code for e in DEFAULT_REGISTRY.editions()],
                },
            )

        requirements = requirements_for_edition(edition)
        if not requirements:
            return Response(
                {
                    "claim": str(claim.id),
                    "edition": edition,
                    "findings": [],
                    "note": (
                        f"No notice requirements are registered for edition "
                        f"{edition!r}. Requirements from another edition are "
                        f"deliberately not substituted."
                    ),
                }
            )

        notices = [
            NoticeEvent(
                document_id=str(n.correspondence.document_id or n.correspondence_id),
                document_title=n.correspondence.subject or "Notice",
                sent_date=n.correspondence.sent_date,
                received_date=n.correspondence.received_date,
                recipient=(
                    recipient_label(
                        n.correspondence.recipient.name, n.correspondence.recipient.role
                    )
                    if n.correspondence.recipient
                    else n.correspondence.recipient_raw
                ),
                is_confirmed_notice=n.is_confirmed_notice,
                clause_number=n.clause_number,
            )
            for n in claim.notices.select_related(
                "correspondence__recipient", "correspondence__document"
            )
        ]

        findings = [
            assess_notice(
                requirement, awareness_date=claim.awareness_date, notices=notices
            )
            for requirement in requirements
        ]

        return Response(
            {
                "claim": str(claim.id),
                "edition": edition,
                "awareness_date": (
                    claim.awareness_date.isoformat() if claim.awareness_date else None
                ),
                "findings": [
                    {
                        "clause_number": f.requirement.clause_number,
                        "description": f.requirement.description,
                        "status": f.status.value,
                        "summary": f.summary(),
                        "deadline": f.deadline.isoformat() if f.deadline else None,
                        "days_used": f.days_used,
                        "days_late": f.days_late,
                        "is_time_barred": f.is_time_barred,
                        "is_condition_precedent": f.requirement.is_condition_precedent,
                        "assumptions": f.assumptions,
                        "warnings": f.warnings,
                    }
                    for f in findings
                ],
            }
        )


def _relevance(value: str) -> Relevance:
    try:
        return Relevance(value)
    except ValueError:
        return Relevance.UNASSESSED


def _render_entry(entry) -> dict:
    return {
        "id": entry.entry_id,
        "kind": entry.kind.value,
        "date": entry.occurred_on.isoformat() if entry.occurred_on else None,
        "date_display": entry.render_date(),
        "precision": entry.precision.value,
        "title": entry.title,
        "description": entry.description,
        "party": entry.party,
        "clause_references": list(entry.clause_references),
        "source_document_id": entry.source_document_id,
        "source_document_title": entry.source_document_title,
        "source_page": entry.source_page,
        "claim_id": entry.claim_id,
        "needs_review": entry.needs_review,
        "has_provenance": entry.has_provenance,
    }


class EvidenceViewSet(viewsets.ModelViewSet):
    """Evidence within projects the user may read."""

    serializer_class = EvidenceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["claim", "issue", "relevance", "project"]
    ordering = ["-created_at"]

    def get_queryset(self) -> QuerySet[Evidence]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Evidence.objects.none()
        return Evidence.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
        ).select_related("claim", "issue", "document")

    def perform_create(self, serializer) -> None:
        claim = serializer.validated_data.get("claim")
        project_id = str(claim.project_id) if claim else serializer.validated_data.get("project")
        context = access_for(self.request, project_id)
        if context is None or not context.has(EVIDENCE_MANAGE.code):
            raise PermissionDeniedError("Not permitted.")
        serializer.save(
            created_by=self.request.user,
            project_id=claim.project_id if claim else project_id,
        )

    def perform_update(self, serializer) -> None:
        instance = serializer.instance
        context = access_for(self.request, str(instance.project_id))
        if context is None or not context.has(EVIDENCE_MANAGE.code):
            raise PermissionDeniedError("Not permitted.")
        # Assessing the evidence is a review, and is attributed. Re-filing it
        # under a different issue, or correcting its title, is clerical and must
        # not mark it reviewed: being reviewed is what lets supporting evidence
        # establish a required element rather than merely bear on it.
        assessed = any(
            field in serializer.validated_data
            and serializer.validated_data[field] != getattr(instance, field)
            for field in ("relevance", "weight")
        )
        review = (
            {"reviewed_by": self.request.user, "reviewed_at": timezone.now()}
            if assessed
            else {}
        )
        serializer.save(updated_by=self.request.user, **review)

    def perform_destroy(self, instance: Evidence) -> None:
        context = access_for(self.request, str(instance.project_id))
        if context is None or not context.has(EVIDENCE_MANAGE.code):
            raise PermissionDeniedError("Not permitted.")
        instance.soft_delete(user=self.request.user)


class ClaimEventViewSet(viewsets.ModelViewSet):
    """Timeline events."""

    serializer_class = ClaimEventSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["claim", "project", "event_type", "is_confirmed"]
    ordering = ["occurred_on"]

    def get_queryset(self) -> QuerySet[ClaimEvent]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return ClaimEvent.objects.none()
        return ClaimEvent.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
        ).select_related("claim", "party", "source_document")

    def perform_create(self, serializer) -> None:
        claim = serializer.validated_data.get("claim")
        project_id = str(claim.project_id) if claim else serializer.validated_data.get("project")
        context = access_for(self.request, project_id)
        if context is None or not context.has(CLAIM_EDIT.code):
            raise PermissionDeniedError("Not permitted.")
        serializer.save(
            created_by=self.request.user,
            project_id=claim.project_id if claim else project_id,
            # Created by a person, so confirmed by definition.
            is_confirmed=True,
        )

    def perform_update(self, serializer) -> None:
        context = access_for(self.request, str(serializer.instance.project_id))
        if context is None or not context.has(CLAIM_EDIT.code):
            raise PermissionDeniedError("Not permitted.")
        serializer.save(updated_by=self.request.user)
