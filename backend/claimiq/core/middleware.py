"""Request-scoped context middleware."""
from __future__ import annotations

import time
import uuid
from typing import Callable

from django.http import HttpRequest, HttpResponse

from claimiq.core.logging import bind_context, clear_context, get_logger

logger = get_logger("core.request")

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"

#: Paths excluded from access logging. Health checks are polled continuously by
#: the container runtime and would otherwise dominate the log volume.
_QUIET_PATHS = ("/api/v1/health/live", "/api/v1/health/ready")


class RequestContextMiddleware:
    """Assign a request id, bind logging context, and log the access line.

    An inbound ``X-Request-ID`` is honoured so a reverse proxy or a client can
    correlate across hops; otherwise one is generated. The id is echoed on the
    response and included in error envelopes, so a user reporting a failure can
    quote something that finds the exact log lines.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]

        clear_context()
        bind_context(
            request_id=request_id,
            method=request.method,
            path=request.path,
        )

        started = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                bind_context(user_id=str(user.pk))

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response[RESPONSE_HEADER] = request_id

        if not request.path.startswith(_QUIET_PATHS):
            logger.info(
                "request.completed",
                extra={
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )

        clear_context()
        return response
