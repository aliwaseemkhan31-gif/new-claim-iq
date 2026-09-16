"""Report endpoints, mounted under ``/api/v1/reports/``.

- ``GET  /``                     reports on projects the user may read
- ``POST /``                     generate {report_type, project, claim?}
- ``GET  /{id}/``                the composed document
- ``GET  /{id}/download/{pdf|docx}/``

Reports are immutable; there is no update or delete. Regenerating makes a new version.
"""
from __future__ import annotations

from uuid import UUID

from django.http import FileResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from claimiq.accounts.domain.permissions import CLAIM_VIEW, REPORT_GENERATE, REPORT_VIEW
from claimiq.core.api.pagination import StandardPagination
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from claimiq.reports.models import Report, ReportType
from claimiq.reports.services.generation import generate_report


class GenerateSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=ReportType.choices)
    project = serializers.UUIDField()
    claim = serializers.UUIDField(required=False, allow_null=True)


def _summary(report: Report) -> dict:
    return {
        "id": str(report.pk),
        "report_type": report.report_type,
        "report_type_label": report.get_report_type_display(),
        "title": report.title,
        "version_number": report.version_number,
        "project": str(report.project_id),
        "project_name": report.project.name,
        "claim": str(report.claim_id) if report.claim_id else None,
        "generated_by": (
            (report.generated_by.full_name or report.generated_by.email) if report.generated_by_id else None
        ),
        "created_at": report.created_at.isoformat(),
        "formats": [f for f, field in (("pdf", report.pdf_file), ("docx", report.docx_file)) if field],
    }


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _require(self, request: Request, project_id, permission: str):
        context = access_for(request, str(project_id))
        if context is None or not context.has(permission):
            raise PermissionDeniedError(
                "You do not have permission to perform this action on this project.",
                details={"required_permission": permission},
            )
        return context

    def _get(self, request: Request, pk) -> Report:
        context = access_for(request)
        if context is None or context.organization_id is None:
            raise NotFoundError("The requested report does not exist.")
        try:
            report_id = UUID(str(pk))
        except ValueError:
            raise NotFoundError("The requested report does not exist.") from None
        report = (
            Report.objects.select_related("project", "generated_by")
            .filter(
                pk=report_id,
                project__organization_id=context.organization_id,
                project_id__in=context.accessible_project_ids,
            )
            .first()
        )
        if report is None:
            raise NotFoundError("The requested report does not exist.")
        self._require(request, report.project_id, REPORT_VIEW.code)
        return report

    def list(self, request: Request) -> Response:
        context = access_for(request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")
        allowed = [
            pid
            for pid in context.accessible_project_ids
            if (access_for(request, str(pid)) or context).has(REPORT_VIEW.code)
        ]
        queryset = Report.objects.select_related("project", "generated_by").filter(
            project__organization_id=context.organization_id, project_id__in=allowed
        )
        for param in ("project", "claim"):
            if value := request.query_params.get(param):
                try:
                    queryset = queryset.filter(**{f"{param}_id": UUID(value)})
                except ValueError:
                    raise ValidationError(f"'{param}' must be a UUID.", details={"field": param}) from None
        if report_type := request.query_params.get("report_type"):
            queryset = queryset.filter(report_type=report_type)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response([_summary(r) for r in page])

    def create(self, request: Request) -> Response:
        serializer = GenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from claimiq.claims.models import Claim
        from claimiq.projects.models import Project

        context = self._require(request, data["project"], REPORT_GENERATE.code)
        project = Project.objects.filter(
            pk=data["project"], organization_id=context.organization_id
        ).first()
        if project is None or project.pk not in context.accessible_project_ids:
            raise NotFoundError("The requested project does not exist.")

        claim = None
        if data.get("claim"):
            claim = (
                Claim.objects.select_related("project", "claimant", "respondent", "assessed_by")
                .filter(pk=data["claim"], project=project)
                .first()
            )
            if claim is None:
                raise NotFoundError("The requested claim does not exist on this project.")
        if data["report_type"] == ReportType.CLAIM_ASSESSMENT or claim is not None:
            context.require(CLAIM_VIEW.code)

        report = generate_report(
            report_type=data["report_type"], project=project, claim=claim, user=request.user
        )
        return Response({**_summary(report), "document": report.document}, status=201)

    def retrieve(self, request: Request, pk=None) -> Response:
        report = self._get(request, pk)
        return Response({**_summary(report), "document": report.document})

    # The format is a path segment, not a query parameter: DRF reads ?format=
    # as content negotiation and answers 404 for an unknown renderer.
    @action(detail=True, methods=["get"], url_path=r"download/(?P<file_format>pdf|docx)")
    def download(self, request: Request, pk=None, file_format: str = "pdf"):
        report = self._get(request, pk)
        fmt = file_format
        field = {"pdf": report.pdf_file, "docx": report.docx_file}.get(fmt)
        if field is None:
            raise ValidationError("Unsupported format.", details={"valid": ["pdf", "docx"]})
        if not field:
            raise NotFoundError("This report has no file in that format.")
        content_type = (
            "application/pdf"
            if fmt == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        filename = f"{report.title} v{report.version_number}.{fmt}".replace("/", "-")
        return FileResponse(field.open("rb"), content_type=content_type, as_attachment=True, filename=filename)
