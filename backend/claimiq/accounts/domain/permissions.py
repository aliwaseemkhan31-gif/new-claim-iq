"""Permission catalogue and role definitions.

Pure Python: the authorisation *model* is defined and tested independently of
Django, so "can a Reviewer delete a document?" is answerable by a unit test
rather than by standing up a database and a request.

Two scopes exist and must not be confused:

- **Organization permissions** — administering the tenant: users, roles,
  knowledge bases, model configuration.
- **Project permissions** — working inside one project: documents, claims,
  evidence, analysis.

A user's effective permissions are the union of their organization role and
their role on the project in question. Organization membership alone grants no
access to a project's contents; a System Administrator is the single exception,
and that exception is explicit rather than implied.

Runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    description: str
    scope: str  # "organization" | "project"


def _org(code: str, label: str, description: str) -> Permission:
    return Permission(code=code, label=label, description=description, scope="organization")


def _proj(code: str, label: str, description: str) -> Permission:
    return Permission(code=code, label=label, description=description, scope="project")


# ---------------------------------------------------------------------------
# Organization-scoped permissions
# ---------------------------------------------------------------------------

ORG_MANAGE = _org("org.manage", "Manage organization", "Edit organization settings.")
ORG_MANAGE_USERS = _org("org.users.manage", "Manage users", "Invite, disable and edit users.")
ORG_MANAGE_ROLES = _org("org.roles.manage", "Manage roles", "Assign roles and permissions.")
ORG_VIEW_AUDIT = _org("org.audit.view", "View audit log", "Read the organization audit trail.")
ORG_MANAGE_PROJECTS = _org("org.projects.manage", "Manage projects", "Create and archive projects.")
ORG_MANAGE_KB = _org("org.kb.manage", "Manage knowledge bases", "Ingest, validate and publish knowledge bases.")
ORG_MANAGE_AI = _org("org.ai.manage", "Manage AI configuration", "Configure models, prompts and retrieval parameters.")
ORG_VIEW_AI_OBSERVABILITY = _org("org.ai.observe", "View AI observability", "Inspect retrieval traces and model calls.")
ORG_MANAGE_SYSTEM = _org("org.system.manage", "Manage system", "Storage, workers and system-wide settings.")
ORG_IMPORT_DATA = _org("org.import", "Import data", "Run legacy and external data imports.")

# ---------------------------------------------------------------------------
# Project-scoped permissions
# ---------------------------------------------------------------------------

PROJECT_VIEW = _proj("project.view", "View project", "See the project and its contents.")
PROJECT_EDIT = _proj("project.edit", "Edit project", "Edit project metadata and parties.")
PROJECT_MANAGE_MEMBERS = _proj("project.members.manage", "Manage members", "Add and remove project members.")

DOCUMENT_VIEW = _proj("document.view", "View documents", "Read documents and their extracted text.")
DOCUMENT_UPLOAD = _proj("document.upload", "Upload documents", "Add documents to the project.")
DOCUMENT_EDIT = _proj("document.edit", "Edit documents", "Edit document metadata and classification.")
DOCUMENT_DELETE = _proj("document.delete", "Delete documents", "Remove documents from the project.")
DOCUMENT_REPROCESS = _proj("document.reprocess", "Reprocess documents", "Re-run the ingestion pipeline.")

CLAIM_VIEW = _proj("claim.view", "View claims", "Read claims and their analysis.")
CLAIM_CREATE = _proj("claim.create", "Create claims", "Create new claims.")
CLAIM_EDIT = _proj("claim.edit", "Edit claims", "Edit claim details and positions.")
CLAIM_DELETE = _proj("claim.delete", "Delete claims", "Remove claims.")
CLAIM_ASSESS = _proj("claim.assess", "Assess claims", "Record a human assessment or determination.")

EVIDENCE_VIEW = _proj("evidence.view", "View evidence", "Read evidence records.")
EVIDENCE_MANAGE = _proj("evidence.manage", "Manage evidence", "Link, unlink and annotate evidence.")

CORRESPONDENCE_VIEW = _proj("correspondence.view", "View correspondence", "Read project correspondence.")
CORRESPONDENCE_MANAGE = _proj("correspondence.manage", "Manage correspondence", "Edit correspondence metadata and links.")

AI_QUERY = _proj("ai.query", "Ask the AI", "Run AI questions scoped to the project.")
AI_ANALYSE = _proj("ai.analyse", "Run AI analysis", "Start claim analysis jobs.")
AI_OVERRIDE = _proj("ai.override", "Override AI findings", "Accept, reject or edit AI findings.")

REPORT_VIEW = _proj("report.view", "View reports", "Read generated reports.")
REPORT_GENERATE = _proj("report.generate", "Generate reports", "Produce new report versions.")

ALL_PERMISSIONS: tuple[Permission, ...] = (
    ORG_MANAGE, ORG_MANAGE_USERS, ORG_MANAGE_ROLES, ORG_VIEW_AUDIT,
    ORG_MANAGE_PROJECTS, ORG_MANAGE_KB, ORG_MANAGE_AI,
    ORG_VIEW_AI_OBSERVABILITY, ORG_MANAGE_SYSTEM, ORG_IMPORT_DATA,
    PROJECT_VIEW, PROJECT_EDIT, PROJECT_MANAGE_MEMBERS,
    DOCUMENT_VIEW, DOCUMENT_UPLOAD, DOCUMENT_EDIT, DOCUMENT_DELETE, DOCUMENT_REPROCESS,
    CLAIM_VIEW, CLAIM_CREATE, CLAIM_EDIT, CLAIM_DELETE, CLAIM_ASSESS,
    EVIDENCE_VIEW, EVIDENCE_MANAGE,
    CORRESPONDENCE_VIEW, CORRESPONDENCE_MANAGE,
    AI_QUERY, AI_ANALYSE, AI_OVERRIDE,
    REPORT_VIEW, REPORT_GENERATE,
)

PERMISSIONS_BY_CODE: Mapping[str, Permission] = {p.code: p for p in ALL_PERMISSIONS}

ORGANIZATION_PERMISSIONS = frozenset(
    p.code for p in ALL_PERMISSIONS if p.scope == "organization"
)
PROJECT_PERMISSIONS = frozenset(p.code for p in ALL_PERMISSIONS if p.scope == "project")


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    label: str
    description: str
    scope: str
    permissions: frozenset[str]
    is_system: bool = True

    def grants(self, permission: str) -> bool:
        return permission in self.permissions


def _codes(*permissions: Permission) -> frozenset[str]:
    return frozenset(p.code for p in permissions)


READ_ONLY_PROJECT = _codes(
    PROJECT_VIEW, DOCUMENT_VIEW, CLAIM_VIEW, EVIDENCE_VIEW,
    CORRESPONDENCE_VIEW, REPORT_VIEW,
)

CONTRIBUTOR_PROJECT = READ_ONLY_PROJECT | _codes(
    DOCUMENT_UPLOAD, DOCUMENT_EDIT, EVIDENCE_MANAGE,
    CORRESPONDENCE_MANAGE, AI_QUERY,
)

# ---- Organization roles ---------------------------------------------------

ROLE_SYSTEM_ADMINISTRATOR = RoleDefinition(
    code="system_administrator",
    label="System Administrator",
    description=(
        "Full control, including system configuration and every project. The "
        "only role with implicit access to project contents."
    ),
    scope="organization",
    permissions=frozenset(p.code for p in ALL_PERMISSIONS),
)

ROLE_ORGANIZATION_ADMINISTRATOR = RoleDefinition(
    code="organization_administrator",
    label="Organization Administrator",
    description=(
        "Administers users, roles, projects and knowledge bases. Does not "
        "receive automatic access to project contents — project membership is "
        "still required, so administering the tenant is separated from reading "
        "its commercially sensitive material."
    ),
    scope="organization",
    permissions=_codes(
        ORG_MANAGE, ORG_MANAGE_USERS, ORG_MANAGE_ROLES, ORG_VIEW_AUDIT,
        ORG_MANAGE_PROJECTS, ORG_MANAGE_KB, ORG_MANAGE_AI,
        ORG_VIEW_AI_OBSERVABILITY, ORG_IMPORT_DATA,
    ),
)

ROLE_ORGANIZATION_MEMBER = RoleDefinition(
    code="organization_member",
    label="Organization Member",
    description="Belongs to the organization. All access comes from project membership.",
    scope="organization",
    permissions=frozenset(),
)

# ---- Project roles --------------------------------------------------------

ROLE_PROJECT_MANAGER = RoleDefinition(
    code="project_manager",
    label="Project Manager",
    description="Full control of one project, including membership.",
    scope="project",
    permissions=PROJECT_PERMISSIONS,
)

ROLE_CLAIMS_MANAGER = RoleDefinition(
    code="claims_manager",
    label="Claims Manager",
    description="Owns claims: creation, analysis, assessment and reporting.",
    scope="project",
    permissions=CONTRIBUTOR_PROJECT | _codes(
        CLAIM_CREATE, CLAIM_EDIT, CLAIM_DELETE, CLAIM_ASSESS,
        AI_ANALYSE, AI_OVERRIDE, REPORT_GENERATE, DOCUMENT_REPROCESS,
    ),
)

ROLE_CONTRACT_MANAGER = RoleDefinition(
    code="contract_manager",
    label="Contract Manager",
    description="Owns contract documents and obligations; contributes to claims.",
    scope="project",
    permissions=CONTRIBUTOR_PROJECT | _codes(
        DOCUMENT_DELETE, DOCUMENT_REPROCESS, CLAIM_CREATE, CLAIM_EDIT,
        AI_ANALYSE, REPORT_GENERATE,
    ),
)

ROLE_ENGINEER = RoleDefinition(
    code="engineer",
    label="Engineer / Consultant",
    description="Contributes documents, evidence and correspondence; may run analysis.",
    scope="project",
    permissions=CONTRIBUTOR_PROJECT | _codes(AI_ANALYSE, CLAIM_CREATE),
)

ROLE_LEGAL_COMMERCIAL = RoleDefinition(
    code="legal_commercial",
    label="Legal / Commercial",
    description="Assesses claims and produces reports; does not administer documents.",
    scope="project",
    permissions=READ_ONLY_PROJECT | _codes(
        AI_QUERY, AI_ANALYSE, AI_OVERRIDE, CLAIM_ASSESS, CLAIM_EDIT, REPORT_GENERATE,
    ),
)

ROLE_REVIEWER = RoleDefinition(
    code="reviewer",
    label="Reviewer",
    description="Reviews and signs off AI findings without altering source records.",
    scope="project",
    permissions=READ_ONLY_PROJECT | _codes(AI_QUERY, AI_OVERRIDE, CLAIM_ASSESS),
)

ROLE_VIEWER = RoleDefinition(
    code="viewer",
    label="Viewer",
    description="Read-only access to the project.",
    scope="project",
    permissions=READ_ONLY_PROJECT,
)

SYSTEM_ROLES: tuple[RoleDefinition, ...] = (
    ROLE_SYSTEM_ADMINISTRATOR,
    ROLE_ORGANIZATION_ADMINISTRATOR,
    ROLE_ORGANIZATION_MEMBER,
    ROLE_PROJECT_MANAGER,
    ROLE_CLAIMS_MANAGER,
    ROLE_CONTRACT_MANAGER,
    ROLE_ENGINEER,
    ROLE_LEGAL_COMMERCIAL,
    ROLE_REVIEWER,
    ROLE_VIEWER,
)

ROLES_BY_CODE: Mapping[str, RoleDefinition] = {r.code: r for r in SYSTEM_ROLES}

ORGANIZATION_ROLES = tuple(r for r in SYSTEM_ROLES if r.scope == "organization")
PROJECT_ROLES = tuple(r for r in SYSTEM_ROLES if r.scope == "project")


def resolve_permissions(
    organization_role: str | None,
    project_role: str | None = None,
    extra: Iterable[str] = (),
) -> frozenset[str]:
    """Compute the effective permission set for a principal.

    Args:
        organization_role: Role code held in the organization.
        project_role: Role code held on the project being accessed.
        extra: Additional permission codes granted directly.

    Returns:
        The union of the granted sets. Unknown role codes contribute nothing
        rather than raising, so a stale role reference degrades to *less*
        access, never more.
    """
    granted: set[str] = set(extra)

    if organization_role:
        role = ROLES_BY_CODE.get(organization_role)
        if role is not None:
            granted |= role.permissions

    if project_role:
        role = ROLES_BY_CODE.get(project_role)
        if role is not None:
            granted |= role.permissions

    return frozenset(granted)


def has_permission(
    permission: str,
    organization_role: str | None,
    project_role: str | None = None,
    extra: Iterable[str] = (),
) -> bool:
    return permission in resolve_permissions(organization_role, project_role, extra)


def validate_role_definitions() -> list[str]:
    """Check the catalogue for internal inconsistency.

    Run as a test and at startup. Catches the class of mistake where a role is
    given a permission from the wrong scope — a project role granted an
    organization permission would silently widen access for every member.
    """
    problems: list[str] = []
    for role in SYSTEM_ROLES:
        for code in role.permissions:
            permission = PERMISSIONS_BY_CODE.get(code)
            if permission is None:
                problems.append(f"Role {role.code!r} grants unknown permission {code!r}")
                continue
            if role.scope == "project" and permission.scope != "project":
                problems.append(
                    f"Project role {role.code!r} grants organization permission {code!r}"
                )
    return problems
