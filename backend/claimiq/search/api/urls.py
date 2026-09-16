"""Search routes, mounted under ``/api/v1/search/``."""
from __future__ import annotations

from django.urls import path

from claimiq.search.api.views import SearchView

app_name = "search"

urlpatterns = [path("", SearchView.as_view(), name="search")]
