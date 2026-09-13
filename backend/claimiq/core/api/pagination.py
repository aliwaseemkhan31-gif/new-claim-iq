"""Pagination.

Offset pagination for ordinary lists, cursor pagination for the append-heavy
tables (audit log, notifications, timeline events) where deep offsets get slow
and where rows inserted during paging would otherwise cause the client to see
duplicates.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """Page-number pagination with a hard ceiling on ``page_size``.

    The ceiling matters: an unbounded ``page_size`` on a table of document
    chunks is an accidental denial of service.
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("pages", self.page.paginator.num_pages),
                    ("page_size", self.get_page_size(self.request)),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )


class LargePagination(StandardPagination):
    """For chunk and page listings, where clients legitimately want more rows."""

    page_size = 100
    max_page_size = 500


class TimelinePagination(CursorPagination):
    """Cursor pagination for append-only, time-ordered data."""

    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = "-created_at"
