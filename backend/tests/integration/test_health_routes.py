"""Health routes must answer both slash forms.

Regression for a live contract mismatch: the SPA requested
``/api/v1/health/ready/`` while the route was ``health/ready``, so the dashboard
reported "health checks unavailable" against a healthy backend. The container
healthchecks and nginx use the slash-free form, so both must resolve.
"""
from __future__ import annotations

import pytest
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from claimiq.core.api.health import (
    LivenessView,
    ReadinessView,
    SystemStatusView,
    VersionView,
    application_version,
)

EXPECTED = {
    "live": LivenessView,
    "ready": ReadinessView,
    "status": SystemStatusView,
    "version": VersionView,
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("suffix", ["", "/"])
def test_route_resolves_with_and_without_trailing_slash(name: str, suffix: str) -> None:
    match = resolve(f"/api/v1/health/{name}{suffix}")
    assert match.func.view_class is EXPECTED[name]


def test_liveness_needs_no_dependencies_or_authentication() -> None:
    request = APIRequestFactory().get("/api/v1/health/live/")
    response = LivenessView.as_view()(request)
    assert response.status_code == 200
    assert response.data == {"status": "alive"}


def test_version_requires_authentication() -> None:
    request = APIRequestFactory().get("/api/v1/health/version/")
    response = VersionView.as_view()(request)
    assert response.status_code in (401, 403)


def test_version_reports_the_packaged_version() -> None:
    from claimiq.accounts.models import User

    request = APIRequestFactory().get("/api/v1/health/version/")
    force_authenticate(request, user=User(email="reader@localhost"))
    response = VersionView.as_view()(request)

    assert response.status_code == 200
    assert response.data["application"] == "claimiq-enterprise"
    assert response.data["api_version"] == "v1"
    assert response.data["version"] == application_version()
    assert response.data["version"] != "unknown", "pyproject.toml must be found"
