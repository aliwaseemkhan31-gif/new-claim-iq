"""Claim endpoints."""
from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from claimiq.accounts.domain.permissions import (
    CLAIM_ASSESS,
    CLAIM_CREATE,
    CLAIM_DELETE,
    CLAIM_EDIT,
    CLAIM_VIEW,
    DOCUMENT_UPLOAD,
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
    ContractDeadline,
    Evidence,
)
from claimiq.claims.services.timeline import build_project_chronology
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.documents.models import Document
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.projects.models import Party, Project


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

    @transaction.atomic
    def perform_update(self, serializer) -> None:
        claim = serializer.instance
        self._require(CLAIM_EDIT.code, str(claim.project_id))

        destination = serializer.validated_data.get("project")
        destination_id = getattr(destination, "pk", destination)
        if destination_id is None or destination_id == claim.project_id:
            serializer.save(updated_by=self.request.user)
            return

        self._move_to_project(claim, destination_id)
        serializer.save(updated_by=self.request.user)
        self._follow_claim_to_project(claim, destination_id)

    def _move_to_project(self, claim: Claim, destination_id) -> None:
        """Check that this claim may be put into ``destination_id``.

        Moving a claim is two acts, not one: removing it from where it is, and
        placing it where it is going. Checking only the source — which is what
        an ordinary edit check does — would let someone with edit rights on one
        project deposit a claim into a project they cannot otherwise write to.
        """
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")

        destination = Project.objects.filter(
            pk=destination_id, organization_id=context.organization_id
        ).first()
        if destination is None or destination.pk not in context.accessible_project_ids:
            raise ValidationError(
                "That project does not exist, or you cannot see it.",
                details={"field": "project"},
            )

        self._require(CLAIM_CREATE.code, str(destination.pk))

    def _follow_claim_to_project(self, claim: Claim, destination_id) -> None:
        """Move what belongs to the claim along with it.

        A claim's events and evidence each carry their own project, so a claim
        moved on its own leaves its chronology and its evidence behind in the
        project it came from — present on the claim, absent from every register
        that lists them. They move with it.

        Parties are project entities, so a claimant recorded on the old project
        does not exist on the new one. The reference is cleared rather than
        carried, because a claim naming a party from another project is a
        quieter error than one naming nobody.
        """
        ClaimEvent.objects.filter(claim=claim).update(project_id=destination_id)
        Evidence.objects.filter(claim=claim).update(project_id=destination_id)

        stale = [
            field
            for field in ("claimant", "respondent")
            if getattr(claim, f"{field}_id")
            and not Party.objects.filter(
                pk=getattr(claim, f"{field}_id"), project_id=destination_id
            ).exists()
        ]
        if stale:
            for field in stale:
                setattr(claim, f"{field}_id", None)
            claim.save(update_fields=[*stale, "updated_at"])

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
        from claimiq.claims.services.deadlines import (
            assess_claim,
            finding_payload,
            requirements_for_claim,
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

        if not requirements_for_claim(claim):
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

        findings = assess_claim(claim)
        return Response(
            {
                "claim": str(claim.id),
                "edition": edition,
                "awareness_date": (
                    claim.awareness_date.isoformat() if claim.awareness_date else None
                ),
                "findings": [finding_payload(f) for f in findings],
            }
        )

    # -- Documents filed on the claim --------------------------------------

    @action(
        detail=True,
        methods=["get", "post"],
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def files(self, request: Request, pk: str | None = None) -> Response:
        """The claim document, notices and supporting documents filed on a claim.

        POST either uploads a new file (multipart ``file`` plus
        ``document_type``) or files a document already in the project
        (``document``), with ``role`` saying where it goes and the dates and
        provision that make it count.
        """
        from claimiq.claims.services.claim_files import (
            Attachment,
            attach,
            files_payload,
            validate,
        )

        claim = self.get_object()
        if request.method == "GET":
            self._require(CLAIM_VIEW.code, str(claim.project_id))
            return Response({"results": files_payload(claim)})

        self._require(CLAIM_EDIT.code, str(claim.project_id))
        data = request.data
        role = str(data.get("role") or "")
        details = Attachment(
            role=role,
            sent_date=_parse_date(data.get("sent_date"), "sent_date"),
            received_date=_parse_date(data.get("received_date"), "received_date"),
            clause_number=str(data.get("clause_number") or ""),
            obligation=str(data.get("obligation") or ""),
            confirmed=str(data.get("confirmed", "true")).lower() in ("1", "true", "yes", "on"),
            element=str(data.get("element") or ""),
            relevance=str(data.get("relevance") or "supports"),
            note=str(data.get("note") or ""),
        )
        validate(details)

        upload = request.FILES.get("file")
        if upload is not None:
            self._require(DOCUMENT_UPLOAD.code, str(claim.project_id))
            from claimiq.documents.services.upload import upload_document

            document_type = str(data.get("document_type") or _DEFAULT_TYPE.get(role, "other"))
            result = upload_document(
                project=claim.project,
                upload=upload,
                document_type=document_type,
                user=request.user,
                title=str(data.get("title") or ""),
                reference=str(data.get("reference") or ""),
                document_date=details.sent_date,
                allow_duplicate=str(data.get("allow_duplicate", "")).lower()
                in ("1", "true", "yes", "on"),
            )
            document = result.document
        else:
            document_id = data.get("document")
            if not document_id:
                raise ValidationError(
                    "Attach a file, or choose a document already in the project.",
                    details={"field": "file"},
                )
            document = Document.objects.filter(
                pk=document_id, project_id=claim.project_id, deleted_at__isnull=True
            ).first()
            if document is None:
                raise NotFoundError("That document is not in this claim's project.")

        attach(claim, document, details, user=request.user)
        return Response({"results": files_payload(claim)}, status=201)

    # -- Claim bundles ------------------------------------------------------

    @action(
        detail=False,
        methods=["post"],
        url_path="bundle/read",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def bundle_read(self, request: Request) -> Response:
        """Upload a claim bundle and split it into its letter and annexures.

        The file is stored like any project document. The split is a proposal;
        nothing is filed on a claim until ``bundle/apply``.
        """
        from claimiq.claims.services.intake import document_summary, read_bundle, receive

        document = receive(
            request,
            document_type=str(request.data.get("document_type") or "claim"),
            permission_check=lambda project_id, permission: self._require(permission, str(project_id)),
        )
        return Response({"document": document_summary(document), **read_bundle(document)})

    @action(detail=False, methods=["post"], url_path="bundle/apply")
    def bundle_apply(self, request: Request) -> Response:
        """File a split bundle on a claim, creating the claim if asked."""
        from claimiq.claims.services.intake import apply_bundle

        context = access_for(request)
        document = Document.objects.filter(
            pk=request.data.get("document"),
            project__organization_id=getattr(context, "organization_id", None),
            deleted_at__isnull=True,
        ).select_related("project").first()
        if document is None or document.project_id not in context.accessible_project_ids:
            raise NotFoundError("That document does not exist.")
        self._require(
            CLAIM_CREATE.code if request.data.get("new_claim") else CLAIM_EDIT.code,
            str(document.project_id),
        )
        return Response(apply_bundle(document, dict(request.data), user=request.user), status=201)

    @action(detail=True, methods=["delete"], url_path=r"files/(?P<link_id>[^/.]+)")
    def file_detail(self, request: Request, pk: str | None = None, link_id: str | None = None) -> Response:
        from uuid import UUID

        from claimiq.claims.services.claim_files import detach

        claim = self.get_object()
        self._require(CLAIM_EDIT.code, str(claim.project_id))
        try:
            key = UUID(str(link_id))
        except ValueError:
            raise NotFoundError("That document is not filed on this claim.") from None
        detach(claim, key, user=request.user)
        return Response(status=204)

    # -- Checklist ---------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="checklist")
    def checklist(self, request: Request, pk: str | None = None) -> Response:
        """Is each event, notice, submission and piece of evidence on record and in time?"""
        from claimiq.claims.services.checklist import for_claim

        claim = self.get_object()
        self._require(CLAIM_VIEW.code, str(claim.project_id))
        return Response(for_claim(claim))

    @action(detail=False, methods=["get"], url_path="checklist")
    def checklist_list(self, request: Request) -> Response:
        """The checklist for every claim the filters select, one project or all."""
        from claimiq.claims.services.checklist import for_claims

        claims = list(self.filter_queryset(self.get_queryset())[:MAX_SCREENING_ROWS])
        return Response({"count": len(claims), "results": for_claims(claims)})


#: The document type a file gets when filed without one.
_DEFAULT_TYPE = {
    "claim_submission": "claim",
    "notice": "notice",
    "supporting": "site_record",
}


def _parse_date(value, field: str):
    from datetime import date as _date

    if value in (None, ""):
        return None
    try:
        return _date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(
            "Use a date in the form YYYY-MM-DD.", details={"field": field}
        ) from None


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


class ContractDeadlineSerializer(serializers.ModelSerializer):
    source_document_title = serializers.CharField(
        source="source_document.title", read_only=True, default=None
    )
    confirmed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ContractDeadline
        fields = (
            "id", "project", "clause_number", "obligation", "action", "status",
            "title", "period_days", "day_count", "runs_from", "runs_from_clause",
            "applies_to", "is_condition_precedent", "recipient", "late_consequence",
            "note", "source_document", "source_document_title", "source_page",
            "source_excerpt", "confirmed_by_name", "confirmed_at", "created_at",
        )
        read_only_fields = ("id", "confirmed_at", "created_at")

    def get_confirmed_by_name(self, obj) -> str | None:
        user = obj.confirmed_by
        if user is None:
            return None
        return user.get_full_name() or user.email

    def validate(self, attrs):
        def current(field, default=None):
            return attrs.get(field, getattr(self.instance, field, default))

        action_value = current("action", "amend")
        period = current("period_days")
        if action_value == ContractDeadline.Action.ADD and not period:
            raise serializers.ValidationError(
                {"period_days": "Give the number of days the contract allows."}
            )
        changes_something = period or any(
            current(field) not in (None, "")
            for field in (
                "day_count", "runs_from", "applies_to", "is_condition_precedent",
                "recipient", "late_consequence",
            )
        )
        if action_value == ContractDeadline.Action.AMEND and not changes_something:
            raise serializers.ValidationError(
                {"period_days": "Say what the contract changes: the period, or another term."}
            )
        runs_from = attrs.get("runs_from", getattr(self.instance, "runs_from", ""))
        runs_from_clause = attrs.get(
            "runs_from_clause", getattr(self.instance, "runs_from_clause", "")
        )
        if runs_from == ContractDeadline.RunsFrom.NOTICE and not runs_from_clause:
            raise serializers.ValidationError(
                {"runs_from_clause": "Name the clause of the notice this period runs from."}
            )
        return attrs


class ContractDeadlineViewSet(viewsets.ModelViewSet):
    """Notice periods as the project's own contract sets them.

    A row typed by a person is confirmed as it is saved. A row read from a
    document arrives as a suggestion, and applies only once confirmed.
    """

    serializer_class = ContractDeadlineSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "status", "clause_number"]
    ordering = ["clause_number", "obligation", "created_at"]

    def get_queryset(self) -> QuerySet[ContractDeadline]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return ContractDeadline.objects.none()
        return ContractDeadline.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
        ).select_related("source_document", "confirmed_by")

    def _require(self, permission: str, project_id) -> None:
        context = access_for(self.request, str(project_id))
        if context is None or not context.has(permission):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission},
            )

    def _project(self, project_id) -> Project:
        context = access_for(self.request)
        project = Project.objects.filter(
            pk=project_id, organization_id=getattr(context, "organization_id", None)
        ).first()
        if project is None or project.pk not in context.accessible_project_ids:
            raise NotFoundError("The requested project does not exist.")
        return project

    def perform_create(self, serializer) -> None:
        project = serializer.validated_data.get("project")
        if project is None:
            raise ValidationError("A 'project' is required.", details={"field": "project"})
        self._require(CLAIM_EDIT.code, project.pk)
        serializer.save(
            created_by=self.request.user,
            status=ContractDeadline.Status.CONFIRMED,
            confirmed_by=self.request.user,
            confirmed_at=timezone.now(),
        )

    def perform_update(self, serializer) -> None:
        instance = serializer.instance
        new_project = serializer.validated_data.get("project")
        if new_project is not None and new_project.pk != instance.project_id:
            raise ValidationError("A contract deadline cannot be moved to another project.")
        self._require(CLAIM_EDIT.code, instance.project_id)
        extra = {}
        status_value = serializer.validated_data.get("status")
        if status_value == ContractDeadline.Status.CONFIRMED and (
            instance.status != ContractDeadline.Status.CONFIRMED
        ):
            extra = {"confirmed_by": self.request.user, "confirmed_at": timezone.now()}
        elif status_value and status_value != ContractDeadline.Status.CONFIRMED:
            extra = {"confirmed_by": None, "confirmed_at": None}
        serializer.save(updated_by=self.request.user, **extra)

    def perform_destroy(self, instance: ContractDeadline) -> None:
        self._require(CLAIM_EDIT.code, instance.project_id)
        instance.delete()

    @action(detail=False, methods=["get"])
    def effective(self, request: Request) -> Response:
        """The deadlines the project's claims face, standard and amended."""
        from claimiq.claims.services.contract_deadlines import effective_payload

        project = self._project(request.query_params.get("project"))
        self._require(CLAIM_VIEW.code, project.pk)
        return Response(
            {
                "project": str(project.pk),
                "edition": project.contract_edition or "",
                "results": effective_payload(project),
            }
        )

    @action(detail=False, methods=["post"])
    def scan(self, request: Request) -> Response:
        """Read the project's contract documents for amended periods."""
        from claimiq.claims.services.contract_deadlines import scan

        project = self._project(request.data.get("project"))
        self._require(CLAIM_EDIT.code, project.pk)
        return Response(scan(project, user=request.user))
