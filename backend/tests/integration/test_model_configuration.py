"""The administrator's model choice reaches every processing path.

Before :mod:`claimiq.ai.services.configuration`, each service read
``settings.AI_SETTINGS`` directly, so a model choice was a deployment act. The
property under test is that one stored selection now governs answering,
drafting, retrieval and ingestion alike — and that the resolver degrades to the
environment rather than raising when the database cannot answer.

No database is needed: the configuration row is mocked, because what is under
test is the resolution, not the ORM.
"""
from __future__ import annotations

from unittest import mock

import pytest
from django.test import override_settings

from claimiq.ai.services import configuration as configuration_module

BASE_AI_SETTINGS = {
    "OLLAMA_BASE_URL": "http://ollama:11434",
    "OLLAMA_TIMEOUT_SECONDS": 300,
    "LLM_MAX_OUTPUT_TOKENS": 1536,
    "LLM_CONTEXT_TOKENS": 8192,
    "DEFAULT_LLM_MODEL": "env-llm",
    "DRAFTING_LLM_MODEL": "",
    "DEFAULT_EMBEDDING_MODEL": "env-embedding",
    "DEFAULT_RERANKER_MODEL": "",
    "EMBEDDING_DIMENSIONS": 1024,
    "HARDWARE_PROFILE": "cpu",
    "MODEL_CACHE_DIR": "/models",
}


@pytest.fixture(autouse=True)
def _no_cache():
    """Read through on every call, so each test sees its own overrides."""
    with mock.patch.object(configuration_module.cache, "get", return_value=None), \
            mock.patch.object(configuration_module.cache, "set"):
        yield


def resolve_with(overrides: dict) -> dict:
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        with mock.patch.object(
            configuration_module, "_read_overrides", return_value=overrides
        ):
            return configuration_module.resolve_ai_settings()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_with_nothing_configured_the_environment_governs():
    resolved = resolve_with({})
    assert resolved["DEFAULT_LLM_MODEL"] == "env-llm"
    assert resolved["DEFAULT_EMBEDDING_MODEL"] == "env-embedding"


def test_a_stored_selection_wins_over_the_environment():
    resolved = resolve_with({"DEFAULT_LLM_MODEL": "qwen2.5:14b-instruct"})
    assert resolved["DEFAULT_LLM_MODEL"] == "qwen2.5:14b-instruct"
    # Untouched keys still come from the environment.
    assert resolved["OLLAMA_BASE_URL"] == "http://ollama:11434"


def test_drafting_falls_back_to_the_answering_model():
    """The documented behaviour, which drafting.py used to do by hand."""
    resolved = resolve_with({"DEFAULT_LLM_MODEL": "chosen"})
    assert resolved["DRAFTING_LLM_MODEL"] == "chosen"


def test_a_separate_drafting_model_is_honoured():
    resolved = resolve_with(
        {"DEFAULT_LLM_MODEL": "small-fast", "DRAFTING_LLM_MODEL": "large-careful"}
    )
    assert resolved["DEFAULT_LLM_MODEL"] == "small-fast"
    assert resolved["DRAFTING_LLM_MODEL"] == "large-careful"


def test_the_resolved_dict_is_a_copy():
    """Call sites mutate it; the settings dict must not be touched."""
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        from django.conf import settings

        with mock.patch.object(configuration_module, "_read_overrides", return_value={}):
            resolved = configuration_module.resolve_ai_settings()
        resolved["DEFAULT_LLM_MODEL"] = "mutated"
        assert settings.AI_SETTINGS["DEFAULT_LLM_MODEL"] == "env-llm"


def test_a_database_failure_degrades_to_the_environment():
    """A resolver that can fail would turn a preference into an outage."""
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        with mock.patch(
            "claimiq.ai.models.ModelConfiguration.objects"
        ) as objects:
            objects.filter.side_effect = RuntimeError("relation does not exist")
            resolved = configuration_module.resolve_ai_settings()
    assert resolved["DEFAULT_LLM_MODEL"] == "env-llm"


def test_the_stored_column_width_is_never_overridden():
    """Width is whatever the migration built; no choice can change it."""
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        with mock.patch.object(
            configuration_module,
            "_read_overrides",
            return_value={"EMBEDDING_DIMENSIONS": 384},
        ):
            assert configuration_module.stored_embedding_dimensions() == 1024


def test_the_hardware_profile_is_resolved_too():
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        with mock.patch.object(
            configuration_module,
            "_read_overrides",
            return_value={"HARDWARE_PROFILE": "gpu-mid"},
        ):
            assert configuration_module.active_hardware_profile() == "gpu-mid"


# ---------------------------------------------------------------------------
# The selection reaches each service
# ---------------------------------------------------------------------------


def test_answering_uses_the_resolved_model():
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS)):
        with mock.patch.object(
            configuration_module,
            "_read_overrides",
            return_value={"DEFAULT_LLM_MODEL": "chosen-llm"},
        ):
            from claimiq.ai.services.answering import build_default_answering_service
            from claimiq.search.services import retrieval

            with mock.patch.object(retrieval, "build_default_service"):
                service = build_default_answering_service()
    assert service.llm_model == "chosen-llm"


def test_retrieval_uses_the_resolved_embedding_model():
    with override_settings(AI_SETTINGS=dict(BASE_AI_SETTINGS), RETRIEVAL_SETTINGS={}):
        with mock.patch.object(
            configuration_module,
            "_read_overrides",
            return_value={"DEFAULT_EMBEDDING_MODEL": "chosen-embedding"},
        ):
            from claimiq.search.services.retrieval import build_default_service

            service = build_default_service()
    assert service.embedding_model == "chosen-embedding"


def test_ingestion_reports_embeddings_unavailable_when_nothing_is_selected():
    settings_without_embedding = {**BASE_AI_SETTINGS, "DEFAULT_EMBEDDING_MODEL": ""}
    with override_settings(AI_SETTINGS=settings_without_embedding):
        with mock.patch.object(
            configuration_module, "_read_overrides", return_value={}
        ):
            from claimiq.ingestion.services.runner import embedding_provider_available

            assert embedding_provider_available() is False


def test_ingestion_sees_a_selection_made_after_the_process_started():
    """The worker holds its process for hours; a restart must not be needed."""
    settings_without_embedding = {**BASE_AI_SETTINGS, "DEFAULT_EMBEDDING_MODEL": ""}
    with override_settings(AI_SETTINGS=settings_without_embedding):
        with mock.patch.object(
            configuration_module,
            "_read_overrides",
            return_value={"DEFAULT_EMBEDDING_MODEL": "bge-m3"},
        ):
            from claimiq.ai.providers.ollama import OllamaEmbeddingProvider
            from claimiq.ingestion.services.runner import embedding_provider_available

            with mock.patch.object(
                OllamaEmbeddingProvider, "is_available", return_value=True
            ):
                assert embedding_provider_available() is True


# ---------------------------------------------------------------------------
# A run whose worker dies must not wedge the feature
# ---------------------------------------------------------------------------
#
# The unique constraint allows one active run per installation, which is right:
# two benchmarks measuring the same runtime queue behind each other inside
# Ollama and each reports the other's waiting as its own slowness. But a run
# left `running` by a dead worker would then block every future detection with
# no way out from the interface. Observed while developing this feature: a
# development thread died with its process and the next detection was refused.


def _stale_run(minutes: int = 45):
    """A run object that has not reported progress for ``minutes``."""
    from datetime import timedelta

    from django.utils import timezone

    from claimiq.ai.models import BenchmarkStatus, ModelBenchmarkRun

    return ModelBenchmarkRun(
        status=BenchmarkStatus.RUNNING,
        updated_at=timezone.now() - timedelta(minutes=minutes),
    )


def test_a_run_silent_past_the_threshold_is_stale():
    from claimiq.ai.services.selection import _is_stale

    assert _is_stale(_stale_run(45)) is True


def test_a_run_reporting_progress_is_not_stale():
    from claimiq.ai.services.selection import _is_stale

    assert _is_stale(_stale_run(1)) is False


def test_a_finished_run_is_never_stale():
    from claimiq.ai.models import BenchmarkStatus
    from claimiq.ai.services.selection import _is_stale

    run = _stale_run(500)
    run.status = BenchmarkStatus.COMPLETED
    assert _is_stale(run) is False


def test_a_fresh_active_run_blocks_a_second_detection():
    from claimiq.ai.models import ModelBenchmarkRun
    from claimiq.ai.services.selection import start_detection
    from claimiq.core.domain.errors import ConflictError

    with mock.patch.object(ModelBenchmarkRun, "objects") as objects:
        objects.filter.return_value.first.return_value = _stale_run(1)
        with pytest.raises(ConflictError):
            start_detection(organization_id=1, user=None)


def test_a_stale_run_is_retired_so_a_new_one_can_start():
    from claimiq.ai.models import ModelBenchmarkRun
    from claimiq.ai.services import selection

    stale = _stale_run(45)
    with mock.patch.object(ModelBenchmarkRun, "objects") as objects:
        objects.filter.return_value.first.return_value = stale
        objects.create.return_value = _stale_run(0)
        with mock.patch.object(selection, "_retire") as retire, \
                mock.patch.object(selection, "_dispatch"):
            selection.start_detection(organization_id=1, user=None)
    retire.assert_called_once_with(stale)


def test_cancelling_a_stale_run_closes_it_rather_than_asking_politely():
    """There is no worker left to cooperate with a cancellation request."""
    from claimiq.ai.models import ModelBenchmarkRun
    from claimiq.ai.services import selection

    stale = _stale_run(45)
    with mock.patch.object(ModelBenchmarkRun, "objects"), \
            mock.patch.object(selection, "_retire") as retire, \
            mock.patch.object(stale, "refresh_from_db"):
        selection.cancel_detection(stale)
    retire.assert_called_once()
