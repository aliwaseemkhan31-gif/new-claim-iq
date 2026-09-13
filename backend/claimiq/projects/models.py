"""Projects, membership and contracting parties."""
from __future__ import annotations

from django.db import models

from claimiq.accounts.models import PROJECT_ROLE_CHOICES, Organization, User
from claimiq.core.models import BaseModel, BaseSoftDeleteModel


class ProjectStatus(models.TextChoices):
    PLANNING = "planning", "Planning"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    COMPLETED = "completed", "Completed"
    CLOSED = "closed", "Closed"
    ARCHIVED = "archived", "Archived"


class PartyRole(models.TextChoices):
    EMPLOYER = "employer", "Employer"
    CONTRACTOR = "contractor", "Contractor"
    ENGINEER = "engineer", "Engineer"
    SUBCONTRACTOR = "subcontractor", "Subcontractor"
    CONSULTANT = "consultant", "Consultant"
    SUPPLIER = "supplier", "Supplier"
    OTHER = "other", "Other"


class Project(BaseSoftDeleteModel):
    """A construction project. The primary working context.

    ``contract_edition`` is the field that makes ADR 0004 workable: retrieval
    scope is derived from the project's declared governing form rather than
    from a constant. It is nullable because a project may be created before the
    contract is known — but knowledge-base retrieval is refused with an
    actionable error until it is set, rather than guessing an edition.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=32, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )

    contract_form = models.CharField(
        max_length=64,
        blank=True,
        help_text="Contract form code, e.g. 'fidic-red-book'.",
    )
    contract_edition = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=(
            "Governing edition code, e.g. 'red-book-2017'. Knowledge-base "
            "retrieval is refused until this is set — an edition must never be "
            "assumed (ADR 0004)."
        ),
    )

    contract_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, blank=True)

    commencement_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)
    actual_completion_date = models.DateField(null=True, blank=True)

    location = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "projects_project"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(code=""),
                name="uniq_project_code_per_org",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}".strip()

    @property
    def has_governing_edition(self) -> bool:
        return bool(self.contract_edition)


class ProjectMember(BaseModel):
    """A user's role on one project. The unit of project-level access control."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="project_memberships")
    role = models.CharField(max_length=64, choices=PROJECT_ROLE_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "projects_project_member"
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_project_member")
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["project", "role"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} on {self.project} ({self.role})"


class Party(BaseModel):
    """An organization or person party to the contract.

    Modelled as a project entity rather than free text so correspondence can be
    attributed, obligations can name a responsible party, and "who was required
    to notify whom" becomes a query rather than a reading exercise.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="parties")
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=32, choices=PartyRole.choices)
    short_name = models.CharField(max_length=64, blank=True)
    is_primary = models.BooleanField(
        default=False,
        help_text="The principal party in this role, where several exist.",
    )
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Alternative names seen in correspondence. Used to attribute letters "
            "and emails to a party when the sender line varies."
        ),
    )

    class Meta:
        db_table = "projects_party"
        ordering = ["role", "name"]
        indexes = [models.Index(fields=["project", "role"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_role_display()})"
