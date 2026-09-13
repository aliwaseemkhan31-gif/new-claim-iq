"""Knowledge base: standard-form contract text, scoped by edition.

Separate from project documents because the two answer different questions and
carry different access rules. A knowledge base is organization-wide reference
material — the FIDIC Red Book applies to every project governed by it — whereas
a project document belongs to one project and one permission boundary.

The column that matters most is ``edition`` on every chunk, indexed, so
edition scoping is a SQL predicate rather than a convention someone can forget
(ADR 0004). The prototype tagged edition in a metadata dict and then shipped an
endpoint that did not filter on it.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from claimiq.accounts.models import Organization
from claimiq.core.models import BaseModel, BaseSoftDeleteModel

EMBEDDING_DIMENSIONS = settings.AI_SETTINGS["EMBEDDING_DIMENSIONS"]


class KnowledgeBaseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    VALIDATING = "validating", "Validating"
    PUBLISHED = "published", "Published"
    QUARANTINED = "quarantined", "Quarantined"
    """Validation found blocking contamination. Not retrievable."""


class KnowledgeBase(BaseSoftDeleteModel):
    """One edition of one standard form, as ingested into this installation."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="knowledge_bases"
    )
    name = models.CharField(max_length=255)
    edition_code = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Edition code from the registry, e.g. 'red-book-2017'.",
    )
    form_code = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=16,
        choices=KnowledgeBaseStatus.choices,
        default=KnowledgeBaseStatus.DRAFT,
        db_index=True,
    )

    source_filename = models.CharField(max_length=512, blank=True)
    source_checksum = models.CharField(max_length=64, blank=True)

    chunk_count = models.PositiveIntegerField(default=0)
    validated_at = models.DateTimeField(null=True, blank=True)
    validation_report = models.JSONField(
        default=dict,
        blank=True,
        help_text="Latest ValidationReport. Blocking findings prevent publication.",
    )

    class Meta:
        db_table = "knowledge_knowledge_base"
        ordering = ["edition_code"]
        constraints = [
            # One knowledge base per edition per organization. Two would mean
            # a retrieval scoped to an edition could draw from either, which is
            # the ambiguity edition scoping exists to remove.
            models.UniqueConstraint(
                fields=["organization", "edition_code"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_kb_edition_per_org",
            )
        ]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.edition_code})"

    @property
    def is_retrievable(self) -> bool:
        return self.status == KnowledgeBaseStatus.PUBLISHED


class KnowledgeBaseChunk(BaseModel):
    """A retrievable passage of standard-form text.

    ``edition_code`` is denormalised from the parent knowledge base so that
    edition filtering needs no join. That is deliberate: the filter runs on
    every knowledge-base query, and a denormalised indexed column is harder to
    accidentally omit than a join condition.
    """

    knowledge_base = models.ForeignKey(
        KnowledgeBase, on_delete=models.CASCADE, related_name="chunks"
    )
    edition_code = models.CharField(max_length=64, db_index=True)

    sequence = models.PositiveIntegerField()
    text = models.TextField()
    embedding_text = models.TextField(blank=True)

    clause_number = models.CharField(max_length=32, blank=True, db_index=True)
    clause_title = models.CharField(max_length=512, blank=True)
    heading_path = models.JSONField(default=list, blank=True)

    page_number = models.PositiveIntegerField()
    is_complete_clause = models.BooleanField(default=False)
    char_count = models.PositiveIntegerField(default=0)

    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    embedding_model = models.CharField(max_length=128, blank=True)
    embedded_at = models.DateTimeField(null=True, blank=True)

    search_vector = SearchVectorField(null=True, editable=False)

    is_quarantined = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Excluded from retrieval by knowledge-base validation — contents "
            "pages, guidance notes, front matter. Retained rather than deleted "
            "so a validation rule can be revised without re-ingesting."
        ),
    )
    quarantine_reason = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "knowledge_knowledge_base_chunk"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge_base", "sequence"], name="uniq_kb_chunk_sequence"
            )
        ]
        indexes = [
            models.Index(fields=["knowledge_base", "sequence"]),
            # The two predicates on every knowledge-base query.
            models.Index(fields=["edition_code", "clause_number"]),
            models.Index(fields=["edition_code", "is_quarantined"]),
            GinIndex(fields=["search_vector"], name="kb_chunk_search_gin"),
            HnswIndex(
                name="kb_chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.edition_code} {self.clause_number} p.{self.page_number}".strip()
