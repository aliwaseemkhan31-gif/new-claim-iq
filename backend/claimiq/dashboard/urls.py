"""Dashboard route, mounted at ``/api/v1/dashboard/``."""
from __future__ import annotations

from django.urls import path

from claimiq.dashboard.views import DashboardView

app_name = "dashboard"

urlpatterns = [path("", DashboardView.as_view(), name="dashboard")]
