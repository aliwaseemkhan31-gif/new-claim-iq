"""Organizations, users and role assignments."""
from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from claimiq.accounts.domain.permissions import (
    ORGANIZATION_ROLES,
    PROJECT_ROLES,
    ROLE_ORGANIZATION_MEMBER,
)
from claimiq.core.models import BaseModel, BaseSoftDeleteModel

ORGANIZATION_ROLE_CHOICES = [(r.code, r.label) for r in ORGANIZATION_ROLES]
PROJECT_ROLE_CHOICES = [(r.code, r.label) for r in PROJECT_ROLES]


class Organization(BaseSoftDeleteModel):
    """A tenant. The outermost boundary for data isolation."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True)
    """Per-tenant overrides (retention, default contract form). JSONB is correct
    here: the keys are open-ended and never queried relationally."""

    class Meta:
        db_table = "accounts_organization"
        ordering = ["name"]
        indexes = [models.Index(fields=["slug"])]

    def __str__(self) -> str:
        return self.name


class UserManager(BaseUserManager):
    """Email-based user creation. There is no username field."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create_user(email, password, **extra)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    """A person. Authenticates by email.

    Django's ``is_superuser`` governs the Django admin only. Application
    authorisation runs through :mod:`claimiq.accounts.domain.permissions`, so
    a Django superuser is not automatically a ClaimIQ System Administrator —
    the two are deliberately separate.
    """

    email = models.EmailField(_("email address"), unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    preferences = models.JSONField(default=dict, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        ordering = ["email"]

    def __str__(self) -> str:
        return self.full_name or self.email

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > timezone.now()

    def get_short_name(self) -> str:
        return (self.full_name or self.email).split(" ")[0]


class OrganizationMembership(BaseModel):
    """Links a user to an organization with an organization-scoped role."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(
        max_length=64,
        choices=ORGANIZATION_ROLE_CHOICES,
        default=ROLE_ORGANIZATION_MEMBER.code,
    )
    is_active = models.BooleanField(default=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_organization_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="uniq_org_membership"
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["organization", "role"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.organization} ({self.role})"


class ApiToken(BaseModel):
    """A long-lived token for machine access.

    Only the hash is stored, so a database compromise does not yield usable
    tokens. The plaintext is shown once at creation and never again.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_tokens"
    )
    name = models.CharField(max_length=255)
    token_hash = models.CharField(max_length=128, unique=True, db_index=True)
    prefix = models.CharField(max_length=12, db_index=True)
    """Non-secret leading characters, so a user can identify a token in a list."""

    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_api_token"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @property
    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()
