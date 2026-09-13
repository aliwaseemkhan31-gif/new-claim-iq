"""Documents, versions, pages, sections and chunks.

The document hierarchy is what makes a citation resolvable:

    Document -> DocumentVersion -> DocumentPage -> DocumentSection -> DocumentChunk

Every retrieved chunk therefore knows its page, its clause and its character
offsets, so a citation can navigate a viewer to the exact passage. The
prototype stored ``{page, source}`` per chunk and could go no further than
"page 127".
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from claimiq.core.models import BaseModel, BaseSoftDeleteModel
from claimiq.projects.models import Project

EMBEDDING_DIMENSIONS = settings.AI_SETTINGS["EMBEDDING_DIMENSIONS"]


class ProcessingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    QUARANTINED = "quarantined", "Quarantined"
    CANCELLED = "cancelled", "Cancelled"


class ExtractionMethod(models.TextChoices):
    DIGITAL = "digital", "Digital text extraction"
    OCR = "ocr", "OCR"
    HYBRID = "hybrid", "Hybrid (digital + OCR)"
    NONE = "none", "No text extracted"


class DocumentCollection(BaseSoftDeleteModel):
    """A user-defined grouping of documents within a project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="collections")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "documents_collection"
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self) -> str:
        return self.name


class Document(BaseSoftDeleteModel):
    """A logical document. Content lives in its versions.

    Separating the document from its versions means a superseded revision keeps
    its own extracted text, chunks and embeddings. Comparison between revisions,
    and citations into a historical revision, both depend on that.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    collection = models.ForeignKey(
        DocumentCollection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
    )

    title = models.CharField(max_length=512)
    document_type = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Taxonomy code from claimiq.documents.domain.taxonomy.",
    )
    reference = models.CharField(
        max_length=255, blank=True, help_text="Project document reference or number."
    )
    description = models.TextField(blank=True)

    document_date = models.DateField(
        null=True, blank=True, db_index=True,
        help_text="Date on the document itself, not the upload date.",
    )

    current_version = models.OneToOneField(
        "documents.DocumentVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for",
    )

    is_confidential = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "documents_document"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "document_type"]),
            models.Index(fields=["project", "-document_date"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentVersion(BaseModel):
    """One uploaded revision of a document, and its processing state."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField(default=1)

    file = models.FileField(upload_to="documents/%Y/%m/")
    original_filename = models.CharField(max_length=512)
    content_type = models.CharField(max_length=128)
    file_size_bytes = models.BigIntegerField()
    checksum_sha256 = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Content hash. Detects re-uploads of identical files.",
    )

    page_count = models.PositiveIntegerField(default=0)
    extraction_method = models.CharField(
        max_length=16, choices=ExtractionMethod.choices, default=ExtractionMethod.NONE
    )
    processing_status = models.CharField(
        max_length=16,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
        db_index=True,
    )
    processing_error = models.TextField(
        blank=True,
        help_text="Operator-facing failure reason. Never returned as an answer.",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    extraction_quality = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "0-1 confidence in the extracted text. Low values mark a document "
            "whose citations should be treated with caution rather than "
            "silently trusted."
        ),
    )

    is_superseded = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "documents_document_version"
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "version_number"], name="uniq_document_version"
            )
        ]
        indexes = [
            models.Index(fields=["document", "-version_number"]),
            models.Index(fields=["processing_status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.document.title} v{self.version_number}"


class DocumentPage(BaseModel):
    """One page of extracted text."""

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="pages"
    )
    page_number = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    width = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    rotation = models.IntegerField(default=0)

    extraction_method = models.CharField(
        max_length=16, choices=ExtractionMethod.choices, default=ExtractionMethod.DIGITAL
    )
    ocr_confidence = models.FloatField(null=True, blank=True)
    has_tables = models.BooleanField(default=False)
    has_images = models.BooleanField(default=False)

    is_content_page = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "False for contents pages, indexes and front matter. Excluded from "
            "chunking — this flag is what prevents the contamination that "
            "required a manual purge in the legacy knowledge base."
        ),
    )

    search_vector = SearchVectorField(null=True, editable=False)

    class Meta:
        db_table = "documents_document_page"
        ordering = ["page_number"]
        constraints = [
            models.UniqueConstraint(fields=["version", "page_number"], name="uniq_version_page")
        ]
        indexes = [
            models.Index(fields=["version", "page_number"]),
            GinIndex(fields=["search_vector"], name="doc_page_search_gin"),
        ]

    def __str__(self) -> str:
        return f"p.{self.page_number}"


class DocumentSection(BaseModel):
    """A structural region of a document: a clause, heading or table.

    Self-referential so the clause hierarchy detected during ingestion is
    persisted rather than flattened.
    """

    class SectionKind(models.TextChoices):
        CLAUSE = "clause", "Clause"
        HEADING = "heading", "Heading"
        PARAGRAPH = "paragraph", "Paragraph"
        TABLE = "table", "Table"
        LIST = "list", "List"
        FOOTNOTE = "footnote", "Footnote"
        PREAMBLE = "preamble", "Preamble"

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="sections"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )

    kind = models.CharField(max_length=16, choices=SectionKind.choices)
    clause_number = models.CharField(max_length=32, blank=True, db_index=True)
    title = models.CharField(max_length=512, blank=True)
    depth = models.PositiveSmallIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=0)

    start_page = models.PositiveIntegerField()
    end_page = models.PositiveIntegerField()
    start_offset = models.PositiveIntegerField(default=0)
    end_offset = models.PositiveIntegerField(default=0)

    text = models.TextField(blank=True)
    detection_confidence = models.FloatField(
        default=1.0,
        help_text="Confidence from clause detection. Surfaced so a user can see "
                  "which structure was inferred rather than certain.",
    )

    class Meta:
        db_table = "documents_document_section"
        ordering = ["sequence"]
        indexes = [
            models.Index(fields=["version", "sequence"]),
            models.Index(fields=["version", "clause_number"]),
            models.Index(fields=["parent"]),
        ]

    def __str__(self) -> str:
        return f"{self.clause_number} {self.title}".strip() or self.kind


class DocumentChunk(BaseModel):
    """A retrievable unit of text with its embedding and full provenance.

    Both retrieval representations live on the same row: ``search_vector`` for
    lexical matching and ``embedding`` for dense similarity. Keeping them
    together is what makes hybrid retrieval a single query with a single
    permission filter (ADR 0002).
    """

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="chunks"
    )
    section = models.ForeignKey(
        DocumentSection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chunks",
    )

    sequence = models.PositiveIntegerField()
    text = models.TextField()
    embedding_text = models.TextField(
        blank=True,
        help_text="Text actually embedded: the chunk prefixed with its clause context.",
    )

    clause_number = models.CharField(max_length=32, blank=True, db_index=True)
    clause_title = models.CharField(max_length=512, blank=True)
    heading_path = models.JSONField(default=list, blank=True)

    start_page = models.PositiveIntegerField(db_index=True)
    end_page = models.PositiveIntegerField()
    start_offset = models.PositiveIntegerField(default=0)
    end_offset = models.PositiveIntegerField(default=0)

    is_complete_clause = models.BooleanField(default=False)
    char_count = models.PositiveIntegerField(default=0)

    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    embedding_model = models.CharField(
        max_length=128,
        blank=True,
        help_text=(
            "Model that produced the embedding. Recorded so vectors from "
            "different models are never compared, and so a model change can be "
            "detected and the affected chunks re-embedded."
        ),
    )
    embedded_at = models.DateTimeField(null=True, blank=True)

    search_vector = SearchVectorField(null=True, editable=False)

    class Meta:
        db_table = "documents_document_chunk"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["version", "sequence"], name="uniq_version_chunk")
        ]
        indexes = [
            models.Index(fields=["version", "sequence"]),
            models.Index(fields=["version", "clause_number"]),
            GinIndex(fields=["search_vector"], name="doc_chunk_search_gin"),
            HnswIndex(
                name="doc_chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"chunk {self.sequence} of {self.version_id}"
