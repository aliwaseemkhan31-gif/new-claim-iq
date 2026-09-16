"""Dashboard aggregates.

``GET /api/v1/dashboard/`` — every figure is a query over the caller's
accessible projects, filtered again by what their role on each project lets
them see. Nothing is estimated or sampled, and a figure the caller may not see
is absent rather than zero.

Notice deadlines are computed with the same deterministic notice-timing code
the claim screen uses; no model is involved.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from claimiq.accounts.domain.permissions import ALL_PERMISSIONS, resolve_permissions
from claimiq.core.api.permissions import access_for
from claimiq.core.domain.errors import PermissionDeniedError

#: Deadlines this far ahead appear in the dashboard list.
DEADLINE_HORIZON_DAYS = 30
MAX_DEADLINE_CLAIMS = 300


def project_permissions(user, context, project_ids) -> dict:
    """Effective permissions per accessible project, in one query."""
    from claimiq.projects.models import ProjectMember

    if context.is_system_admin:
        everything = frozenset(p.code for p in ALL_PERMISSIONS)
        return {pid: everything for pid in project_ids}
    roles = dict(
        ProjectMember.objects.filter(
            user=user, project_id__in=project_ids, is_active=True
        ).values_list("project_id", "role")
    )
    return {
        pid: resolve_permissions(context.organization_role, roles.get(pid)) for pid in project_ids
    }


def _choices(model, field: str) -> dict:
    return dict(model._meta.get_field(field).choices)


def _grouped(queryset, field: str, labels: dict) -> list[dict]:
    return [
        {"value": row[field], "label": labels.get(row[field], row[field]), "count": row["n"]}
        for row in queryset.values(field).annotate(n=Count("id")).order_by("-n")
    ]


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        context = access_for(request)
        if context is None or context.organization_id is None:
            raise PermissionDeniedError("You are not a member of an active organization.")

        from claimiq.projects.models import Project

        org_id = context.organization_id
        projects = Project.objects.filter(
            organization_id=org_id, pk__in=context.accessible_project_ids
        )
        project_ids = list(projects.values_list("pk", flat=True))
        permissions = project_permissions(request.user, context, project_ids)

        def with_permission(code: str) -> list:
            return [pid for pid in project_ids if code in permissions.get(pid, ())]

        doc_pids = with_permission("document.view")
        claim_pids = with_permission("claim.view")
        ai_pids = with_permission("ai.query")

        return Response(
            {
                "generated_at": timezone.now().isoformat(),
                "projects": self._projects(projects),
                "documents": self._documents(doc_pids),
                "claims": self._claims(claim_pids),
                "analysis": self._analysis(claim_pids),
                "notices": self._deadlines(claim_pids),
                "knowledge": self._knowledge(org_id, projects),
                "ai": self._ai(ai_pids),
                "recent_activity": self._recent(doc_pids, claim_pids),
                "visibility": {
                    "projects": len(project_ids),
                    "projects_with_documents_visible": len(doc_pids),
                    "projects_with_claims_visible": len(claim_pids),
                },
            }
        )

    # ------------------------------------------------------------------

    def _projects(self, projects) -> dict:
        from claimiq.projects.models import Project

        return {
            "total": projects.count(),
            "by_status": _grouped(projects, "status", _choices(Project, "status")),
            "without_governing_edition": projects.filter(contract_edition="").count(),
        }

    def _documents(self, project_ids) -> dict:
        from claimiq.documents.models import DocumentPage, DocumentVersion

        versions = DocumentVersion.objects.filter(
            document__project_id__in=project_ids,
            document__deleted_at__isnull=True,
            is_superseded=False,
        )
        pages = DocumentPage.objects.filter(
            version__document__project_id__in=project_ids,
            version__document__deleted_at__isnull=True,
            version__is_superseded=False,
        )
        return {
            "total": versions.count(),
            "by_status": _grouped(
                versions, "processing_status", _choices(DocumentVersion, "processing_status")
            ),
            "pages": pages.count(),
            "ocr_pages": pages.filter(extraction_method="ocr").count(),
            "low_quality": versions.filter(extraction_quality__lt=0.6).count(),
        }

    def _claims(self, project_ids) -> dict:
        from claimiq.claims.models import Claim

        claims = Claim.objects.filter(project_id__in=project_ids)
        amounts = (
            claims.exclude(amount_claimed=None)
            .values("currency")
            .annotate(total=Sum("amount_claimed"), n=Count("id"))
            .order_by("currency")
        )
        return {
            "total": claims.count(),
            "by_status": _grouped(claims, "status", _choices(Claim, "status")),
            "by_type": _grouped(claims, "claim_type", _choices(Claim, "claim_type")),
            "by_human_outcome": _grouped(claims, "human_outcome", _choices(Claim, "human_outcome")),
            # Per currency: summing across currencies would be a meaningless number.
            "amount_claimed": [
                {"currency": row["currency"] or None, "total": str(row["total"]), "claims": row["n"]}
                for row in amounts
            ],
            "time_claimed_days": claims.aggregate(total=Sum("time_claimed_days"))["total"],
            "without_awareness_date": claims.filter(awareness_date__isnull=True).count(),
        }

    def _analysis(self, project_ids) -> dict:
        from claimiq.analysis.models import AIFinding, ClaimAnalysis

        runs = ClaimAnalysis.objects.filter(project_id__in=project_ids)
        findings = AIFinding.objects.filter(analysis__project_id__in=project_ids)
        return {
            "runs_by_status": _grouped(runs, "status", _choices(ClaimAnalysis, "status")),
            "findings": findings.count(),
            "findings_unreviewed": findings.filter(reviews__isnull=True).count(),
        }

    def _deadlines(self, project_ids) -> dict:
        from claimiq.analysis.services.claim_analysis import compute_notice_timing, notice_events_for
        from claimiq.claims.models import AssessmentOutcome, Claim

        today = timezone.localdate()
        horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
        open_claims = Claim.objects.select_related("project").filter(
            project_id__in=project_ids,
            awareness_date__isnull=False,
            human_outcome=AssessmentOutcome.NOT_ASSESSED,
        )
        # Counted, not silently dropped: a claim whose project has not declared
        # its conditions of contract has no notice periods to compute, and a
        # bare "computed for 0 claims" would read as "nothing is due".
        skipped_no_edition = open_claims.filter(project__contract_edition="").count()
        candidates = open_claims.exclude(project__contract_edition="").order_by(
            "awareness_date"
        )[:MAX_DEADLINE_CLAIMS]
        items: list[dict[str, Any]] = []
        computed = 0
        for claim in candidates:
            computation = compute_notice_timing(
                claim.project.contract_edition, claim.awareness_date, notice_events_for(claim)
            )
            computed += 1
            for finding in computation.findings:
                if finding.status.value != "not_given" or finding.deadline is None:
                    continue
                if finding.deadline > horizon:
                    continue
                items.append(
                    {
                        "claim_id": str(claim.pk),
                        "claim_reference": claim.reference,
                        "claim_title": claim.title,
                        "project_id": str(claim.project_id),
                        "project_name": claim.project.name,
                        "clause_number": finding.requirement.clause_number,
                        "description": finding.requirement.description,
                        "deadline": finding.deadline.isoformat(),
                        "days_remaining": (finding.deadline - today).days,
                        "is_condition_precedent": finding.requirement.is_condition_precedent,
                    }
                )
        items.sort(key=lambda item: item["deadline"])
        return {
            "claims_computed": computed,
            "claims_without_edition": skipped_no_edition,
            "overdue": sum(1 for i in items if i["days_remaining"] < 0),
            "due_within_horizon": sum(1 for i in items if i["days_remaining"] >= 0),
            "horizon_days": DEADLINE_HORIZON_DAYS,
            "items": items[:12],
            "basis": (
                "Computed from recorded awareness dates and notices. Only requirements with no "
                "recorded notice are listed; absence from the record is not proof that none was given."
            ),
        }

    def _knowledge(self, org_id, projects) -> dict:
        from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
        from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus

        bases = list(KnowledgeBase.objects.filter(organization_id=org_id).order_by("edition_code"))
        published = {kb.edition_code for kb in bases if kb.status == KnowledgeBaseStatus.PUBLISHED}
        editions_in_use = set(projects.exclude(contract_edition="").values_list("contract_edition", flat=True))
        return {
            "bases": [
                {
                    "id": str(kb.pk),
                    "edition_code": kb.edition_code,
                    "edition_label": (
                        DEFAULT_REGISTRY.get_edition(kb.edition_code).label
                        if DEFAULT_REGISTRY.has_edition(kb.edition_code)
                        else kb.edition_code
                    ),
                    "status": kb.status,
                    "is_retrievable": kb.status == KnowledgeBaseStatus.PUBLISHED,
                }
                for kb in bases
            ],
            "editions_in_use_without_published_base": sorted(editions_in_use - published),
            "projects_without_standard_form_text": projects.exclude(contract_edition="")
            .exclude(contract_edition__in=published)
            .count(),
        }

    def _ai(self, project_ids) -> dict:
        from claimiq.ai.models import AIQuestion

        since = timezone.now() - timedelta(days=30)
        questions = AIQuestion.objects.filter(project_id__in=project_ids, created_at__gte=since)
        return {
            "questions_30_days": questions.count(),
            "failed_30_days": questions.filter(status="failed").count(),
        }

    def _recent(self, doc_pids, claim_pids) -> list[dict]:
        from claimiq.analysis.models import ClaimAnalysis
        from claimiq.claims.models import Claim
        from claimiq.documents.models import Document

        events: list[dict[str, Any]] = []
        for doc in Document.objects.select_related("project").filter(project_id__in=doc_pids).order_by("-created_at")[:8]:
            events.append(
                {
                    "kind": "document_uploaded",
                    "title": doc.title,
                    "project_id": str(doc.project_id),
                    "project_name": doc.project.name,
                    "at": doc.created_at.isoformat(),
                    "link": {"name": "document-viewer", "params": {"documentId": str(doc.pk)}},
                }
            )
        for claim in Claim.objects.select_related("project").filter(project_id__in=claim_pids).order_by("-created_at")[:8]:
            events.append(
                {
                    "kind": "claim_created",
                    "title": f"{claim.reference} {claim.title}".strip(),
                    "project_id": str(claim.project_id),
                    "project_name": claim.project.name,
                    "at": claim.created_at.isoformat(),
                    "link": {"name": "claim-detail", "params": {"claimId": str(claim.pk)}},
                }
            )
        for run in (
            ClaimAnalysis.objects.select_related("claim", "project")
            .filter(project_id__in=claim_pids, finished_at__isnull=False)
            .order_by("-finished_at")[:8]
        ):
            events.append(
                {
                    "kind": f"analysis_{run.status}",
                    "title": f"Analysis of {run.claim.reference or run.claim.title}",
                    "project_id": str(run.project_id),
                    "project_name": run.project.name,
                    "at": run.finished_at.isoformat(),
                    "link": {
                        "name": "claim-detail",
                        "params": {"claimId": str(run.claim_id)},
                        "query": {"tab": "analysis"},
                    },
                }
            )
        try:
            from claimiq.reports.models import Report

            for report in Report.objects.select_related("project").filter(project_id__in=claim_pids).order_by("-created_at")[:8]:
                events.append(
                    {
                        "kind": "report_generated",
                        "title": report.title,
                        "project_id": str(report.project_id),
                        "project_name": report.project.name,
                        "at": report.created_at.isoformat(),
                        "link": {"name": "report-detail", "params": {"reportId": str(report.pk)}},
                    }
                )
        except ImportError:
            pass
        events.sort(key=lambda e: e["at"], reverse=True)
        return events[:15]
