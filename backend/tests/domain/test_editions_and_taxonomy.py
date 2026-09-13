"""Tests for the edition registry and the document taxonomy."""
from __future__ import annotations

import pytest

from claimiq.core.domain.errors import NotFoundError, ValidationError
from claimiq.documents.domain.taxonomy import (
    ALL_CATEGORIES,
    BUILTIN_TYPES,
    DEFAULT_TAXONOMY,
    DocumentCategory,
    DocumentType,
    TaxonomyRegistry,
)
from claimiq.knowledge.domain.editions import (
    DEFAULT_REGISTRY,
    EDITION_RED_BOOK_1987,
    EDITION_RED_BOOK_2017,
    FORM_FIDIC_RED_BOOK,
    TOPIC_CLAIMS_PROCEDURE,
    TOPIC_DISPUTES,
    TOPIC_EXTENSION_OF_TIME,
    ContractEdition,
    EditionRegistry,
)


# ---------------------------------------------------------------------------
# Editions
# ---------------------------------------------------------------------------


def test_editions_place_the_same_topic_at_different_clauses() -> None:
    """The reason ADR 0004 exists, asserted as data.

    Claims procedure is Clause 53 in 1987 and Clause 20 in 2017. Answering a
    2017 question from the 1987 KB is a wrong answer, not a formatting nit.
    """
    claims_1987 = EDITION_RED_BOOK_1987.clause_for_topic(TOPIC_CLAIMS_PROCEDURE)
    claims_2017 = EDITION_RED_BOOK_2017.clause_for_topic(TOPIC_CLAIMS_PROCEDURE)
    assert claims_1987 is not None and claims_2017 is not None
    assert claims_1987.clause_number == "53"
    assert claims_2017.clause_number == "20"
    assert claims_1987.clause_number != claims_2017.clause_number


def test_disputes_clause_also_differs_between_editions() -> None:
    assert EDITION_RED_BOOK_1987.clause_for_topic(TOPIC_DISPUTES).clause_number == "67"
    assert EDITION_RED_BOOK_2017.clause_for_topic(TOPIC_DISPUTES).clause_number == "21"


def test_extension_of_time_differs_between_editions() -> None:
    assert EDITION_RED_BOOK_1987.clause_for_topic(TOPIC_EXTENSION_OF_TIME).clause_number == "44"
    assert EDITION_RED_BOOK_2017.clause_for_topic(TOPIC_EXTENSION_OF_TIME).clause_number == "8.5"


def test_absent_clause_quirk_is_recorded() -> None:
    """The 1987 reprint genuinely has no Clause 26; validation must not flag it."""
    assert "26" in EDITION_RED_BOOK_1987.absent_clauses
    assert "26" not in EDITION_RED_BOOK_2017.absent_clauses


def test_citation_prefix_names_the_edition_unambiguously() -> None:
    prefix = EDITION_RED_BOOK_2017.citation_prefix()
    assert "2017" in prefix
    assert "FIDIC" in prefix


def test_unknown_edition_raises_rather_than_defaulting() -> None:
    with pytest.raises(NotFoundError) as exc:
        DEFAULT_REGISTRY.get_edition("red-book-2099")
    assert "red-book-2099" in str(exc.value)


def test_registry_lists_editions_for_a_form_in_year_order() -> None:
    editions = DEFAULT_REGISTRY.editions_for_form(FORM_FIDIC_RED_BOOK.code)
    assert [e.year for e in editions] == [1987, 1999, 2017]


def test_1999_edition_is_representable_even_without_content() -> None:
    """A project on the 1999 form must be expressible, never coerced to another."""
    assert DEFAULT_REGISTRY.has_edition("red-book-1999")


def test_edition_referencing_unknown_form_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EditionRegistry(
            forms=[FORM_FIDIC_RED_BOOK],
            editions=[
                ContractEdition(
                    code="ghost-2020", form_code="no-such-form", year=2020, label="Ghost"
                )
            ],
        )


def test_edition_year_is_sanity_checked() -> None:
    with pytest.raises(ValidationError):
        ContractEdition(code="x", form_code=FORM_FIDIC_RED_BOOK.code, year=99, label="x")


def test_edition_codes_are_unique() -> None:
    codes = [e.code for e in DEFAULT_REGISTRY.editions()]
    assert len(codes) == len(set(codes))


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_taxonomy_codes_are_unique() -> None:
    codes = [t.code for t in BUILTIN_TYPES]
    assert len(codes) == len(set(codes))


def test_every_builtin_type_has_a_valid_category() -> None:
    for document_type in BUILTIN_TYPES:
        assert document_type.category in ALL_CATEGORIES


def test_taxonomy_covers_every_category() -> None:
    covered = {t.category for t in BUILTIN_TYPES}
    assert covered == set(ALL_CATEGORIES)


def test_particular_conditions_expects_clauses() -> None:
    assert DEFAULT_TAXONOMY.get("particular_conditions").expects_clauses is True


def test_photograph_does_not_expect_clauses() -> None:
    assert DEFAULT_TAXONOMY.get("photograph").expects_clauses is False


def test_notice_is_contractual_and_correspondence() -> None:
    """Notices create and preserve entitlement; their timing is often decisive."""
    notice = DEFAULT_TAXONOMY.get("notice")
    assert notice.is_contractual is True
    assert notice.expects_correspondence_metadata is True
    assert notice.category == DocumentCategory.CORRESPONDENCE


def test_evidence_types_are_not_contractual() -> None:
    for document_type in DEFAULT_TAXONOMY.by_category(DocumentCategory.EVIDENCE):
        assert document_type.is_contractual is False, document_type.code


def test_unknown_type_raises() -> None:
    with pytest.raises(NotFoundError):
        DEFAULT_TAXONOMY.get("not_a_type")


def test_unknown_category_raises() -> None:
    with pytest.raises(NotFoundError):
        DEFAULT_TAXONOMY.by_category("nonsense")


def test_taxonomy_is_extensible() -> None:
    registry = TaxonomyRegistry(BUILTIN_TYPES)
    registry.register(
        DocumentType(
            code="dilapidation_survey",
            label="Dilapidation Survey",
            category=DocumentCategory.EVIDENCE,
            description="Pre-existing condition survey.",
        )
    )
    assert registry.has("dilapidation_survey")
    assert DEFAULT_TAXONOMY.has("dilapidation_survey") is False, "must not mutate the default"


def test_duplicate_registration_is_rejected() -> None:
    registry = TaxonomyRegistry(BUILTIN_TYPES)
    with pytest.raises(ValidationError):
        registry.register(DEFAULT_TAXONOMY.get("letter"))


def test_invalid_category_on_custom_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DocumentType(code="x", label="X", category="made_up")


def test_clause_expecting_codes_are_a_subset_of_all() -> None:
    all_codes = {t.code for t in DEFAULT_TAXONOMY.all()}
    assert DEFAULT_TAXONOMY.codes_expecting_clauses() <= all_codes
    assert DEFAULT_TAXONOMY.contractual_codes() <= all_codes
    assert DEFAULT_TAXONOMY.correspondence_codes() <= all_codes
