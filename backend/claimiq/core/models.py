"""Base model classes shared by every domain app.

Four concerns are factored out here because getting them inconsistent across a
codebase this size is a reliable source of defects: identity, timestamps, soft
deletion, and authorship.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    """Primary key is a random UUID.

    Sequential integer ids leak record counts and invite enumeration. The
    prototype exposed 8-character id prefixes on unauthenticated endpoints, so
    every contract in the system was reachable by guessing. UUIDs also make
    importing records from another deployment safe, which the legacy import
    tooling needs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=False)

    def delete(self):  # type: ignore[override]
        """Soft-delete the whole queryset."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        """Permanently remove rows. Reserved for retention policy and tests."""
        return super().delete()


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    """Default manager that hides soft-deleted rows.

    ``Model.objects`` returns live rows only; ``Model.all_objects`` returns
    everything. Making the safe query the default means a view that forgets to
    filter shows the right thing.
    """

    def get_queryset(self) -> SoftDeleteQuerySet:
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    pass


class SoftDeleteModel(models.Model):
    """Deletion marks a row rather than removing it.

    Contract records carry evidential weight: a claim, a notice or a piece of
    correspondence that was deleted may still need to be accounted for, and an
    audit trail that references a vanished row is not an audit trail. Hard
    deletion remains available for genuine retention obligations, as an explicit
    operation rather than the default.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, user=None) -> None:
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])


class AuthoredModel(models.Model):
    """Records who created and last modified a row.

    Separate from the audit log, which records *what changed*. These columns
    answer "who owns this record" without a join, which the UI needs on
    virtually every list view.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimestampedModel, AuthoredModel):
    """UUID identity, timestamps and authorship. The default for domain entities."""

    class Meta:
        abstract = True


class BaseSoftDeleteModel(BaseModel, SoftDeleteModel):
    """:class:`BaseModel` plus soft deletion. For records that must survive removal."""

    class Meta:
        abstract = True
