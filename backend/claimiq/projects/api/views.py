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

        documents = Document.objects.filter(project=project)
        return Response(
            {
                "project": str(project.id),
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

    @action(detail=True, methods=["get"])
    def members(self, request: Request, pk: str | None = None) -> Response:
        project = self.get_object()
        context = access_for(request, pk)
        if context is None or not context.has(PROJECT_VIEW.code):
            raise PermissionDeniedError("Not permitted.")
        queryset = project.members.select_related("user").filter(is_active=True)
        return Response(ProjectMemberSerializer(queryset, many=True).data)


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
