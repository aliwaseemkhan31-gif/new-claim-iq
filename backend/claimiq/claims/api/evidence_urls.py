"""Evidence routes.

A separate module because `include()` reads a module's `urlpatterns`, so two
route sets cannot share one file under different namespaces.
"""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.claims.api.views import EvidenceViewSet

app_name = "evidence"

router = DefaultRouter()
router.register("", EvidenceViewSet, basename="evidence")

urlpatterns = [path("", include(router.urls))]
