"""Project endpoints."""
from __future__ import annotations

from django.db.models import Count, Q, QuerySet
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from claimiq.accounts.domain.permissions import (
    ORG_MANAGE_PROJECTS,
    PROJECT_EDIT,
    PROJECT_VIEW,
)
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import PermissionDeniedError, ValidationError
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.projects.models import Party, Project, ProjectMember


class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = (
            "id", "name", "role", "short_name", "is_primary",
            "contact_email", "aliases", "notes",
        )


class ProjectMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = ProjectMember
        fields = ("id", "user", "user_email", "user_name", "role", "is_active")


class ProjectSerializer(serializers.ModelSerializer):
    parties = PartySerializer(many=True, read_only=True)
    document_count = serializers.IntegerField(read_only=True, default=0)
    edition_label = serializers.SerializerMethodField()
    has_governing_edition = serializers.BooleanField(read_only=True)

    class Meta:
        model = Project
        fields = (
            "id", "name", "code", "description", "status",
            "contract_form", "contract_edition", "edition_label",
            "has_governing_edition",
            "contract_value", "currency",
            "commencement_date", "completion_date", "actual_completion_date",
            "location", "metadata", "parties", "document_count",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_edition_label(self, obj: Project) -> str | None:
        """Human-readable edition, so the UI never shows a bare code."""
        if not obj.contract_edition:
            return None
        if not DEFAULT_REGISTRY.has_edition(obj.contract_edition):
            return obj.contract_edition
        return DEFAULT_REGISTRY.get_edition(obj.contract_edition).label

    def validate_contract_edition(self, value: str) -> str:
        """Reject an unregistered edition.

        Storing one would produce a project whose knowledge-base retrieval can
        never resolve, failing later and further from the cause (ADR 0004).
        """
        if value and not DEFAULT_REGISTRY.has_edition(value):
            raise serializers.ValidationError(
                f"Unknown contract edition {value!r}. Known editions: "
                f"{', '.join(e.code for e in DEFAULT_REGISTRY.editions())}."
            )
        return value


class ProjectViewSet(viewsets.ModelViewSet):
    """Projects the requesting user may see.

    The queryset is filtered by the user's accessible project set, so an
    unauthorised id returns 404 rather than 403 — not leaking whether a project
    exists is worth the slightly less precise status.
    """

    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "contract_edition"]
    ordering_fields = ["name", "created_at", "updated_at", "commencement_date"]
    ordering = ["-updated_at"]
    search_fields = ["name", "code", "location"]

    def get_queryset(self) -> QuerySet[Project]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Project.objects.none()
        return (
            Project.objects.filter(
                organization_id=context.organization_id,
                id__in=context.accessible_project_ids,
            )
            .annotate(
                document_count=Count(
                    "documents", filter=Q(documents__deleted_at__isnull=True), distinct=True
                )
            )
            .prefetch_related("parties")
        )

    def perform_create(self, serializer) -> None:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")
        context.require(ORG_MANAGE_PROJECTS.code)
        project = serializer.save(
            organization_id=context.organization_id, created_by=self.request.user
        )
        # The creator is otherwise not a member and would immediately lose
        # sight of the project they just made.
        from claimiq.accounts.domain.permissions import ROLE_PROJECT_MANAGER
        from claimiq.accounts.services.access import invalidate_access

        ProjectMember.objects.create(
            project=project,
            user=self.request.user,
            role=ROLE_PROJECT_MANAGER.code,
            created_by=self.request.user,
        )
        invalidate_access(self.request.user.pk)

    def perform_update(self, serializer) -> None:
        context = access_for(self.request, self.kwargs.get("pk"))
        if context is None:
            raise PermissionDeniedError("Not permitted.")
        context.require(PROJECT_EDIT.code)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Project) -> None:
        context = access_for(self.request, str(instance.pk))
        if context is None:
            raise PermissionDeniedError("Not permitted.")
        context.require(ORG_MANAGE_PROJECTS.code)
        instance.soft_delete(user=self.request.user)

    @action(detail=True, methods=["get"])
    def summary(self, request: Request, pk: str | None = None) -> Response:
        """Counts for the project workspace overview."""
        project = self.get_object()
        context = access_for(request, pk)
        if context is None or not context.has(PROJECT_VIEW.code):
            raise PermissionDeniedError("Not permitted.")

        from claimiq.documents.models import Document, DocumentVersion, ProcessingStatus

        from claimiq.analysis.models import AIFinding, ClaimAnalysis
        from claimiq.claims.models import Claim, ClaimEvent, Evidence
        from claimiq.correspondence.models import Correspondence, Notice
        from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus

        documents = Document.objects.filter(project=project)
        kb = (
            KnowledgeBase.objects.filter(
                organization_id=project.organization_id, edition_code=project.contract_edition
            ).first()
            if project.contract_edition
            else None
        )
        return Response(
            {
                "project": str(project.id),
                "claims": Claim.objects.filter(project=project).count(),
                "correspondence": Correspondence.objects.filter(project=project).count(),
                "notices": Notice.objects.filter(
                    project=project, correspondence__deleted_at__isnull=True
                ).count(),
                "evidence": Evidence.objects.filter(project=project).count(),
                "events": ClaimEvent.objects.filter(project=project).count(),
                "analyses": ClaimAnalysis.objects.filter(project=project).count(),
                "findings_unreviewed": AIFinding.objects.filter(
                    analysis__project=project, reviews__isnull=True
                ).count(),
                # Whether standard-form text for this project's edition can be
                # retrieved at all — the question every AI answer depends on.
                "knowledge_base": (
                    {
                        "id": str(kb.pk),
                        "status": kb.status,
                        "is_retrievable": kb.status == KnowledgeBaseStatus.PUBLISHED,
                    }
                    if kb
                    else None
                ),
                "documents": documents.count(),
                "documents_processing": DocumentVersion.objects.filter(
                    document__project=project,
                    processing_status__in=[
                        ProcessingStatus.QUEUED,
                        ProcessingStatus.PROCESSING,
                    ],
                ).count(),
                "documents_failed": DocumentVersion.objects.filter(
                    document__project=project, processing_status=ProcessingStatus.FAILED
                ).count(),
                "parties": project.parties.count(),
                "members": project.members.filter(is_active=True).count(),
                "has_governing_edition": project.has_governing_edition,
            }
        )

    @action(detail=True, methods=["get", "post"])
    def members(self, request: Request, pk: str | None = None) -> Response:
        """Project members. Adding one requires ``project.members.manage``."""
        project = self.get_object()
        context = access_for(request, pk)
        if request.method == "GET":
            if context is None or not context.has(PROJECT_VIEW.code):
                raise PermissionDeniedError("Not permitted.")
            queryset = project.members.select_related("user").filter(is_active=True)
            return Response(ProjectMemberSerializer(queryset, many=True).data)

        from claimiq.accounts.domain.permissions import PROJECT_MANAGE_MEMBERS, PROJECT_ROLES
        from claimiq.accounts.models import OrganizationMembership
        from claimiq.accounts.services.access import invalidate_access

        if context is None or not context.has(PROJECT_MANAGE_MEMBERS.code):
            raise PermissionDeniedError(
                "You do not have permission to manage this project's members.",
                details={"required_permission": PROJECT_MANAGE_MEMBERS.code},
            )
        role = str(request.data.get("role") or "")
        valid_roles = {r.code for r in PROJECT_ROLES}
        if role not in valid_roles:
            raise ValidationError("Unknown project role.", details={"field": "role", "valid": sorted(valid_roles)})
        user_id = request.data.get("user")
        membership = (
            OrganizationMembership.objects.select_related("user")
            .filter(organization_id=project.organization_id, user_id=user_id, is_active=True)
            .first()
            if user_id
            else None
        )
        if membership is None:
            raise ValidationError(
                "The user must be an active member of the organization.", details={"field": "user"}
            )
        member, _created = ProjectMember.objects.update_or_create(
            project=project,
            user=membership.user,
            defaults={"role": role, "is_active": True, "updated_by": request.user},
        )
        invalidate_access(membership.user.pk)
        return Response(ProjectMemberSerializer(member).data, status=201)

    @action(detail=True, methods=["patch", "delete"], url_path=r"members/(?P<member_id>[^/.]+)")
    def member_detail(self, request: Request, pk: str | None = None, member_id: str | None = None) -> Response:
        from uuid import UUID

        from claimiq.accounts.domain.permissions import PROJECT_MANAGE_MEMBERS, PROJECT_ROLES
        from claimiq.accounts.services.access import invalidate_access
        from claimiq.core.domain.errors import NotFoundError

        project = self.get_object()
        self._require_project(request, pk, PROJECT_MANAGE_MEMBERS)
        try:
            member = project.members.select_related("user").filter(pk=UUID(str(member_id))).first()
        except ValueError:
            member = None
        if member is None:
            raise NotFoundError("The requested member does not exist on this project.")
        if member.user_id == request.user.pk:
            raise ValidationError("You cannot change your own project membership.")

        if request.method == "DELETE":
            member.is_active = False
        else:
            role = request.data.get("role")
            if role is not None:
                valid_roles = {r.code for r in PROJECT_ROLES}
                if role not in valid_roles:
                    raise ValidationError("Unknown project role.", details={"field": "role", "valid": sorted(valid_roles)})
                member.role = role
        member.updated_by = request.user
        member.save()
        invalidate_access(member.user_id)
        if request.method == "DELETE":
            return Response(status=204)
        return Response(ProjectMemberSerializer(member).data)

    def _require_project(self, request: Request, pk, permission) -> None:
        context = access_for(request, pk)
        if context is None or not context.has(permission.code):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission.code},
            )

    @action(detail=True, methods=["get", "post"])
    def parties(self, request: Request, pk: str | None = None) -> Response:
        """The project's parties. Claimants, respondents, senders and recipients are chosen from these."""
        project = self.get_object()
        if request.method == "GET":
            self._require_project(request, pk, PROJECT_VIEW)
            return Response(
                PartySerializer(project.parties.order_by("role", "name"), many=True).data
            )
        self._require_project(request, pk, PROJECT_EDIT)
        serializer = PartySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        party = serializer.save(project=project, created_by=request.user)
        return Response(PartySerializer(party).data, status=201)

    @action(detail=True, methods=["patch", "delete"], url_path=r"parties/(?P<party_id>[^/.]+)")
    def party_detail(self, request: Request, pk: str | None = None, party_id: str | None = None) -> Response:
        from uuid import UUID

        project = self.get_object()
        self._require_project(request, pk, PROJECT_EDIT)
        try:
            party = project.parties.filter(pk=UUID(str(party_id))).first()
        except ValueError:
            party = None
        if party is None:
            from claimiq.core.domain.errors import NotFoundError

            raise NotFoundError("The requested party does not exist on this project.")
        if request.method == "DELETE":
            # References from claims and correspondence are SET_NULL; the raw
            # names recorded on correspondence are kept.
            party.delete()
            return Response(status=204)
        serializer = PartySerializer(party, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(PartySerializer(serializer.save(updated_by=request.user)).data)

    @action(detail=True, methods=["get"])
    def timeline(self, request: Request, pk: str | None = None) -> Response:
        """The project chronology across every claim, with conflicts and gaps surfaced."""
        from claimiq.claims.api.views import _render_entry
        from claimiq.claims.domain.chronology import EntryKind
        from claimiq.claims.services.timeline import build_project_chronology

        project = self.get_object()
        self._require_project(request, pk, PROJECT_VIEW)

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
            project_id=project.pk,
            claim_id=None,
            include_unreviewed=request.query_params.get("include_unreviewed", "true") != "false",
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


class EditionListView(viewsets.ViewSet):
    """The contract editions this installation understands.

    Read-only reference data, so the UI can offer a real choice rather than a
    free-text field that produces unresolvable editions.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request: Request) -> Response:
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
                    }
                    for e in DEFAULT_REGISTRY.editions()
                ],
            }
        )
