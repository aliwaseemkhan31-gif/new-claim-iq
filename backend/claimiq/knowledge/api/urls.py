"""Knowledge base routes, mounted under ``/api/v1/knowledge/``."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.knowledge.api.views import EditionCatalogView, KnowledgeBaseViewSet

app_name = "knowledge"

router = DefaultRouter()
router.register("bases", KnowledgeBaseViewSet, basename="knowledge-base")

urlpatterns = [
    path("editions/", EditionCatalogView.as_view(), name="editions"),
    path("", include(router.urls)),
]
