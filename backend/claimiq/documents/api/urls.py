"""Document routes."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.documents.api.views import (
    DocumentViewSet,
    ProcessingJobViewSet,
    TaxonomyView,
)

app_name = "documents"

router = DefaultRouter()
# Registered before the bare-prefix document route so `jobs/` and `taxonomy/`
# are not swallowed by the detail lookup.
router.register("jobs", ProcessingJobViewSet, basename="job")
router.register("", DocumentViewSet, basename="document")

urlpatterns = [
    path("taxonomy/", TaxonomyView.as_view(), name="taxonomy"),
    path("", include(router.urls)),
]
