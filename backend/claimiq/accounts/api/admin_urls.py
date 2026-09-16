"""Administration routes, mounted under ``/api/v1/admin/``."""
from __future__ import annotations

from django.urls import path

from claimiq.accounts.api.admin_views import (
    JobsView,
    ResetPasswordView,
    RolesView,
    SystemView,
    UserDetailView,
    UsersView,
)

app_name = "admin-api"

urlpatterns = [
    path("roles/", RolesView.as_view(), name="roles"),
    path("users/", UsersView.as_view(), name="users"),
    path("users/<str:user_id>/", UserDetailView.as_view(), name="user-detail"),
    path("users/<str:user_id>/reset-password/", ResetPasswordView.as_view(), name="reset-password"),
    path("system/", SystemView.as_view(), name="system"),
    path("jobs/", JobsView.as_view(), name="jobs"),
]
