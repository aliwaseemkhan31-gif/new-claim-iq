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
from claimiq.ai.api.admin_views import (
    DetectionCancelView,
    DetectionDetailView,
    DetectionView,
    ModelAdministrationView,
)

app_name = "admin-api"

urlpatterns = [
    path("roles/", RolesView.as_view(), name="roles"),
    path("users/", UsersView.as_view(), name="users"),
    path("users/<str:user_id>/", UserDetailView.as_view(), name="user-detail"),
    path("users/<str:user_id>/reset-password/", ResetPasswordView.as_view(), name="reset-password"),
    path("system/", SystemView.as_view(), name="system"),
    path("jobs/", JobsView.as_view(), name="jobs"),
    # Model selection lives in the ai app; mounted here because it is an
    # operator's decision and the SPA reads it from /api/v1/admin/.
    path("ai/models/", ModelAdministrationView.as_view(), name="ai-models"),
    path("ai/detect/", DetectionView.as_view(), name="ai-detect"),
    path("ai/detect/<uuid:pk>/", DetectionDetailView.as_view(), name="ai-detect-detail"),
    path(
        "ai/detect/<uuid:pk>/cancel/",
        DetectionCancelView.as_view(),
        name="ai-detect-cancel",
    ),
]
