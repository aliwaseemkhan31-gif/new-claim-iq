"""Health endpoint routes.

Each accepts an optional trailing slash. The container healthchecks and nginx
call ``/api/v1/health/live`` with no slash; the SPA's API client calls
``/api/v1/health/ready/`` with one, like every other route it uses. Django's
APPEND_SLASH only redirects a missing slash, never the reverse, so a
slash-only or slash-free route silently 404s for one of the two callers — which
is exactly how the dashboard's System Status card came to report "health checks
unavailable" against a perfectly healthy backend.
"""
from __future__ import annotations

from django.urls import re_path

from claimiq.core.api.health import LivenessView, ReadinessView, SystemStatusView, VersionView

app_name = "health"

urlpatterns = [
    re_path(r"^live/?$", LivenessView.as_view(), name="live"),
    re_path(r"^ready/?$", ReadinessView.as_view(), name="ready"),
    re_path(r"^status/?$", SystemStatusView.as_view(), name="status"),
    re_path(r"^version/?$", VersionView.as_view(), name="version"),
]
