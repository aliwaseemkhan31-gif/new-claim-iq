"""Administration endpoints, mounted under ``/api/v1/admin/``.

- ``GET        roles/``                        role catalogue with permissions
- ``GET/POST   users/``                        organization members; add a user
- ``PATCH      users/{id}/``                   name, role, active
- ``POST       users/{id}/reset-password/``    set a new password and unlock
- ``GET        system/``                       database, vectors, models, OCR, workers
- ``GET        jobs/``                         ingestion jobs and analysis runs

Role changes and deactivation invalidate cached access immediately, so a
revocation bites on the next request rather than when a cache expires.
"""
from __future__ import annotations

from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import (
    ORG_MANAGE_ROLES,
    ORG_MANAGE_SYSTEM,
    ORG_MANAGE_USERS,
    ORGANIZATION_ROLES,
    PROJECT_ROLES,
    ROLE_SYSTEM_ADMINISTRATOR,
    ROLES_BY_CODE,
)
from claimiq.accounts.models import OrganizationMembership, User
from claimiq.accounts.services.access import invalidate_access
from claimiq.core.api.pagination import StandardPagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


def _context(request: Request, permission: str | None = None):
    context = access_for(request)
    if context is None or context.organization_id is None:
        raise PermissionDeniedError("You are not a member of an active organization.")
    if permission:
        context.require(permission)
    return context


def _role_payload(role) -> dict:
    return {
        "code": role.code,
        "label": role.label,
        "description": role.description,
        "scope": role.scope,
        "permissions": sorted(role.permissions),
    }


def _member_payload(membership: OrganizationMembership, project_counts: dict) -> dict:
    user = membership.user
    role = ROLES_BY_CODE.get(membership.role)
    return {
        "id": str(user.pk),
        "email": user.email,
        "full_name": user.full_name,
        "job_title": user.job_title,
        "is_active": user.is_active and membership.is_active,
        "role": membership.role,
        "role_label": role.label if role else membership.role,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "is_locked": user.is_locked,
        "failed_login_attempts": user.failed_login_attempts,
        "projects": project_counts.get(user.pk, 0),
        "joined_at": membership.created_at.isoformat(),
    }


def _validated_password(password: str, user: User | None = None) -> str:
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise ValidationError(
            "The password does not meet the password policy.",
            details={"field": "password", "errors": list(exc.messages)},
        ) from None
    return password


def _stage_detail(job) -> str:
    """Progress within a job's running stage, in that stage's own units."""
    try:
        return job.load_state().stage_detail()
    except Exception:  # noqa: BLE001 - a malformed legacy state is not a server error
        return ""


class RolesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        _context(request)
        return Response(
            {
                "organization_roles": [_role_payload(r) for r in ORGANIZATION_ROLES],
                "project_roles": [_role_payload(r) for r in PROJECT_ROLES],
            }
        )


class CreateUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    job_title = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    role = serializers.CharField()
    password = serializers.CharField(required=False, allow_blank=True, default="", write_only=True)


class UpdateUserSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    job_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    role = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)


def _check_assignable(context, role_code: str) -> None:
    valid = {r.code for r in ORGANIZATION_ROLES}
    if role_code not in valid:
        raise ValidationError(
            "Unknown organization role.", details={"field": "role", "valid": sorted(valid)}
        )
    # Only a system administrator can create another.
    if role_code == ROLE_SYSTEM_ADMINISTRATOR.code and not context.is_system_admin:
        raise PermissionDeniedError(
            "Only a System Administrator can grant the System Administrator role."
        )


class UsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        context = _context(request, ORG_MANAGE_USERS.code)
        from django.db.models import Count

        from claimiq.projects.models import ProjectMember

        memberships = OrganizationMembership.objects.select_related("user").filter(
            organization_id=context.organization_id
        ).order_by("user__email")
        if search := request.query_params.get("search"):
            memberships = memberships.filter(user__email__icontains=search) | memberships.filter(
                user__full_name__icontains=search
            )
        counts = dict(
            ProjectMember.objects.filter(
                is_active=True, project__organization_id=context.organization_id
            )
            .values("user_id")
            .annotate(n=Count("id"))
            .values_list("user_id", "n")
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(memberships, request, view=self)
        return paginator.get_paginated_response([_member_payload(m, counts) for m in page])

    @transaction.atomic
    def post(self, request: Request) -> Response:
        context = _context(request, ORG_MANAGE_USERS.code)
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        _check_assignable(context, data["role"])
        email = data["email"].strip().lower()

        user = User.objects.filter(email=email).first()
        if user is None:
            if not data["password"]:
                raise ValidationError(
                    "A new user needs an initial password.", details={"field": "password"}
                )
            user = User(email=email, full_name=data["full_name"], job_title=data["job_title"])
            _validated_password(data["password"], user)
            user.set_password(data["password"])
            user.password_changed_at = timezone.now()
            user.save()
        elif OrganizationMembership.objects.filter(
            user=user, organization_id=context.organization_id
        ).exists():
            raise ConflictError(
                "This user is already a member of the organization.", details={"email": email}
            )

        membership = OrganizationMembership.objects.create(
            organization_id=context.organization_id,
            user=user,
            role=data["role"],
            created_by=request.user,
            accepted_at=timezone.now(),
        )
        invalidate_access(user.pk)
        return Response(_member_payload(membership, {}), status=201)


class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _membership(self, context, user_id) -> OrganizationMembership:
        try:
            uid = UUID(str(user_id))
        except ValueError:
            raise NotFoundError("The requested user does not exist.") from None
        membership = (
            OrganizationMembership.objects.select_related("user")
            .filter(organization_id=context.organization_id, user_id=uid)
            .first()
        )
        if membership is None:
            raise NotFoundError("The requested user does not exist.")
        return membership

    @transaction.atomic
    def patch(self, request: Request, user_id) -> Response:
        context = _context(request, ORG_MANAGE_USERS.code)
        membership = self._membership(context, user_id)
        serializer = UpdateUserSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = membership.user
        is_self = user.pk == request.user.pk

        if "role" in data and data["role"] != membership.role:
            context.require(ORG_MANAGE_ROLES.code)
            if is_self:
                raise ValidationError("You cannot change your own role.")
            if membership.role == ROLE_SYSTEM_ADMINISTRATOR.code and not context.is_system_admin:
                raise PermissionDeniedError("Only a System Administrator can change that role.")
            _check_assignable(context, data["role"])
            membership.role = data["role"]
        if "is_active" in data:
            if is_self and not data["is_active"]:
                raise ValidationError("You cannot deactivate your own account.")
            membership.is_active = data["is_active"]
            user.is_active = data["is_active"]
        for field in ("full_name", "job_title"):
            if field in data:
                setattr(user, field, data[field])

        user.save()
        membership.save()
        invalidate_access(user.pk)
        return Response(_member_payload(membership, {}))


class ResetPasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, user_id) -> Response:
        context = _context(request, ORG_MANAGE_USERS.code)
        membership = UserDetailView()._membership(context, user_id)
        password = str(request.data.get("password") or "")
        user = membership.user
        _validated_password(password, user)
        user.set_password(password)
        user.password_changed_at = timezone.now()
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["password", "password_changed_at", "failed_login_attempts", "locked_until", "updated_at"])
        return Response({"id": str(user.pk), "password_reset": True, "is_locked": False})


class SystemView(APIView):
    """What an operator needs to know is working, with nothing guessed."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        context = _context(request)
        if not (context.has(ORG_MANAGE_SYSTEM.code) or context.has(ORG_MANAGE_USERS.code)):
            context.require(ORG_MANAGE_SYSTEM.code)

        from django.conf import settings

        from claimiq.ai.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
        from claimiq.core.api.health import (
            _check_cache,
            _check_database,
            _check_migrations,
            _check_pgvector,
        )
        from claimiq.ingestion.providers.extraction import DocTROCRProvider, RapidOCRProvider

        from claimiq.ai.services.configuration import resolve_ai_settings

        ai = resolve_ai_settings()
        llm = OllamaLLMProvider(ai["OLLAMA_BASE_URL"], 5)
        reachable = llm.is_available()
        llm_models = [m.name for m in llm.list_models()] if reachable else []
        embedding_models = (
            [m.name for m in OllamaEmbeddingProvider(ai["OLLAMA_BASE_URL"], 5).list_models()]
            if reachable
            else []
        )

        def present(configured: str, available: list[str]) -> bool | None:
            if not configured:
                return None
            return any(name == configured or name.split(":")[0] == configured.split(":")[0] for name in available)

        return Response(
            {
                "checks": {
                    "database": _check_database(),
                    "pgvector": _check_pgvector(),
                    "migrations": _check_migrations(),
                    "cache": _check_cache(),
                },
                "ai_runtime": {
                    "reachable": reachable,
                    "hardware_profile": ai["HARDWARE_PROFILE"],
                    "llm": {"configured": ai.get("DEFAULT_LLM_MODEL") or None, "installed": present(ai.get("DEFAULT_LLM_MODEL") or "", llm_models)},
                    "embedding": {
                        "configured": ai.get("DEFAULT_EMBEDDING_MODEL") or None,
                        "installed": present(ai.get("DEFAULT_EMBEDDING_MODEL") or "", embedding_models),
                        "dimensions": ai["EMBEDDING_DIMENSIONS"],
                    },
                    "available_models": sorted(set(llm_models + embedding_models)),
                },
                "ocr": {
                    "rapidocr": RapidOCRProvider().is_available(),
                    "doctr": DocTROCRProvider().is_available(),
                },
                "workers": {
                    "broker": "redis" if getattr(settings, "USE_REDIS", True) else "none",
                    "tasks_run_in_process": bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)),
                },
            }
        )


class JobsView(APIView):
    """Recent ingestion jobs (including knowledge-base sources) and analysis runs."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        context = _context(request)
        if not (context.has(ORG_MANAGE_SYSTEM.code) or context.has(ORG_MANAGE_USERS.code)):
            context.require(ORG_MANAGE_SYSTEM.code)

        from django.db.models import Q

        from claimiq.analysis.models import ClaimAnalysis
        from claimiq.ingestion.models import ProcessingJob

        jobs = (
            ProcessingJob.objects.select_related("document_version__document__project")
            .filter(
                Q(document_version__document__project__organization_id=context.organization_id)
                | Q(document_version__document__organization_id=context.organization_id)
            )
            .order_by("-created_at")[:50]
        )
        runs = (
            ClaimAnalysis.objects.select_related("claim", "project")
            .filter(project__organization_id=context.organization_id)
            .order_by("-created_at")[:25]
        )
        return Response(
            {
                "ingestion": [
                    {
                        "id": str(job.pk),
                        "document_id": str(job.document_version.document_id),
                        "document_title": job.document_version.document.title,
                        "project": (
                            job.document_version.document.project.name
                            if job.document_version.document.project_id
                            else None
                        ),
                        "is_reference_document": job.document_version.document.project_id is None,
                        "status": job.status,
                        "current_stage": job.current_stage or None,
                        "progress_percent": job.progress_percent,
                        # Whole-stage progress does not move during a long OCR
                        # run, which reads as a stalled job.
                        "stage_detail": _stage_detail(job),
                        "error_message": job.error_message or None,
                        "created_at": job.created_at.isoformat(),
                        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    }
                    for job in jobs
                ],
                "analysis": [
                    {
                        "id": str(run.pk),
                        "claim_id": str(run.claim_id),
                        "claim": run.claim.reference or run.claim.title,
                        "project": run.project.name,
                        "status": run.status,
                        "progress_percent": run.progress_percent,
                        "error_message": run.error_message or None,
                        "created_at": run.created_at.isoformat(),
                        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                    }
                    for run in runs
                ],
            }
        )
