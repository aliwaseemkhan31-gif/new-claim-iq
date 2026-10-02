"""Model administration endpoints, mounted under ``/api/v1/admin/ai/``.

- ``GET  models/``                  current selection, what is installed, latest run
- ``POST models/``                  apply a selection
- ``POST detect/``                  start a detection run
- ``GET  detect/<id>/``             progress and results for one run
- ``POST detect/<id>/cancel/``      stop at the next model boundary

All of it requires ``org.system.manage``: the selection is installation-wide,
so it is an operator's decision rather than a user preference.
"""
from __future__ import annotations

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import ORG_MANAGE_SYSTEM
from claimiq.ai.domain.benchmark import (
    ROLE_DRAFTING,
    ROLE_EMBEDDING,
    ROLE_LABELS,
    ROLE_LLM,
    SELECTABLE_ROLES,
)
from claimiq.ai.domain.hardware import profile_for
from claimiq.ai.domain.model_registry import (
    ALL_PROFILES,
    DEFAULT_MODEL_REGISTRY,
    same_model,
)
from claimiq.ai.models import ModelBenchmarkRun, ModelConfiguration
from claimiq.ai.services.capacity import probe_host_capacity
from claimiq.ai.services.configuration import (
    resolve_ai_settings,
    stored_embedding_dimensions,
)
from claimiq.ai.services.selection import (
    apply_selection,
    cancel_detection,
    start_detection,
)
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError


def _context(request: Request):
    context = access_for(request)
    if context is None or context.organization_id is None:
        raise PermissionDeniedError("You are not a member of an active organization.")
    context.require(ORG_MANAGE_SYSTEM.code)
    return context


def _run_payload(run: ModelBenchmarkRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": str(run.pk),
        "status": run.status,
        "current_step": run.current_step or None,
        "progress_percent": run.progress_percent,
        "include_pulls": run.include_pulls,
        "runtime_version": run.runtime_version or None,
        "host_capacity": run.host_capacity or None,
        "detected_profile": run.detected_profile or None,
        "profile_basis": run.profile_basis or None,
        "profile_explanation": run.profile_explanation or None,
        "probes": run.probes or [],
        "recommendations": run.recommendations or {},
        "warnings": run.warnings or [],
        "error_code": run.error_code or None,
        "error_message": run.error_message or None,
        "requested_by": run.requested_by.email if run.requested_by_id else None,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "is_finished": run.is_finished,
        "cancel_requested": run.cancel_requested,
    }


def _installed_models() -> tuple[bool, list[dict]]:
    """What the runtime has right now, with no benchmark needed.

    Cheap enough for a page load. The detection is what produces timings; this
    is only so the selection controls can be populated before one has been run.
    """
    from claimiq.ai.providers.ollama import OllamaClient, is_embedding_model

    ai = resolve_ai_settings()
    client = OllamaClient(ai["OLLAMA_BASE_URL"], 8)
    if not client.is_reachable():
        return False, []
    models = []
    for tag in client.tags():
        name = str(tag.get("name") or tag.get("model") or "")
        if not name:
            continue
        details = tag.get("details") or {}
        models.append(
            {
                "name": name,
                "size_bytes": tag.get("size"),
                "parameter_size": details.get("parameter_size") or None,
                "quantization": details.get("quantization_level") or None,
                "family": details.get("family") or None,
                # Generation or embedding. The interface filters each role's
                # choices by this: offering an embedding model as the
                # answering model is a misconfiguration that only shows up
                # later, as generation failing on every question.
                "kind": "embedding"
                if is_embedding_model(name, dict(details))
                else "generation",
            }
        )
    models.sort(key=lambda m: m["name"])
    return True, models


class ModelAdministrationView(APIView):
    """Read and change the models this installation uses."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        _context(request)

        ai = resolve_ai_settings()
        configuration = ModelConfiguration.load()
        reachable, installed = _installed_models()

        # The profile without a benchmark: the host probe only. Honest about
        # being a guess, because in the containerised deployment it usually is.
        capacity = probe_host_capacity()
        verdict = profile_for(capacity)
        active_profile = str(ai.get("HARDWARE_PROFILE") or "cpu")

        latest = ModelBenchmarkRun.objects.order_by("-created_at").first()
        active_run = ModelBenchmarkRun.objects.filter(
            status__in=("queued", "running")
        ).first()

        return Response(
            {
                "runtime_reachable": reachable,
                "installed": installed,
                "active": {
                    "llm": ai.get("DEFAULT_LLM_MODEL") or None,
                    "drafting_llm": ai.get("DRAFTING_LLM_MODEL") or None,
                    "embedding": ai.get("DEFAULT_EMBEDDING_MODEL") or None,
                    "reranker": ai.get("DEFAULT_RERANKER_MODEL") or None,
                    "hardware_profile": active_profile,
                },
                "configured": {
                    "llm": configuration.llm_model or None,
                    "drafting_llm": configuration.drafting_llm_model or None,
                    "embedding": configuration.embedding_model or None,
                    "hardware_profile": configuration.hardware_profile or None,
                    "source": configuration.source,
                    "updated_at": configuration.updated_at.isoformat(),
                    "updated_by": (
                        configuration.updated_by.email
                        if configuration.updated_by_id
                        else None
                    ),
                    "applied_from": (
                        str(configuration.applied_from_id)
                        if configuration.applied_from_id
                        else None
                    ),
                },
                "roles": [
                    {"role": role, "label": ROLE_LABELS[role]} for role in SELECTABLE_ROLES
                ],
                "embedding_dimensions_required": stored_embedding_dimensions(),
                "profile_estimate": {
                    **verdict.as_dict(),
                    "capacity": capacity.as_dict(),
                    "matches_active": verdict.profile == active_profile,
                },
                "profiles": list(ALL_PROFILES),
                "recommended_for_profile": {
                    role: [
                        {
                            "name": spec.name,
                            "label": spec.label(),
                            "provider": spec.provider,
                            "vram_gb": spec.estimated_vram_gb,
                            "notes": spec.notes,
                            "installed": any(
                                same_model(m["name"], spec.name) for m in installed
                            ),
                        }
                        for spec in DEFAULT_MODEL_REGISTRY.recommendations_for(
                            role, verdict.profile
                        )
                    ]
                    for role in ("llm", "embedding")
                },
                "latest_run": _run_payload(latest),
                "active_run": str(active_run.pk) if active_run else None,
            }
        )

    def post(self, request: Request) -> Response:
        """Apply a selection. Roles omitted from the body are left alone."""
        context = _context(request)

        body = request.data if isinstance(request.data, dict) else {}
        selections = {}
        for role, key in (
            (ROLE_LLM, "llm"),
            (ROLE_DRAFTING, "drafting_llm"),
            (ROLE_EMBEDDING, "embedding"),
        ):
            if key in body:
                value = body[key]
                if value is not None and not isinstance(value, str):
                    raise ValidationError(
                        "A model name must be a string.", details={"field": key}
                    )
                selections[role] = value or ""
        if not selections and "hardware_profile" not in body:
            raise ValidationError(
                "Nothing to apply.",
                details={"fields": ["llm", "drafting_llm", "embedding", "hardware_profile"]},
            )

        profile = body.get("hardware_profile") or None
        if profile and profile not in ALL_PROFILES:
            raise ValidationError(
                "Unknown hardware profile.",
                details={"field": "hardware_profile", "valid": list(ALL_PROFILES)},
            )

        run = None
        if run_id := body.get("from_run"):
            run = ModelBenchmarkRun.objects.filter(pk=run_id).first()
            if run is None:
                raise NotFoundError("The detection run does not exist.")

        configuration, warnings = apply_selection(
            selections,
            user=request.user,
            organization_id=context.organization_id,
            run=run,
            hardware_profile=profile,
        )
        ai = resolve_ai_settings()
        return Response(
            {
                "applied": {
                    "llm": configuration.llm_model or None,
                    "drafting_llm": configuration.drafting_llm_model or None,
                    "embedding": configuration.embedding_model or None,
                    "hardware_profile": configuration.hardware_profile or None,
                },
                "effective": {
                    "llm": ai.get("DEFAULT_LLM_MODEL") or None,
                    "drafting_llm": ai.get("DRAFTING_LLM_MODEL") or None,
                    "embedding": ai.get("DEFAULT_EMBEDDING_MODEL") or None,
                    "hardware_profile": ai.get("HARDWARE_PROFILE") or None,
                },
                "warnings": warnings,
            }
        )


class StartDetectionSerializer(serializers.Serializer):
    include_pulls = serializers.BooleanField(default=False)
    pull_targets = serializers.ListField(
        child=serializers.CharField(max_length=128), required=False, allow_empty=True
    )


class DetectionView(APIView):
    """Start a detection run."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        context = _context(request)
        serializer = StartDetectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        run = start_detection(
            organization_id=context.organization_id,
            user=request.user,
            include_pulls=data["include_pulls"],
            pull_targets=data.get("pull_targets") or [],
        )
        return Response(_run_payload(run), status=202)


class DetectionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _run(self, pk) -> ModelBenchmarkRun:
        run = ModelBenchmarkRun.objects.select_related("requested_by").filter(pk=pk).first()
        if run is None:
            raise NotFoundError("The detection run does not exist.")
        return run

    def get(self, request: Request, pk) -> Response:
        _context(request)
        return Response(_run_payload(self._run(pk)))


class DetectionCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, pk) -> Response:
        _context(request)
        run = DetectionDetailView()._run(pk)
        return Response(_run_payload(cancel_detection(run)))
