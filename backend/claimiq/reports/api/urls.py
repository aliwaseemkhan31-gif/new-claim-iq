"""Report routes, mounted under ``/api/v1/reports/``."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.reports.api.views import ReportViewSet

app_name = "reports"

router = DefaultRouter()
router.register("", ReportViewSet, basename="report")

urlpatterns = [path("", include(router.urls))]
