"""Notification endpoints.

- ``GET  /api/v1/notifications/``               the current user's notifications
- ``GET  /api/v1/notifications/unread-count/``  the bell badge
- ``POST /api/v1/notifications/{id}/read/``     mark one read
- ``POST /api/v1/notifications/read-all/``      mark all read
"""
from __future__ import annotations

from uuid import UUID

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.core.api.pagination import StandardPagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError
from claimiq.notifications.models import Notification
from claimiq.notifications.services import handlers as services


def _payload(n: Notification) -> dict:
    return {
        "id": str(n.pk),
        "category": n.category,
        "severity": n.severity,
        "title": n.title,
        "message": n.message,
        "link": n.link or None,
        "project": str(n.project_id) if n.project_id else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
    }


def _refresh_deadlines(request: Request) -> None:
    context = access_for(request)
    if context is not None and context.organization_id is not None:
        services.refresh_deadline_notifications(context.organization_id)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        _refresh_deadlines(request)
        queryset = Notification.objects.filter(recipient=request.user)
        if request.query_params.get("unread") == "true":
            queryset = queryset.filter(read_at__isnull=True)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response([_payload(n) for n in page])
        response.data["unread"] = services.unread_count(request.user)
        return response


class UnreadCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        _refresh_deadlines(request)
        return Response({"unread": services.unread_count(request.user)})


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, pk: UUID) -> Response:
        queryset = Notification.objects.filter(pk=pk, recipient=request.user)
        if not queryset.exists():
            raise NotFoundError("The requested notification does not exist.")
        services.mark_read(queryset)
        return Response({"unread": services.unread_count(request.user)})


class MarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        marked = services.mark_read(Notification.objects.filter(recipient=request.user))
        return Response({"marked": marked, "unread": 0})
