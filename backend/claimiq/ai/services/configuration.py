"""Resolving which models are in use, right now.

``settings.AI_SETTINGS`` is read from the environment at process start, which
makes a model choice a deployment act: edit a variable, rebuild, restart. The
docstring in ``base.py`` has long claimed the active model is resolved from a
table at call time; this module is that table's resolver, and every service
that needs a model name now goes through :func:`resolve_ai_settings` instead of
reading the settings dict directly.

Three properties the call sites depend on:

* **It never raises.** A resolver that can fail turns a model preference into
  an outage. Before the migration has run, or if the database is unreachable
  mid-request, it returns the environment values — the behaviour the
  installation had before this feature existed.
* **Blank means fall back.** An empty column is not a choice to use no model.
* **It is cached for seconds, not minutes.** An administrator who changes the
  model expects the next question to use it, and the ingestion worker holds a
  process for hours. A short TTL with explicit invalidation on save gets both
  without a per-call query.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache

from claimiq.core.logging import get_logger

logger = get_logger("ai.configuration")

_CACHE_KEY = "ai:model-configuration:overrides"
#: Short enough that a change is live almost immediately, long enough that a
#: batch of two hundred embedding calls does not make two hundred queries.
_CACHE_TTL_SECONDS = 20

#: Sentinel distinguishing "cached, and there are no overrides" from "not
#: cached". Without it an installation that has never configured anything
#: queries on every call.
_EMPTY: dict[str, str] = {}


def resolve_ai_settings() -> dict[str, Any]:
    """``AI_SETTINGS`` with the administrator's selections applied.

    Returns a new dict; callers may mutate it freely.
    """
    resolved = dict(settings.AI_SETTINGS)
    resolved.update(_overrides())
    # Drafting falls back to the answering model, which is what the setting
    # already documented and what drafting.py did by hand.
    if not resolved.get("DRAFTING_LLM_MODEL"):
        resolved["DRAFTING_LLM_MODEL"] = resolved.get("DEFAULT_LLM_MODEL") or ""
    return resolved


def active_hardware_profile() -> str:
    return str(resolve_ai_settings().get("HARDWARE_PROFILE") or "cpu")


def stored_embedding_dimensions() -> int:
    """Width of the ``embedding`` column, as the migration created it.

    Read from settings rather than from the configuration row on purpose: the
    column width is whatever the migration built, and no administrative choice
    can change it. It is the gate every embedding selection is checked against.
    """
    return int(settings.AI_SETTINGS["EMBEDDING_DIMENSIONS"])


def invalidate_configuration_cache() -> None:
    """Drop the cached overrides. Called whenever the row is written."""
    try:
        cache.delete(_CACHE_KEY)
    except Exception:  # noqa: BLE001 - a cache outage must not fail a save
        logger.warning("ai.configuration.cache_invalidate_failed")


def _overrides() -> dict[str, str]:
    cached = None
    try:
        cached = cache.get(_CACHE_KEY)
    except Exception:  # noqa: BLE001 - an unreachable cache means read through
        cached = None
    if cached is not None:
        return dict(cached)

    overrides = _read_overrides()
    try:
        cache.set(_CACHE_KEY, overrides, _CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return dict(overrides)


def _read_overrides() -> dict[str, str]:
    """Read the singleton, tolerating every way the read can fail.

    This is called from request paths, from Celery workers and from management
    commands, including during ``migrate`` before the table exists. A failure
    here degrades to environment configuration rather than to an error.
    """
    try:
        from claimiq.ai.models import ModelConfiguration

        row = ModelConfiguration.objects.filter(scope=ModelConfiguration.SCOPE).first()
    except Exception as exc:  # noqa: BLE001 - table missing, DB down, app loading
        logger.debug(
            "ai.configuration.unavailable", extra={"reason": type(exc).__name__}
        )
        return dict(_EMPTY)
    if row is None:
        return dict(_EMPTY)
    return row.overrides()
