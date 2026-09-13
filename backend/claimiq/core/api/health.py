"""Health, readiness and liveness endpoints.

Three distinct checks, because a container runtime needs to tell three
different situations apart:

- **live** — the process is running. Never touches a dependency. If this fails
  the process is wedged and should be restarted.
- **ready** — dependencies are reachable and the schema is migrated. A failure
  means "do not send traffic yet", not "restart me". Restarting a healthy app
  because PostgreSQL is briefly unavailable turns a blip into an outage.
- **status** — a detailed authenticated report for operators.
"""
from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.db import connection
from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.core.logging import get_logger

logger = get_logger("core.health")


class LivenessView(APIView):
    """Process liveness. Deliberately checks nothing external."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        return Response({"status": "alive"})


def _check_database() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        logger.error("health.database_unavailable", extra={"error": str(exc)})
        return {"ok": False, "error": "unreachable"}


def _check_pgvector() -> dict[str, Any]:
    """Confirm the vector extension is installed.

    Worth its own check: without it every retrieval query fails, and the error
    it produces otherwise is opaque.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cursor.fetchone()
        if row is None:
            return {"ok": False, "error": "extension_not_installed"}
        return {"ok": True, "version": row[0]}
    except Exception:
        return {"ok": False, "error": "unreachable"}


def _check_migrations() -> dict[str, Any]:
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return {"ok": not pending, "pending": len(pending)}
    except Exception:
        return {"ok": False, "error": "unreadable"}


def _check_cache() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cache.set("claimiq:health", "1", timeout=10)
        value = cache.get("claimiq:health")
        return {
            "ok": value == "1",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception:
        return {"ok": False, "error": "unreachable"}


class ReadinessView(APIView):
    """Dependency readiness. 503 when the service should not receive traffic."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        checks = {
            "database": _check_database(),
            "pgvector": _check_pgvector(),
            "migrations": _check_migrations(),
            "cache": _check_cache(),
        }
        ready = all(check["ok"] for check in checks.values())
        return Response(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class SystemStatusView(APIView):
    """Detailed operator report. Authenticated: it names internal components."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        ai_settings = settings.AI_SETTINGS
        return Response(
            {
                "application": "claimiq-enterprise",
                "checks": {
                    "database": _check_database(),
                    "pgvector": _check_pgvector(),
                    "migrations": _check_migrations(),
                    "cache": _check_cache(),
                },
                "configuration": {
                    "hardware_profile": ai_settings["HARDWARE_PROFILE"],
                    "embedding_dimensions": ai_settings["EMBEDDING_DIMENSIONS"],
                    # Whether a provider is *configured*, not what it is. The
                    # base URL is internal topology and is not exposed here.
                    "llm_configured": bool(ai_settings["DEFAULT_LLM_MODEL"]),
                    "embedding_configured": bool(ai_settings["DEFAULT_EMBEDDING_MODEL"]),
                },
            }
        )
