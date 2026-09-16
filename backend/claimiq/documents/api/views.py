"""Document endpoints."""
from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import (
    DOCUMENT_DELETE,
    DOCUMENT_REPROCESS,
    DOCUMENT_UPLOAD,
    DOCUMENT_VIEW,
)
from claimiq.core.api.pagination import LargePagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.documents.api.viewer import (
    original_file_response,
    page_image_response,
    page_payload,
    processing_payload,
    sections_payload,
)
from claimiq.documents.domain.taxonomy import ALL_CATEGORIES, DEFAULT_TAXONOMY
from claimiq.documents.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentVersion,
)
from claimiq.documents.services.upload import upload_document
from claimiq.ingestion.models import ProcessingJob


class DocumentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentVersion
        fields = (
            "id", "version_number", "original_filename", "content_type",
            "file_size_bytes", "page_count", "extraction_method",
            "processing_status", "processing_error", "extraction_quality",
            "is_superseded", "processed_at", "created_at",
        )
        read_only_fields = fields


class DocumentSerializer(serializers.ModelSerializer):
    current_version = DocumentVersionSerializer(read_only=True)
    document_type_label = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = (
            "id", "project", "title", "document_type", "document_type_label",
            "reference", "description", "document_date", "is_confidential",
            "current_version", "created_at", "updated_at",
        )
        read_only_fields = ("id", "project", "current_version", "created_at", "updated_at")

    def get_document_type_label(self, obj: Document) -> str:
        if not DEFAULT_TAXONOMY.has(obj.document_type):
            return obj.document_type
        return DEFAULT_TAXONOMY.get(obj.document_type).label


class DocumentPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentPage
        fields = (
            "page_number", "text", "extraction_method", "ocr_confidence",
            "is_content_page", "has_tables",
        )
        read_only_fields = fields


class DocumentChunkSerializer(serializers.ModelSerializer):
    has_embedding = serializers.SerializerMethodField()

    class Meta:
        model = DocumentChunk
        fields = (
            "id", "sequence", "text", "clause_number", "clause_title",
            "start_page", "end_page", "is_complete_clause", "char_count",
            "has_embedding",
        )
        read_only_fields = fields

    def get_has_embedding(self, obj: DocumentChunk) -> bool:
        """Whether this chunk participates in vector search.

        Surfaced because a deployment without an embedding provider produces
        documents that are lexically searchable and not vector-searchable, and
        the UI must be able to say which.
        """
        return obj.embedding is not None


class ProcessingJobSerializer(serializers.ModelSerializer):
    document_id = serializers.UUIDField(source="document_version.document_id", read_only=True)
    document_title = serializers.CharField(
        source="document_version.document.title", read_only=True
    )

    class Meta:
        model = ProcessingJob
        fields = (
            "id", "document_id", "document_title", "kind", "status",
            "current_stage", "progress_percent", "error_code", "error_message",
            "started_at", "finished_at", "created_at",
        )
        read_only_fields = fields


class DocumentViewSet(viewsets.ModelViewSet):
    """Documents within projects the user may read."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["project", "document_type", "is_confidential"]
    ordering_fields = ["created_at", "document_date", "title"]
    ordering = ["-created_at"]
    search_fields = ["title", "reference"]

    def get_queryset(self) -> QuerySet[Document]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return Document.objects.none()
        return (
            Document.objects.filter(
                project__organization_id=context.organization_id,
                project_id__in=context.accessible_project_ids,
            )
            .select_related("current_version", "project")
        )

    def _require(self, permission: str, project_id) -> None:
        context = access_for(self.request, project_id)
        if context is None or not context.has(permission):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission},
            )

    def create(self, request: Request, *args, **kwargs) -> Response:
        """Upload a document. Multipart: `file`, `project`, `document_type`."""
        project_id = request.data.get("project")
        if not project_id:
            raise ValidationError("A 'project' is required.")
        self._require(DOCUMENT_UPLOAD.code, project_id)

        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("A 'file' is required.")

        document_type = request.data.get("document_type")
        if not document_type:
            raise ValidationError("A 'document_type' is required.")

        from claimiq.projects.models import Project

        context = access_for(request, project_id)
        project = Project.objects.filter(
            pk=project_id, organization_id=context.organization_id
        ).first()
        if project is None or project.pk not in context.accessible_project_ids:
            raise NotFoundError("The requested project does not exist.")

        result = upload_document(
            project=project,
            upload=upload,
            document_type=document_type,
            user=request.user,
            title=request.data.get("title", ""),
            reference=request.data.get("reference", ""),
            document_date=request.data.get("document_date") or None,
            replaces_document_id=request.data.get("replaces") or None,
        )

        return Response(
            {
                "document": DocumentSerializer(result.document).data,
                "version": DocumentVersionSerializer(result.version).data,
                "job_id": result.job_id,
                "warnings": [
                    f.message
                    for f in result.safety.findings
                    if f.level.value == "suspicious"
                ],
                # Honest about a broker failure rather than reporting success.
                "queued": result.job_id is not None,
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer) -> None:
        document = self.get_object()
        self._require(DOCUMENT_UPLOAD.code, str(document.project_id))
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Document) -> None:
        self._require(DOCUMENT_DELETE.code, str(instance.project_id))
        instance.soft_delete(user=self.request.user)

    @action(detail=True, methods=["get"], pagination_class=LargePagination)
    def pages(self, request: Request, pk: str | None = None) -> Response:
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        if document.current_version is None:
            return Response({"count": 0, "results": []})
        queryset = DocumentPage.objects.filter(
            version=document.current_version
        ).order_by("page_number")
        page = self.paginate_queryset(queryset)
        serializer = DocumentPageSerializer(page or queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=True, methods=["get"], pagination_class=LargePagination)
    def chunks(self, request: Request, pk: str | None = None) -> Response:
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        if document.current_version is None:
            return Response({"count": 0, "results": []})
        queryset = DocumentChunk.objects.filter(
            version=document.current_version
        ).order_by("sequence")
        page = self.paginate_queryset(queryset)
        serializer = DocumentChunkSerializer(page or queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=True, methods=["post"])
    def reprocess(self, request: Request, pk: str | None = None) -> Response:
        """Re-run ingestion, optionally from a specific stage."""
        document = self.get_object()
        self._require(DOCUMENT_REPROCESS.code, str(document.project_id))
        if document.current_version is None:
            raise ValidationError("This document has no version to reprocess.")

        from claimiq.ingestion.domain.pipeline import Stage
        from claimiq.ingestion.services.dispatch import dispatch_job
        from claimiq.ingestion.services.runner import reprocess_from

        stage_value = request.data.get("from_stage") or Stage.VALIDATE.value
        try:
            stage = Stage(stage_value)
        except ValueError:
            raise ValidationError(
                f"Unknown pipeline stage {stage_value!r}.",
                details={"valid": [s.value for s in Stage]},
            ) from None

        job = reprocess_from(document.current_version, stage)
        dispatch_job(str(job.id))
        return Response({"job_id": str(job.id), "from_stage": stage.value})

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_number>\d+)")
    def page(self, request: Request, pk: str | None = None, page_number: str = "1") -> Response:
        """One page's text and what the viewer can show for it."""
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        return Response(page_payload(document, int(page_number)))

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_number>\d+)/image")
    def page_image(self, request: Request, pk: str | None = None, page_number: str = "1"):
        """The page rendered as PNG. 404 for formats without page images."""
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        return page_image_response(document, int(page_number), request)

    @action(detail=True, methods=["get"])
    def file(self, request: Request, pk: str | None = None):
        """The original uploaded file of the current version."""
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        return original_file_response(document)

    @action(detail=True, methods=["get"])
    def sections(self, request: Request, pk: str | None = None) -> Response:
        """Detected clause hierarchy and extracted tables."""
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        return Response(sections_payload(document))

    @action(detail=True, methods=["get"])
    def processing(self, request: Request, pk: str | None = None) -> Response:
        """The latest ingestion job for the current version, stage by stage."""
        document = self.get_object()
        self._require(DOCUMENT_VIEW.code, str(document.project_id))
        return Response(processing_payload(document))


class ProcessingJobViewSet(viewsets.ReadOnlyModelViewSet):
    """Ingestion jobs for documents the user may read."""

    serializer_class = ProcessingJobSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "kind"]
    ordering = ["-created_at"]

    def get_queryset(self) -> QuerySet[ProcessingJob]:
        context = access_for(self.request)
        if context is None or context.organization_id is None:
            return ProcessingJob.objects.none()
        return ProcessingJob.objects.filter(
            document_version__document__project_id__in=context.accessible_project_ids
        ).select_related("document_version__document")


class TaxonomyView(APIView):
    """The document taxonomy, so the UI offers real types rather than free text."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(
            {
                "categories": list(ALL_CATEGORIES),
                "types": [
                    {
                        "code": t.code,
                        "label": t.label,
                        "category": t.category,
                        "description": t.description,
                        "expects_clauses": t.expects_clauses,
                        "is_contractual": t.is_contractual,
                    }
                    for t in DEFAULT_TAXONOMY.all()
                ],
            }
        )
