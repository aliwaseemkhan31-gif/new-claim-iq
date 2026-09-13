"""Authentication routes."""
from __future__ import annotations

from django.urls import path

from claimiq.accounts.api.auth_views import (
    ChangePasswordView,
    CsrfView,
    CurrentUserView,
    LoginView,
    LogoutView,
    ProjectPermissionsView,
)

app_name = "auth"

urlpatterns = [
    path("csrf/", CsrfView.as_view(), name="csrf"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("me/permissions/", ProjectPermissionsView.as_view(), name="me-permissions"),
    path("password/change/", ChangePasswordView.as_view(), name="password-change"),
]
