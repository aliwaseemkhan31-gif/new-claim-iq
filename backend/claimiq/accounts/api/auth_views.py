"""Authentication endpoints.

Session-cookie authentication. Chosen over tokens because the client is a
first-party SPA served from the same origin: an HttpOnly session cookie cannot
be read by injected script, whereas a JWT in localStorage can.
"""
from __future__ import annotations

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.api.serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    OrganizationSerializer,
    UserSerializer,
)
from claimiq.accounts.services.access import invalidate_access, resolve_access
from claimiq.core.domain.errors import ValidationError
from claimiq.core.logging import get_logger

logger = get_logger("accounts.auth")

#: Failed attempts before the account is locked.
MAX_FAILED_ATTEMPTS = 10
LOCKOUT_MINUTES = 15


def _session_payload(user) -> dict:
    context = resolve_access(user)
    organization = None
    if context.organization_id:
        from claimiq.accounts.models import Organization

        organization = Organization.objects.filter(pk=context.organization_id).first()

    return {
        "user": UserSerializer(user).data,
        "organization": OrganizationSerializer(organization).data if organization else None,
        "organization_role": context.organization_role,
        "permissions": sorted(context.permissions),
        "is_system_admin": context.is_system_admin,
    }


class CsrfView(APIView):
    """Sets the CSRF cookie. Called before the first unsafe request."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        get_token(request)
        return Response({"detail": "CSRF cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        password = serializer.validated_data["password"]

        from claimiq.accounts.models import User

        existing = User.objects.filter(email=email).first()
        if existing is not None and existing.is_locked:
            logger.warning("auth.login_locked", extra={"user_id": str(existing.pk)})
            return Response(
                {
                    "error": {
                        "code": "account_locked",
                        "message": (
                            "This account is temporarily locked after repeated "
                            "failed sign-in attempts. Try again later, or ask an "
                            "administrator to unlock it."
                        ),
                        "details": {},
                        "request_id": getattr(request, "request_id", "-"),
                    }
                },
                status=status.HTTP_423_LOCKED,
            )

        user = authenticate(request, username=email, password=password)

        if user is None:
            if existing is not None:
                self._record_failure(existing)
            # Same message and status whether the address is unknown or the
            # password is wrong: distinguishing them turns the endpoint into an
            # account-enumeration oracle.
            logger.info("auth.login_failed", extra={"email_domain": email.split("@")[-1]})
            return Response(
                {
                    "error": {
                        "code": "authentication_failed",
                        "message": "The email address or password is incorrect.",
                        "details": {},
                        "request_id": getattr(request, "request_id", "-"),
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {
                    "error": {
                        "code": "account_disabled",
                        "message": "This account has been disabled.",
                        "details": {},
                        "request_id": getattr(request, "request_id", "-"),
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        self._record_success(request, user)
        logger.info("auth.login_succeeded", extra={"user_id": str(user.pk)})
        return Response(_session_payload(user))

    def _record_failure(self, user) -> None:
        user.failed_login_attempts += 1
        fields = ["failed_login_attempts", "updated_at"]
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = timezone.now() + timezone.timedelta(minutes=LOCKOUT_MINUTES)
            fields.append("locked_until")
            logger.warning("auth.account_locked", extra={"user_id": str(user.pk)})
        user.save(update_fields=fields)

    def _record_success(self, request: Request, user) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        user.last_login_ip = (
            forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
        )
        user.save(
            update_fields=["failed_login_attempts", "locked_until", "last_login_ip", "updated_at"]
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        user_id = str(request.user.pk)
        logout(request)
        logger.info("auth.logout", extra={"user_id": user_id})
        return Response({"detail": "Signed out."})


class CurrentUserView(APIView):
    """The session. The SPA's source of truth for "am I signed in?"."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(_session_payload(request.user))

    def patch(self, request: Request) -> Response:
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(_session_payload(request.user))


class ProjectPermissionsView(APIView):
    """Effective permissions for one project — the organization role unioned
    with the project role."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        project_id = request.query_params.get("project")
        if not project_id:
            raise ValidationError("A 'project' query parameter is required.")
        context = resolve_access(request.user, project_id=project_id)
        return Response(
            {
                "project": project_id,
                "project_role": context.project_role,
                "permissions": sorted(context.permissions),
            }
        )


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request: Request) -> Response:
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        if not user.check_password(serializer.validated_data["current_password"]):
            raise ValidationError(
                "The current password is incorrect.",
                details={"field": "current_password"},
            )

        new_password = serializer.validated_data["new_password"]
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise ValidationError(
                "The new password does not meet the password policy.",
                details={"errors": list(exc.messages)},
            ) from None

        user.set_password(new_password)
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "password_changed_at", "updated_at"])
        # Keep this session valid; every other session for the user is
        # invalidated by the password change.
        update_session_auth_hash(request, user)
        invalidate_access(user.pk)

        logger.info("auth.password_changed", extra={"user_id": str(user.pk)})
        return Response({"detail": "Password changed."})
