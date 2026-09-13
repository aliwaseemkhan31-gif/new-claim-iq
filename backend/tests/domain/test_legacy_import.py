"""Tests for legacy ClaimIQ import.

Includes tests that parse the **real** prototype files at the repository root.
Those are read-only reads: nothing here writes to, moves or modifies anything
under the legacy application. They are skipped when the legacy tree is absent,
so the suite still passes in a checkout that does not contain it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimiq.core.domain.errors import ImportValidationError, NotFoundError
from claimiq.imports.domain.legacy import (
    LegacyDocument,
    build_import_plan,
    parse_legacy_db,
    parse_legacy_db_file,
    parse_skeleton_file,
    parse_skeleton_source,
    validate_document,
    validate_skeleton,
)

# tests/domain -> tests -> backend -> claimiq-enterprise -> repository root
LEGACY_ROOT = Path(__file__).resolve().parents[4] / "backend"
SKELETON_1987 = LEGACY_ROOT / "fidic_1987_skeleton.py"
SKELETON_2017 = LEGACY_ROOT / "fidic_2017_skeleton.py"
LEGACY_DB = LEGACY_ROOT / "db.json"

requires_legacy = pytest.mark.skipif(
    not SKELETON_1987.is_file(), reason="legacy prototype tree not present"
)

SAMPLE_SKELETON = '''
"""Sample skeleton docstring."""
SKELETON = {
    "edition": "2017",
    "clauses": {
        "1": {"title": "General Provisions", "subclauses": {"1.1": "Definitions"}},
        "20": {"title": "Claims", "subclauses": {"20.1": "Claims", "20.2": "Claims For Payment"}},
    },
}
'''


# ---------------------------------------------------------------------------
# Skeleton parsing
# ---------------------------------------------------------------------------


def test_skeleton_is_parsed_into_a_clause_hierarchy() -> None:
    skeleton = parse_skeleton_source(SAMPLE_SKELETON)
    assert skeleton.edition == "2017"
    assert skeleton.numbers() == ["1", "1.1", "20", "20.1", "20.2"]
    assert skeleton.source_notes == "Sample skeleton docstring."


def test_subclause_records_its_parent() -> None:
    skeleton = parse_skeleton_source(SAMPLE_SKELETON)
    sub = next(c for c in skeleton.clauses if c.number == "20.1")
    assert sub.parent_number == "20"
    assert sub.depth == 2


def test_skeleton_is_parsed_not_executed() -> None:
    """Running code from a data file is not something an import tool should do."""
    hostile = (
        "import os\n"
        "os.environ['CLAIMIQ_IMPORT_EXECUTED'] = 'yes'\n"
        + SAMPLE_SKELETON
    )
    import os

    os.environ.pop("CLAIMIQ_IMPORT_EXECUTED", None)
    skeleton = parse_skeleton_source(hostile)
    assert skeleton.edition == "2017"
    assert "CLAIMIQ_IMPORT_EXECUTED" not in os.environ


def test_missing_edition_is_rejected() -> None:
    """Clauses cannot be filed without knowing which contract form they belong to."""
    with pytest.raises(ImportValidationError) as exc:
        parse_skeleton_source('SKELETON = {"clauses": {"1": {"title": "x"}}}')
    assert "edition" in str(exc.value).lower()


def test_missing_skeleton_variable_is_rejected() -> None:
    with pytest.raises(ImportValidationError):
        parse_skeleton_source("OTHER = {}")


def test_unparseable_source_is_rejected() -> None:
    with pytest.raises(ImportValidationError):
        parse_skeleton_source("def broken(:\n")


def test_missing_file_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        parse_skeleton_file(Path("does-not-exist.py"))


def test_clauses_sort_numerically() -> None:
    source = '''
SKELETON = {
    "edition": "2017",
    "clauses": {"20": {"title": "C", "subclauses": {"20.10": "Ten", "20.2": "Two"}}},
}
'''
    assert parse_skeleton_source(source).numbers() == ["20", "20.2", "20.10"]


# ---------------------------------------------------------------------------
# Skeleton validation
# ---------------------------------------------------------------------------


def test_duplicate_clause_numbers_are_an_error() -> None:
    skeleton = parse_skeleton_source(SAMPLE_SKELETON)
    skeleton.clauses.append(skeleton.clauses[0])
    issues = validate_skeleton(skeleton)
    assert any(i.severity == "error" for i in issues)


def test_unknown_edition_is_a_warning_not_an_error() -> None:
    skeleton = parse_skeleton_source(SAMPLE_SKELETON)
    issues = validate_skeleton(skeleton, known_editions=["red-book-2017"])
    assert any(i.severity == "warning" and "edition" in i.message for i in issues)
    assert not any(i.severity == "error" for i in issues)


def test_numbering_gap_is_reported_as_info() -> None:
    """The 1987 reprint's missing Clause 26 is a source quirk, not a parse failure."""
    source = '''
SKELETON = {
    "edition": "1987",
    "clauses": {"25": {"title": "A"}, "27": {"title": "B"}},
}
'''
    issues = validate_skeleton(parse_skeleton_source(source))
    gap_issues = [i for i in issues if i.severity == "info" and "26" in i.message]
    assert gap_issues
    assert gap_issues[0].context["gaps"] == ["26"]


def test_untitled_clauses_are_warned_about() -> None:
    source = 'SKELETON = {"edition": "1987", "clauses": {"21": {"title": ""}}}'
    issues = validate_skeleton(parse_skeleton_source(source))
    assert any("no title" in i.message for i in issues)


def test_empty_skeleton_is_an_error() -> None:
    source = 'SKELETON = {"edition": "1987", "clauses": {}}'
    issues = validate_skeleton(parse_skeleton_source(source))
    assert any(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# db.json
# ---------------------------------------------------------------------------


def test_db_json_yields_contracts_and_claims() -> None:
    payload = {
        "contracts": {
            "abc123": {
                "filename": "contract.pdf",
                "page_count": 2,
                "pages": [{"page": 1, "text": "Hello"}, {"page": 2, "text": "World"}],
                "clause_index": [{"clause": "1.1", "page": 1}],
                "ocr_method": "pdfplumber",
            }
        },
        "claims": {
            "def456": {
                "filename": "claim.pdf",
                "page_count": 1,
                "pages": [{"page": 1, "text": "Claim text"}],
            }
        },
    }
    documents = parse_legacy_db(payload)
    assert {d.kind for d in documents} == {"contract", "claim"}
    contract = next(d for d in documents if d.kind == "contract")
    assert contract.total_characters == 10
    assert contract.ocr_method == "pdfplumber"


def test_empty_db_yields_nothing() -> None:
    assert parse_legacy_db({}) == []
    assert parse_legacy_db({"contracts": {}, "claims": {}}) == []


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "db.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ImportValidationError):
        parse_legacy_db_file(path)


def test_document_without_pages_is_an_error() -> None:
    issues = validate_document(
        LegacyDocument(legacy_id="x", filename="a.pdf", kind="contract", page_count=0)
    )
    assert any(i.severity == "error" for i in issues)


def test_mostly_empty_pages_are_warned_about() -> None:
    document = LegacyDocument(
        legacy_id="x",
        filename="scan.pdf",
        kind="contract",
        page_count=4,
        pages=[
            {"page": 1, "text": "Real content here"},
            {"page": 2, "text": ""},
            {"page": 3, "text": "  "},
            {"page": 4, "text": ""},
        ],
    )
    issues = validate_document(document)
    assert any(i.severity == "warning" and "empty" in i.message for i in issues)


def test_page_count_mismatch_is_warned_about() -> None:
    document = LegacyDocument(
        legacy_id="x",
        filename="a.pdf",
        kind="contract",
        page_count=10,
        pages=[{"page": 1, "text": "content"}],
    )
    assert any("declares 10" in i.message for i in validate_document(document))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_plan_is_a_dry_run_that_writes_nothing() -> None:
    plan = build_import_plan(skeletons=[parse_skeleton_source(SAMPLE_SKELETON)])
    assert plan.can_proceed is True
    assert plan.clause_count() == 5
    assert "skeleton" in plan.summary()


def test_two_skeletons_for_one_edition_is_an_error() -> None:
    """Importing both would merge two clause sets into a single edition."""
    skeleton = parse_skeleton_source(SAMPLE_SKELETON)
    plan = build_import_plan(skeletons=[skeleton, parse_skeleton_source(SAMPLE_SKELETON)])
    assert plan.can_proceed is False
    assert any("More than one skeleton" in i.message for i in plan.errors)


def test_plan_states_that_embeddings_are_not_imported() -> None:
    """The operator should see the decision, not wonder where the vectors went."""
    document = LegacyDocument(
        legacy_id="x",
        filename="a.pdf",
        kind="contract",
        page_count=1,
        pages=[{"page": 1, "text": "some real content"}],
    )
    plan = build_import_plan(documents=[document])
    assert any("not imported" in i.message and "384" in i.message for i in plan.issues)


def test_errors_block_the_plan() -> None:
    bad = LegacyDocument(legacy_id="x", filename="a.pdf", kind="contract", page_count=0)
    assert build_import_plan(documents=[bad]).can_proceed is False


# ---------------------------------------------------------------------------
# Against the real prototype files (read-only)
# ---------------------------------------------------------------------------


@requires_legacy
def test_real_1987_skeleton_parses() -> None:
    skeleton = parse_skeleton_file(SKELETON_1987)
    assert skeleton.edition == "1987"
    assert len(skeleton.clauses) > 100
    assert "1" in skeleton.numbers()


@requires_legacy
def test_real_1987_skeleton_shows_the_missing_clause_26() -> None:
    """The hand-annotated source quirk, recovered automatically."""
    skeleton = parse_skeleton_file(SKELETON_1987)
    assert "26" in skeleton.missing_from_sequence()


@requires_legacy
def test_real_1987_skeleton_notes_are_preserved() -> None:
    skeleton = parse_skeleton_file(SKELETON_1987)
    assert "26" in skeleton.source_notes


@requires_legacy
def test_real_1987_skeleton_validates_without_errors() -> None:
    issues = validate_skeleton(parse_skeleton_file(SKELETON_1987))
    assert [i for i in issues if i.severity == "error"] == []


@pytest.mark.skipif(not SKELETON_2017.is_file(), reason="legacy tree not present")
def test_real_2017_skeleton_parses_and_has_claims_at_clause_20() -> None:
    skeleton = parse_skeleton_file(SKELETON_2017)
    assert skeleton.edition == "2017"
    numbers = skeleton.numbers()
    assert "20" in numbers
    assert "21" in numbers


@pytest.mark.skipif(not SKELETON_2017.is_file(), reason="legacy tree not present")
def test_both_real_skeletons_plan_cleanly_together() -> None:
    plan = build_import_plan(
        skeletons=[parse_skeleton_file(SKELETON_1987), parse_skeleton_file(SKELETON_2017)]
    )
    assert plan.can_proceed is True
    assert plan.clause_count() > 200


@pytest.mark.skipif(not LEGACY_DB.is_file(), reason="legacy db.json not present")
def test_real_legacy_db_parses() -> None:
    documents = parse_legacy_db_file(LEGACY_DB)
    assert isinstance(documents, list)
    for document in documents:
        assert document.legacy_id
        assert document.kind in ("contract", "claim")


@requires_legacy
def test_import_reads_do_not_modify_the_legacy_tree() -> None:
    """Explicit guard: parsing must not change the source files."""
    before = {
        path: path.stat().st_mtime_ns
        for path in (SKELETON_1987, SKELETON_2017)
        if path.is_file()
    }
    for path in before:
        parse_skeleton_file(path)
    after = {path: path.stat().st_mtime_ns for path in before}
    assert before == after
