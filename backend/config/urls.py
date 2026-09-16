"""Root URL configuration.

Every application route lives under ``/api/v1/`` and is namespaced by domain.
Versioning is in the path from the first release: adding it later means
breaking clients or maintaining an unversioned alias forever.
"""
from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

# Routes are added here as their viewsets are implemented, not before.
# Listing a route whose module does not exist crashes URL resolution at
# startup — every request, not just that route — which is how this list looked
# before it was first run against a real Django.
#
# Outstanding: search/, correspondence/, reports/, admin/. The frontend's api/
# modules already target these paths, so adding each one is wiring a viewset to
# a path the client is already calling.
api_v1 = [
    path("health/", include("claimiq.core.api.health_urls")),
    path("auth/", include("claimiq.accounts.api.auth_urls")),
    path("projects/", include("claimiq.projects.api.urls")),
    path("documents/", include("claimiq.documents.api.urls")),
    path("claims/", include("claimiq.claims.api.urls")),
    path("evidence/", include("claimiq.claims.api.evidence_urls")),
    path("ai/", include("claimiq.ai.api.urls")),
    path("knowledge/", include("claimiq.knowledge.api.urls")),
    path("search/", include("claimiq.search.api.urls")),
    path("correspondence/", include("claimiq.correspondence.api.urls")),
    path("notifications/", include("claimiq.notifications.api.urls")),
    path("dashboard/", include("claimiq.dashboard.urls")),
    path("reports/", include("claimiq.reports.api.urls")),
    path("admin/", include("claimiq.accounts.api.admin_urls")),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include((api_v1, "v1"))),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
