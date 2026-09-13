"""Account serializers."""
from __future__ import annotations

from rest_framework import serializers

from claimiq.accounts.models import Organization, User


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name", "slug")
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """The current user.

    Deliberately narrow. It omits `is_superuser`, `is_staff`, lockout counters
    and the last-login IP — none of which the SPA needs, and all of which are
    information about the security posture of the account.
    """

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "job_title", "phone", "preferences")
        read_only_fields = ("id", "email")


class LoginSerializer(serializers.Serializer):
    """Credentials. Email, because `User.USERNAME_FIELD` is email."""

    email = serializers.EmailField()
    password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )


class SessionSerializer(serializers.Serializer):
    """What the SPA needs to render an authenticated shell."""

    user = UserSerializer(read_only=True)
    organization = OrganizationSerializer(read_only=True, allow_null=True)
    organization_role = serializers.CharField(read_only=True, allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_system_admin = serializers.BooleanField(read_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
