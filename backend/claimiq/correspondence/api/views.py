"""Correspondence and notice endpoints.

- ``/api/v1/correspondence/``                     letters, emails, instructions…
- ``/api/v1/correspondence/{id}/notices/``        assert that an item is a Notice
- ``/api/v1/correspondence/notices/``             notices, filterable by claim

A Notice is an assertion about a correspondence item, not a property of it:
whether a letter satisfies a notice provision is contested, so the assertion,
the provision it is said to satisfy, and whether anyone has confirmed it are
recorded separately from the letter.
"""
from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from claimiq.accounts.domain.permissions import CORRESPONDENCE_MANAGE, CORRESPONDENCE_VIEW
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import PermissionDeniedError, ValidationError
from claimiq.correspondence.models import Correspondence, Notice


class NoticeSerializer(serializers.ModelSerializer):
    claim_reference = serializers.CharField(source="claim.reference", read_only=True, default=None)
    claim_title = serializers.CharField(source="claim.title", read_only=True, default=None)
    correspondence_subject = serializers.CharField(source="correspondence.subject", read_only=True)
    correspondence_reference = serializers.CharField(source="correspondence.reference", read_only=True)
    sent_date = serializers.DateField(source="correspondence.sent_date", read_only=True)
    received_date = serializers.DateField(source="correspondence.received_date", read_only=True)

    class Meta:
        model = Notice
        fields = (
            "id", "project", "correspondence", "claim", "clause_number", "edition_code",
            "is_confirmed_notice", "notes", "claim_reference", "claim_title",
            "correspondence_subject", "correspondence_reference", "sent_date", "received_date",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "project", "correspondence", "created_at", "updated_at")


class CorrespondenceSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.name", read_only=True, default=None)
    sender_role = serializers.CharField(source="sender.role", read_only=True, default=None)
    recipient_name = serializers.CharField(source="recipient.name", read_only=True, default=None)
    recipient_role = serializers.CharField(source="recipient.role", read_only=True, default=None)
    document_title = serializers.CharField(source="document.title", read_only=True, default=None)
    notices = NoticeSerializer(many=True, read_only=True)

    class Meta:
        model = Correspondence
        fields = (
            "id", "project", "document", "document_title", "reference", "subject", "summary",
            "kind", "direction", "sender", "sender_name", "sender_role", "recipient",
            "recipient_name", "recipient_role", "sender_raw", "recipient_raw",
            "sent_date", "received_date", "clause_references", "in_reply_to",
            "is_ai_extracted", "extraction_confidence", "is_confirmed", "notices",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "is_ai_extracted", "extraction_confidence", "created_at", "updated_at",
        )

    def validate_clause_references(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("clause_references must be a list of clause numbers.")
        return [str(v).strip() for v in value if str(v).strip()]

    def validate(self, attrs):
        project = attrs.get("project") or getattr(self.instance, "project", None)
        for field in ("sender", "recipient"):
            party = attrs.get(field)
            if party is not None and project is not None and party.project_id != project.pk:
                raise serializers.ValidationError({field: "The party belongs to a different project."})
        document = attrs.get("document")
        if document is not None and project is not None and document.project_id != project.pk:
            raise serializers.ValidationError({"document": "The document belongs to a different project."})
        reply_to = attrs.get("in_reply_to")
        if reply_to is not None and project is not None and reply_to.project_id != project.pk:
            raise serializers.ValidationError({"in_reply_to": "That item belongs to a different project."})
        sent, received = attrs.get("sent_date"), attrs.get("received_date")
        if sent and received and received < sent:
            raise serializers.ValidationError(
                {"received_date": "The received date is before the sent date."}
            )
        return attrs


def _require(request: Request, project_id, permission: str):
    context = access_for(request, str(project_id))
    if context is None or not context.has(permission):
        raise PermissionDeniedError(
            "You do not have permission to perform this action on this project.",
            details={"required_permission": permission},
        )
    return context


class CorrespondenceViewSet(viewsets.ModelViewSet):
    serializer_class = CorrespondenceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "kind", "direction", "is_confirmed", "sender", "recipient"]
    search_fields = ["subject", "reference", "summary", "sender_raw", "recipient_raw"]
    ordering_fields = ["sent_date", "received_date", "created_at", "reference"]
    ordering = ["-sent_date", "-created_at"]

    def get_queryset(self) -> QuerySet[Correspondence]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Correspondence.objects.none()
        queryset = Correspondence.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
        ).select_related("sender", "recipient", "document").prefetch_related("notices__claim")
        if self.action in ("list", "retrieve"):
            allowed = [
                pid for pid in context.accessible_project_ids
                if (access_for(self.request, str(pid)) or context).has(CORRESPONDENCE_VIEW.code)
            ]
            queryset = queryset.filter(project_id__in=allowed)
        claim = self.request.query_params.get("claim")
        if claim:
            queryset = queryset.filter(notices__claim_id=claim).distinct()
        return queryset

    def perform_create(self, serializer) -> None:
        project = serializer.validated_data.get("project")
        if project is None:
            raise ValidationError("A 'project' is required.", details={"field": "project"})
        _require(self.request, project.pk, CORRESPONDENCE_MANAGE.code)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer) -> None:
        instance = serializer.instance
        new_project = serializer.validated_data.get("project")
        if new_project is not None and new_project.pk != instance.project_id:
            raise ValidationError("Correspondence cannot be moved to another project.")
        _require(self.request, instance.project_id, CORRESPONDENCE_MANAGE.code)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Correspondence) -> None:
        _require(self.request, instance.project_id, CORRESPONDENCE_MANAGE.code)
        instance.soft_delete(user=self.request.user)

    @action(detail=True, methods=["post"])
    def notices(self, request: Request, pk: str | None = None) -> Response:
        """Assert that this item is a Notice under a provision, optionally for a claim."""
        item = self.get_object()
        _require(request, item.project_id, CORRESPONDENCE_MANAGE.code)
        serializer = NoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claim = serializer.validated_data.get("claim")
        if claim is not None and claim.project_id != item.project_id:
            raise ValidationError("The claim belongs to a different project.", details={"field": "claim"})
        if not serializer.validated_data.get("clause_number", "").strip():
            raise ValidationError(
                "Name the provision the notice is given under, e.g. 20.2.1.",
                details={"field": "clause_number"},
            )
        notice = serializer.save(
            project_id=item.project_id,
            correspondence=item,
            edition_code=serializer.validated_data.get("edition_code") or item.project.contract_edition,
            created_by=request.user,
        )
        return Response(NoticeSerializer(notice).data, status=status.HTTP_201_CREATED)


class NoticeViewSet(viewsets.ModelViewSet):
    serializer_class = NoticeSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filterset_fields = ["project", "claim", "clause_number", "is_confirmed_notice"]
    ordering = ["correspondence__sent_date"]

    def get_queryset(self) -> QuerySet[Notice]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Notice.objects.none()
        return Notice.objects.filter(
            project__organization_id=context.organization_id,
            project_id__in=context.accessible_project_ids,
            correspondence__deleted_at__isnull=True,
        ).select_related("claim", "correspondence")

    def perform_update(self, serializer) -> None:
        instance = serializer.instance
        _require(self.request, instance.project_id, CORRESPONDENCE_MANAGE.code)
        claim = serializer.validated_data.get("claim")
        if claim is not None and claim.project_id != instance.project_id:
            raise ValidationError("The claim belongs to a different project.", details={"field": "claim"})
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Notice) -> None:
        _require(self.request, instance.project_id, CORRESPONDENCE_MANAGE.code)
        instance.delete()
