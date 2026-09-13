"""DRF exception handling — the standard error envelope.

Every error response has the same shape:

.. code-block:: json

    {
      "error": {
        "code": "retrieval_scope_invalid",
        "message": "Knowledge-base retrieval requires an explicit edition.",
        "details": {"remedy": "Set knowledge_base_edition, e.g. 'red-book-2017'."},
        "request_id": "3f2c…"
      }
    }

``code`` is stable and machine-readable; clients branch on it, never on message
text. ``request_id`` ties the response to the server logs.

Two rules this enforces, both defects in the prototype:

- An unexpected exception returns a generic 500 with the request id. The
  internal message is logged, never sent — the prototype returned
  ``{"found": false, "text": str(e)}``, leaking internals into the UI.
- No exception is swallowed. Anything unhandled is logged at ERROR with a stack
  trace before the generic response is produced.
"""
from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from claimiq.core.domain.errors import ClaimIQError
from claimiq.core.logging import get_logger

logger = get_logger("core.api")


def _envelope(
    code: str,
    message: str,
    details: dict[str, Any] | None,
    request_id: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


def _request_id(context: dict[str, Any]) -> str:
    request = context.get("request")
    return getattr(request, "request_id", "-") if request is not None else "-"


def _drf_code(exc: drf_exceptions.APIException) -> str:
    """Map a DRF exception onto a stable snake_case code."""
    mapping = {
        drf_exceptions.NotAuthenticated: "not_authenticated",
        drf_exceptions.AuthenticationFailed: "authentication_failed",
        drf_exceptions.PermissionDenied: "permission_denied",
        drf_exceptions.NotFound: "not_found",
        drf_exceptions.MethodNotAllowed: "method_not_allowed",
        drf_exceptions.NotAcceptable: "not_acceptable",
        drf_exceptions.UnsupportedMediaType: "unsupported_media_type",
        drf_exceptions.Throttled: "rate_limited",
        drf_exceptions.ParseError: "parse_error",
        drf_exceptions.ValidationError: "validation_error",
    }
    for exc_type, code in mapping.items():
        if isinstance(exc, exc_type):
            return code
    return "api_error"


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF ``EXCEPTION_HANDLER``. Produces the envelope above for every error."""
    request_id = _request_id(context)
    view = context.get("view")
    view_name = type(view).__name__ if view is not None else "-"

    # Domain errors already carry a code, an HTTP status and safe details.
    if isinstance(exc, ClaimIQError):
        logger.warning(
            "api.domain_error",
            extra={
                "error_code": exc.code,
                "view": view_name,
                "status_code": exc.http_status,
            },
        )
        return Response(
            _envelope(exc.code, exc.message, exc.details, request_id),
            status=exc.http_status,
        )

    if isinstance(exc, Http404):
        return Response(
            _envelope("not_found", "The requested resource does not exist.", None, request_id),
            status=404,
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            _envelope("permission_denied", "You do not have access to this resource.", None, request_id),
            status=403,
        )

    if isinstance(exc, DjangoValidationError):
        return Response(
            _envelope(
                "validation_error",
                "The submitted data is invalid.",
                {"errors": getattr(exc, "message_dict", None) or list(exc.messages)},
                request_id,
            ),
            status=400,
        )

    if isinstance(exc, drf_exceptions.APIException):
        response = drf_exception_handler(exc, context)
        if response is not None:
            detail = response.data
            message = str(exc.detail) if not isinstance(exc.detail, (dict, list)) else "Request could not be processed."
            details = detail if isinstance(detail, (dict, list)) else None
            response.data = _envelope(
                _drf_code(exc),
                message,
                {"errors": details} if isinstance(details, (dict, list)) else None,
                request_id,
            )
            return response

    # Anything else is a bug. Log it fully, return nothing internal.
    logger.exception(
        "api.unhandled_exception",
        extra={"view": view_name, "exception_type": type(exc).__name__},
    )
    return Response(
        _envelope(
            "internal_error",
            "An unexpected error occurred. Quote the request id when reporting this.",
            None,
            request_id,
        ),
        status=500,
    )
