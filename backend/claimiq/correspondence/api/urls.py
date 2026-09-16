"""Correspondence routes, mounted under ``/api/v1/correspondence/``."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.correspondence.api.views import CorrespondenceViewSet, NoticeViewSet

app_name = "correspondence"

router = DefaultRouter()
# Registered before the bare-prefix route so "notices" is not read as an id.
router.register("notices", NoticeViewSet, basename="notice")
router.register("", CorrespondenceViewSet, basename="correspondence")

urlpatterns = [path("", include(router.urls))]
