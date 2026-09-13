"""DRF permission classes.

Thin wrappers over :mod:`claimiq.accounts.services.access`. The rules live in
the domain and the lookups in the service; these only bind them to a request.

Every class is deny-by-default: an unauthenticated or unresolvable principal
gets False, never a pass-through. Combined with
``DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]``, a view that forgets to
declare permissions is closed rather than open — the inverse of the prototype,
where every endpoint was public.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from claimiq.accounts.services.access import AccessContext, cached_access


def _uuid_or_none(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def access_for(request: Request, project_id: Any = None) -> AccessContext | None:
    """Resolve and memoise the access context for this request.

    Cached on the request object so several permission classes and the view
    itself resolve once rather than once each.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None

    project_uuid = _uuid_or_none(project_id)
    cache_key = f"_claimiq_access_{project_uuid or '-'}"
    existing = getattr(request, cache_key, None)
    if existing is not None:
        return existing

    organization_id = _uuid_or_none(
        request.headers.get("X-Organization-Id")
        or request.query_params.get("organization")
    )
    context = cached_access(
        user, organization_id=organization_id, project_id=project_uuid
    )
    setattr(request, cache_key, context)
    return context


class HasOrganizationPermission(BasePermission):
    """Requires an organization-scoped permission.

    Set ``required_permission`` on the view.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request: Request, view: Any) -> bool:
        required = getattr(view, "required_permission", None)
        if not required:
            # A view that declares this class but no permission is a
            # configuration error. Deny rather than allow.
            return False
        context = access_for(request)
        return context is not None and context.has(required)


class HasProjectPermission(BasePermission):
    """Requires a project-scoped permission on the project in the URL.

    The project id is taken from the URL kwargs (``project_pk`` or ``pk``) or
    from a ``project`` query parameter. Without one, this denies: a
    project-scoped permission with no project is not a question that can be
    answered safely.
    """

    message = "You do not have permission to perform this action on this project."

    def _project_id(self, request: Request, view: Any) -> Any:
        kwargs = getattr(view, "kwargs", {}) or {}
        candidates = [
            kwargs.get("project_pk"),
            kwargs.get("project_id"),
            kwargs.get("pk") if getattr(view, "pk_is_project", False) else None,
            request.query_params.get("project"),
        ]

        data = getattr(request, "data", None)
        if isinstance(data, dict):
            candidates.append(data.get("project"))

        for candidate in candidates:
            if candidate:
                return candidate
        return None

    def has_permission(self, request: Request, view: Any) -> bool:
        required = getattr(view, "required_permission", None)
        if not required:
            return False
        project_id = self._project_id(request, view)
        if not project_id:
            return False
        context = access_for(request, project_id)
        return context is not None and context.has(required)


class IsOrganizationMember(BasePermission):
    """Requires an active membership. The floor for any authenticated action."""

    message = "You are not a member of an active organization."

    def has_permission(self, request: Request, view: Any) -> bool:
        context = access_for(request)
        return context is not None and context.organization_id is not None


class ReadOnly(BasePermission):
    """Allows safe methods only. Combined with others via ``|``."""

    def has_permission(self, request: Request, view: Any) -> bool:
        return request.method in ("GET", "HEAD", "OPTIONS")
