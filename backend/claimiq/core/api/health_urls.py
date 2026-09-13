"""Health endpoint routes."""
from __future__ import annotations

from django.urls import path

from claimiq.core.api.health import LivenessView, ReadinessView, SystemStatusView

app_name = "health"

urlpatterns = [
    path("live", LivenessView.as_view(), name="live"),
    path("ready", ReadinessView.as_view(), name="ready"),
    path("status", SystemStatusView.as_view(), name="status"),
]
