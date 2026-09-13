"""Typed error hierarchy for the ClaimIQ domain.

Every error carries a stable, machine-readable ``code`` and a human-readable
message. Nothing in this module imports Django; the interface layer maps these
onto HTTP status codes.

Design rule (see ADR 0005): a failure is *raised*. It is never returned as a
string, never becomes LLM context, and never degrades into an answer. The legacy
prototype returned ``f"FIDIC KB unavailable: {e}"`` as retrieval context, so an
infrastructure failure could be read by the model as if it were contract text.
That class of bug is unrepresentable here.
"""
from __future__ import annotations

from typing import Any, Mapping


class ClaimIQError(Exception):
    """Base class for every domain error.

    Attributes:
        code: Stable machine-readable identifier. Safe to expose to clients and
            to branch on. Must not change once released.
        message: Human-readable description. Safe to expose to clients.
        details: Structured context for the caller. Must not contain secrets,
            credentials, absolute filesystem paths, or raw stack traces.
    """

    code = "claimiq_error"
    http_status = 500

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Render as the standard API error envelope body."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


# --------------------------------------------------------------------------
# Client-caused errors (4xx)
# --------------------------------------------------------------------------


class ValidationError(ClaimIQError):
    code = "validation_error"
    http_status = 400


class NotFoundError(ClaimIQError):
    code = "not_found"
    http_status = 404


class PermissionDeniedError(ClaimIQError):
    code = "permission_denied"
    http_status = 403


class ConflictError(ClaimIQError):
    code = "conflict"
    http_status = 409


class UnsupportedMediaTypeError(ClaimIQError):
    code = "unsupported_media_type"
    http_status = 415


class PayloadTooLargeError(ClaimIQError):
    code = "payload_too_large"
    http_status = 413


# --------------------------------------------------------------------------
# Document ingestion
# --------------------------------------------------------------------------


class IngestionError(ClaimIQError):
    code = "ingestion_error"
    http_status = 422


class FileSafetyError(IngestionError):
    """File failed validation before any parser was allowed to touch it."""

    code = "file_safety_rejected"
    http_status = 400


class ExtractionError(IngestionError):
    code = "extraction_failed"


class OCRError(IngestionError):
    code = "ocr_failed"


class DocumentStructureError(IngestionError):
    code = "document_structure_invalid"


# --------------------------------------------------------------------------
# Retrieval and knowledge scope
# --------------------------------------------------------------------------


class RetrievalError(ClaimIQError):
    code = "retrieval_error"
    http_status = 503


class ScopeValidationError(ValidationError):
    """A retrieval scope was constructed that cannot be safely executed.

    The dominant case: a scope that reaches a knowledge base without naming an
    edition. See ADR 0004 — this is refused rather than defaulted, because a
    default silently decides which contract governs.
    """

    code = "retrieval_scope_invalid"


class EditionMixingError(RetrievalError):
    """Post-retrieval verification found a chunk from outside the scoped edition.

    This is defence in depth. It should be unreachable while filtering is
    correct; if it fires, a filter was dropped and the response must not be
    produced. Raising is mandatory — degrading to a warning would reintroduce
    exactly the failure ADR 0004 exists to prevent.
    """

    code = "edition_mixing_detected"
    http_status = 500


class KnowledgeBaseUnavailableError(RetrievalError):
    code = "knowledge_base_unavailable"


# --------------------------------------------------------------------------
# AI providers and grounding
# --------------------------------------------------------------------------


class AIError(ClaimIQError):
    code = "ai_error"
    http_status = 503


class ProviderUnavailableError(AIError):
    code = "ai_provider_unavailable"


class ModelNotConfiguredError(AIError):
    code = "ai_model_not_configured"
    http_status = 500


class StructuredOutputError(AIError):
    """Model output did not conform to the declared schema after retries."""

    code = "ai_structured_output_invalid"


class GroundingError(AIError):
    """Base for every failure to substantiate generated content."""

    code = "ai_grounding_failed"
    http_status = 422


class UnknownCitationError(GroundingError):
    """The model cited an identifier that was not in the assembled context."""

    code = "ai_unknown_citation"


class UnsupportedQuotationError(GroundingError):
    """A verbatim quotation does not occur in the source it was attributed to."""

    code = "ai_unsupported_quotation"


class UncitedFactError(GroundingError):
    """A finding asserted as FACT carries no citation."""

    code = "ai_uncited_fact"


# --------------------------------------------------------------------------
# Background processing
# --------------------------------------------------------------------------


class ProcessingError(ClaimIQError):
    code = "processing_error"


class JobCancelledError(ProcessingError):
    code = "job_cancelled"
    http_status = 409


# --------------------------------------------------------------------------
# Import / migration
# --------------------------------------------------------------------------


class ImportError_(ClaimIQError):
    """Legacy-data import failure. Trailing underscore avoids shadowing the builtin."""

    code = "import_error"
    http_status = 422


class ImportValidationError(ImportError_):
    code = "import_validation_failed"
