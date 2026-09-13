"""Tests for the authorisation model.

Answerable without a database: "can a Reviewer delete a document?" is a
property of the catalogue, not of a request.
"""
from __future__ import annotations

import pytest

from claimiq.accounts.domain.permissions import (
    ALL_PERMISSIONS,
    CLAIM_ASSESS,
    CLAIM_DELETE,
    DOCUMENT_DELETE,
    DOCUMENT_UPLOAD,
    DOCUMENT_VIEW,
    ORG_MANAGE_USERS,
    ORGANIZATION_PERMISSIONS,
    PERMISSIONS_BY_CODE,
    PROJECT_PERMISSIONS,
    PROJECT_VIEW,
    ROLE_CLAIMS_MANAGER,
    ROLE_ORGANIZATION_ADMINISTRATOR,
    ROLE_ORGANIZATION_MEMBER,
    ROLE_PROJECT_MANAGER,
    ROLE_REVIEWER,
    ROLE_SYSTEM_ADMINISTRATOR,
    ROLE_VIEWER,
    ROLES_BY_CODE,
    SYSTEM_ROLES,
    has_permission,
    resolve_permissions,
    validate_role_definitions,
)


def test_catalogue_is_internally_consistent() -> None:
    """No role may grant a permission from the wrong scope or a nonexistent one."""
    assert validate_role_definitions() == []


def test_permission_codes_are_unique() -> None:
    codes = [p.code for p in ALL_PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_every_permission_has_exactly_one_scope() -> None:
    assert ORGANIZATION_PERMISSIONS.isdisjoint(PROJECT_PERMISSIONS)
    assert len(ORGANIZATION_PERMISSIONS) + len(PROJECT_PERMISSIONS) == len(ALL_PERMISSIONS)


def test_role_codes_are_unique() -> None:
    assert len(ROLES_BY_CODE) == len(SYSTEM_ROLES)


# ---------------------------------------------------------------------------
# Separation of administration from project content
# ---------------------------------------------------------------------------


def test_organization_admin_cannot_read_project_content_without_membership() -> None:
    """Administering the tenant is separate from reading its commercial material."""
    effective = resolve_permissions(ROLE_ORGANIZATION_ADMINISTRATOR.code)
    assert ORG_MANAGE_USERS.code in effective
    assert DOCUMENT_VIEW.code not in effective
    assert PROJECT_VIEW.code not in effective


def test_organization_admin_gains_access_through_project_membership() -> None:
    effective = resolve_permissions(
        ROLE_ORGANIZATION_ADMINISTRATOR.code, ROLE_VIEWER.code
    )
    assert DOCUMENT_VIEW.code in effective
    assert DOCUMENT_DELETE.code not in effective


def test_system_administrator_holds_everything() -> None:
    """The single explicit exception, stated rather than implied."""
    effective = resolve_permissions(ROLE_SYSTEM_ADMINISTRATOR.code)
    assert effective == frozenset(p.code for p in ALL_PERMISSIONS)


def test_plain_member_has_nothing_without_a_project_role() -> None:
    assert resolve_permissions(ROLE_ORGANIZATION_MEMBER.code) == frozenset()


def test_no_organization_role_alone_grants_project_view_except_sysadmin() -> None:
    for role in SYSTEM_ROLES:
        if role.scope != "organization" or role is ROLE_SYSTEM_ADMINISTRATOR:
            continue
        assert PROJECT_VIEW.code not in resolve_permissions(role.code), role.code


# ---------------------------------------------------------------------------
# Project roles
# ---------------------------------------------------------------------------


def test_viewer_is_read_only() -> None:
    effective = resolve_permissions(None, ROLE_VIEWER.code)
    assert DOCUMENT_VIEW.code in effective
    for forbidden in (DOCUMENT_UPLOAD, DOCUMENT_DELETE, CLAIM_DELETE):
        assert forbidden.code not in effective


def test_reviewer_can_assess_but_not_alter_source_records() -> None:
    effective = resolve_permissions(None, ROLE_REVIEWER.code)
    assert CLAIM_ASSESS.code in effective
    assert DOCUMENT_DELETE.code not in effective
    assert DOCUMENT_UPLOAD.code not in effective


def test_claims_manager_can_run_and_override_analysis() -> None:
    assert has_permission("ai.analyse", None, ROLE_CLAIMS_MANAGER.code)
    assert has_permission("ai.override", None, ROLE_CLAIMS_MANAGER.code)
    assert has_permission("claim.assess", None, ROLE_CLAIMS_MANAGER.code)


def test_project_manager_holds_every_project_permission() -> None:
    assert resolve_permissions(None, ROLE_PROJECT_MANAGER.code) == PROJECT_PERMISSIONS


def test_no_project_role_grants_organization_administration() -> None:
    for role in SYSTEM_ROLES:
        if role.scope != "project":
            continue
        effective = resolve_permissions(None, role.code)
        assert effective.isdisjoint(ORGANIZATION_PERMISSIONS), role.code


# ---------------------------------------------------------------------------
# Resolution semantics
# ---------------------------------------------------------------------------


def test_roles_union_rather_than_override() -> None:
    effective = resolve_permissions(
        ROLE_ORGANIZATION_ADMINISTRATOR.code, ROLE_VIEWER.code
    )
    assert ORG_MANAGE_USERS.code in effective
    assert DOCUMENT_VIEW.code in effective


def test_unknown_role_grants_nothing_rather_than_raising() -> None:
    """A stale role reference must degrade to less access, never more."""
    assert resolve_permissions("no_such_role", "also_missing") == frozenset()


def test_none_roles_resolve_empty() -> None:
    assert resolve_permissions(None, None) == frozenset()


def test_extra_permissions_are_included() -> None:
    effective = resolve_permissions(None, ROLE_VIEWER.code, extra=[DOCUMENT_UPLOAD.code])
    assert DOCUMENT_UPLOAD.code in effective


@pytest.mark.parametrize("permission", [p.code for p in ALL_PERMISSIONS])
def test_every_permission_is_reachable_by_some_role(permission: str) -> None:
    """A permission no role can hold is dead configuration."""
    assert any(permission in role.permissions for role in SYSTEM_ROLES), permission


@pytest.mark.parametrize("role", SYSTEM_ROLES, ids=lambda r: r.code)
def test_role_permissions_all_exist(role) -> None:
    for code in role.permissions:
        assert code in PERMISSIONS_BY_CODE
