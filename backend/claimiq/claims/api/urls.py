"""Claim routes."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from claimiq.claims.api.views import ClaimEventViewSet, ClaimViewSet, ContractDeadlineViewSet

app_name = "claims"

router = DefaultRouter()
# Registered before the bare-prefix claim route so these are not swallowed by
# the detail lookup.
router.register("events", ClaimEventViewSet, basename="claim-event")
router.register("contract-deadlines", ContractDeadlineViewSet, basename="contract-deadline")
router.register("", ClaimViewSet, basename="claim")

urlpatterns = [path("", include(router.urls))]
