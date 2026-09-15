"""AI routes."""
from __future__ import annotations

from django.urls import include, path

from claimiq.ai.api.views import AskView, ModelStatusView

app_name = "ai"

urlpatterns = [
    path("ask/", AskView.as_view(), name="ask"),
    path("models/", ModelStatusView.as_view(), name="models"),
    # Claim analysis lives in its own app; mounted here because the SPA calls
    # /api/v1/ai/analysis/ and /api/v1/ai/findings/.
    path("", include("claimiq.analysis.api.urls")),
]
