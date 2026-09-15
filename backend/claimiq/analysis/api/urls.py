"""Claim analysis routes, mounted under ``/api/v1/ai/``."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.analysis.api.views import AnalysisViewSet, FindingReviewView

router = DefaultRouter()
router.register("analysis", AnalysisViewSet, basename="analysis")

urlpatterns = [
    path(
        "findings/<str:finding_id>/review/",
        FindingReviewView.as_view(),
        name="finding-review",
    ),
    path("", include(router.urls)),
]
