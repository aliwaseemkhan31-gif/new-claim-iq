"""Access resolution.

Turns a user plus a target into a permission set, using the framework-free
model in :mod:`claimiq.accounts.domain.permissions`. The domain owns the rules;
this module owns the database lookups that feed them.

Kept in the service layer, not in a DRF permission class, for one reason: a
Celery task, a management command and a report generator all need the same
answer and none of them has a request. A permission check that only exists in
the HTTP layer is a permission check the background worker skips.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.core.cache import cache

from claimiq.accounts.domain.permissions import (
    ROLE_SYSTEM_ADMINISTRATOR,
    resolve_permissions,
)
from claimiq.accounts.models import OrganizationMembership, User
from claimiq.core.domain.errors import PermissionDeniedError
from claimiq.projects.models import ProjectMember

#: Short TTL. Long enough to spare the database on a burst of requests, short
#: enough that revoking access takes effect in seconds rather than minutes.
#: Revocation also busts the cache explicitly; this bounds the window if
#: something is changed outside the application.
ACCESS_CACHE_SECONDS = 30


@dataclass(frozen=True)
class AccessContext:
    """A user's effective access, resolved once per request."""

    user_id: UUID
    organization_id: UUID | None
    organization_role: str | None
    project_id: UUID | None = None
    project_role: str | None = None
    permissions: frozenset[str] = frozenset()
    accessible_project_ids: frozenset[UUID] = frozenset()

    @property
    def is_system_admin(self) -> bool:
        return self.organization_role == ROLE_SYSTEM_ADMINISTRATOR.code

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        """Raise unless ``permission`` is held.

        The error names the permission rather than saying "forbidden", because
        an operator diagnosing a role misconfiguration needs to know which one
        was missing.
        """
        if permission not in self.permissions:
            raise PermissionDeniedError(
                "You do not have permission to perform this action.",
                details={
                    "required_permission": permission,
                    "project_id": str(self.project_id) if self.project_id else None,
                },
            )


def _membership_for(user: User, organization_id: UUID | None) -> OrganizationMembership | None:
    queryset = OrganizationMembership.objects.filter(user=user, is_active=True)
    if organization_id is not None:
        queryset = queryset.filter(organization_id=organization_id)
    return queryset.select_related("organization").first()


def accessible_project_ids(user: User, organization_id: UUID) -> frozenset[UUID]:
    """Projects this user may read.

    A System Administrator sees every project in the organization — the single
    explicit exception in the model. Everyone else sees only projects they are
    an active member of; organization membership alone grants nothing.
    """
    membership = _membership_for(user, organization_id)
    if membership is None:
        return frozenset()

    if membership.role == ROLE_SYSTEM_ADMINISTRATOR.code:
        from claimiq.projects.models import Project

        return frozenset(
            Project.objects.filter(organization_id=organization_id).values_list(
                "id", flat=True
            )
        )

    return frozenset(
        ProjectMember.objects.filter(
            user=user,
            is_active=True,
            project__organization_id=organization_id,
            project__deleted_at__isnull=True,
        ).values_list("project_id", flat=True)
    )


def resolve_access(
    user: User,
    *,
    organization_id: UUID | None = None,
    project_id: UUID | None = None,
) -> AccessContext:
    """Resolve a user's effective access.

    Args:
        user: The authenticated principal.
        organization_id: Tenant. Defaults to the user's single active membership.
        project_id: When given, the project role is unioned into the result.

    Returns:
        An :class:`AccessContext`. A user with no active membership gets an
        empty one rather than an error — "no permissions" is a valid state and
        the caller's ``require`` will produce the right message.
    """
    membership = _membership_for(user, organization_id)
    if membership is None:
        return AccessContext(
            user_id=user.id, organization_id=organization_id, organization_role=None
        )

    org_id = membership.organization_id
    org_role = membership.role

    project_role: str | None = None
    if project_id is not None:
        if org_role == ROLE_SYSTEM_ADMINISTRATOR.code:
            # The system administrator exception, applied explicitly rather
            # than by granting them a project role they do not hold.
            from claimiq.accounts.domain.permissions import ROLE_PROJECT_MANAGER

            project_role = ROLE_PROJECT_MANAGER.code
        else:
            member = ProjectMember.objects.filter(
                user=user, project_id=project_id, is_active=True
            ).first()
            project_role = member.role if member else None

    permissions = resolve_permissions(org_role, project_role)

    return AccessContext(
        user_id=user.id,
        organization_id=org_id,
        organization_role=org_role,
        project_id=project_id,
        project_role=project_role,
        permissions=permissions,
        accessible_project_ids=accessible_project_ids(user, org_id),
    )


def cached_access(
    user: User,
    *,
    organization_id: UUID | None = None,
    project_id: UUID | None = None,
) -> AccessContext:
    """:func:`resolve_access` with a short cache.

    Resolution runs on every request and costs two or three queries. The TTL is
    deliberately short — see :data:`ACCESS_CACHE_SECONDS`.
    """
    key = f"access:{user.pk}:{organization_id or '-'}:{project_id or '-'}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    context = resolve_access(
        user, organization_id=organization_id, project_id=project_id
    )
    cache.set(key, context, ACCESS_CACHE_SECONDS)
    return context


def invalidate_access(user_id: UUID) -> None:
    """Drop cached access for a user.

    Called when membership or a role changes. The TTL alone would make a
    revocation take up to 30 seconds to bite, which is too long for an
    intentional revocation even if it is fine for eventual consistency.
    """
    # django-redis exposes delete_pattern; the locmem backend used in tests
    # does not. Falling back to a targeted delete of the common key shapes
    # rather than cache.clear(), which would evict every other tenant's
    # entries to revoke one user.
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern(f"access:{user_id}:*")
        return

    from claimiq.projects.models import ProjectMember

    keys = [f"access:{user_id}:-:-"]
    for org_id, project_id in ProjectMember.objects.filter(
        user_id=user_id
    ).values_list("project__organization_id", "project_id"):
        keys.append(f"access:{user_id}:{org_id}:-")
        keys.append(f"access:{user_id}:{org_id}:{project_id}")
        keys.append(f"access:{user_id}:-:{project_id}")
    cache.delete_many(keys)
