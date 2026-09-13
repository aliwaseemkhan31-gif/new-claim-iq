"""Project routes."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.projects.api.views import EditionListView, ProjectViewSet

app_name = "projects"

router = DefaultRouter()
router.register("editions", EditionListView, basename="edition")
router.register("", ProjectViewSet, basename="project")

urlpatterns = [path("", include(router.urls))]
