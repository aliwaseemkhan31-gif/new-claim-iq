"""AI routes."""
from __future__ import annotations

from django.urls import path

from claimiq.ai.api.views import AskView, ModelStatusView

app_name = "ai"

urlpatterns = [
    path("ask/", AskView.as_view(), name="ask"),
    path("models/", ModelStatusView.as_view(), name="models"),
]
