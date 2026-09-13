"""Tests for query understanding.

The extracted structure chooses retrievers and filters. The prototype used its
clause extraction only to bias an embedding by repeating the number in the
query string; here the same signal routes to exact relational lookup.
"""
from __future__ import annotations

import pytest

from claimiq.search.domain.query import (
    QueryIntent,
    analyse,
    build_lexical_query,
    classify_intent,
    extract_amounts,
    extract_clause_references,
    extract_dates,
    extract_defined_terms,
    extract_keywords,
    merge_clause_references,
    suggested_document_types,
)


# ---------------------------------------------------------------------------
# Clause references
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What does Sub-Clause 20.2.1 require?", ["20.2.1"]),
        ("What does clause 14 say about payment?", ["14"]),
        ("Compare Clause 20.1 and Clause 20.2", ["20.1", "20.2"]),
        ("The provisions of 4.12 apply", ["4.12"]),
        ("Article 5 and Section 3.2", ["5", "3.2"]),
    ],
)
def test_clause_references_are_extracted(question: str, expected: list[str]) -> None:
    assert extract_clause_references(question) == expected


def test_measurements_are_not_clause_references() -> None:
    question = "The shaft is 2.5 m deep and the delay cost USD 1.5 million"
    assert extract_clause_references(question) == []


def test_periods_are_not_clause_references() -> None:
    assert extract_clause_references("Was notice given within 28 days?") == []


def test_duplicate_references_collapse_preserving_order() -> None:
    question = "Clause 20.2.1 says X, and Sub-Clause 20.1 says Y, but 20.2.1 governs"
    assert extract_clause_references(question) == ["20.2.1", "20.1"]


def test_keyword_reference_is_not_double_counted_as_bare() -> None:
    assert extract_clause_references("See Sub-Clause 20.2.1 for details") == ["20.2.1"]


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Was the notice served within the period?", QueryIntent.NOTICE_COMPLIANCE),
        ("Is the contractor entitled to an extension?", QueryIntent.ENTITLEMENT),
        ("Which documents support the EOT claim?", QueryIntent.EVIDENCE_SEARCH),
        ("Give me a timeline of what happened", QueryIntent.CHRONOLOGY),
        ("How much is being claimed for disruption?", QueryIntent.QUANTUM),
        ("What is the definition of Taking-Over Certificate?", QueryIntent.DEFINITION),
        ("What does Clause 20.2.1 require?", QueryIntent.CLAUSE_LOOKUP),
    ],
)
def test_intent_classification(question: str, expected: QueryIntent) -> None:
    intent, _ = classify_intent(question)
    assert intent is expected


def test_unclassifiable_question_is_general() -> None:
    intent, scores = classify_intent("Tell me about the project")
    assert intent is QueryIntent.GENERAL
    assert scores == {}


def test_scores_are_returned_so_classification_is_explainable() -> None:
    _, scores = classify_intent("Was the notice time-barred under condition precedent?")
    assert scores[QueryIntent.NOTICE_COMPLIANCE.value] > 0


def test_time_bar_outweighs_a_generic_signal() -> None:
    intent, _ = classify_intent("Is the claim time-barred because notice was late?")
    assert intent is QueryIntent.NOTICE_COMPLIANCE


def test_empty_question_does_not_raise() -> None:
    result = analyse("")
    assert result.intent is QueryIntent.GENERAL
    assert result.keywords == []


# ---------------------------------------------------------------------------
# Dates and amounts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What happened on 14/03/2026?",
        "Events after 2026-03-14",
        "The notice dated 14 March 2026",
        "Submitted Mar 14, 2026",
    ],
)
def test_dates_are_extracted(question: str) -> None:
    assert extract_dates(question)


def test_amount_with_currency_is_extracted() -> None:
    amounts = extract_amounts("The claim is for USD 1,250,000 in additional cost")
    assert amounts
    assert amounts[0].currency == "USD"


def test_bare_small_number_is_not_an_amount() -> None:
    """"28 days" is a period, not a sum."""
    assert extract_amounts("within 28 days of the event") == []


def test_thousands_separator_alone_qualifies() -> None:
    assert extract_amounts("a sum of 1,250,000") != []


def test_a_year_is_not_an_amount() -> None:
    """A bare four-digit number in year range is a date, not a sum."""
    assert extract_amounts("the notice dated 14 March 2026") == []
    assert extract_amounts("in 2026 the works were delayed") == []


def test_numbers_inside_a_date_are_skipped() -> None:
    amounts = extract_amounts("Between 2026-03-14 and 2026-04-30 the cost was USD 45,000")
    assert [a.raw for a in amounts] == ["USD 45,000"]


def test_a_large_non_year_number_is_still_an_amount() -> None:
    assert extract_amounts("a claim of 45000") != []


# ---------------------------------------------------------------------------
# Defined terms
# ---------------------------------------------------------------------------


def test_capitalised_defined_terms_are_recognised() -> None:
    terms = extract_defined_terms("Was a Notice given to the Engineer about the Works?")
    assert "Notice" in terms
    assert "Engineer" in terms
    assert "Works" in terms


def test_lowercase_prose_is_not_a_defined_term() -> None:
    """"notice" in ordinary prose is not the contractual Notice."""
    assert "Notice" not in extract_defined_terms("please take notice of the works")


def test_multiword_defined_term_is_matched() -> None:
    assert "Taking-Over Certificate" in extract_defined_terms(
        "When was the Taking-Over Certificate issued?"
    )


# ---------------------------------------------------------------------------
# Keywords and the lexical query
# ---------------------------------------------------------------------------


def test_stopwords_are_dropped() -> None:
    keywords = extract_keywords("What is the basis for the claim under the contract?")
    assert "the" not in keywords
    assert "what" not in keywords
    assert "basis" in keywords
    assert "claim" in keywords


def test_defined_terms_lead_the_lexical_query() -> None:
    """Highest-signal tokens first; ts_rank_cd rewards proximity."""
    query = build_lexical_query(["delay", "notice"], ["Notice", "Engineer"])
    assert query.startswith("notice engineer")


def test_lexical_query_has_no_duplicates() -> None:
    query = build_lexical_query(["notice", "notice", "delay"], ["Notice"])
    assert query.split().count("notice") == 1


# ---------------------------------------------------------------------------
# Routing decisions
# ---------------------------------------------------------------------------


def test_clause_reference_triggers_exact_lookup() -> None:
    result = analyse("What does Sub-Clause 20.2.1 require?")
    assert result.should_use_clause_lookup is True
    assert result.clause_references == ["20.2.1"]


def test_no_clause_reference_means_no_exact_lookup() -> None:
    assert analyse("Which documents support the claim?").should_use_clause_lookup is False


def test_evidence_questions_skip_the_knowledge_base() -> None:
    """The standard form has nothing to say about what happened on this project."""
    assert analyse("Which documents support the EOT claim?").should_search_knowledge_base is False


def test_chronology_questions_skip_the_knowledge_base() -> None:
    assert analyse("Give me a timeline of what happened").should_search_knowledge_base is False


def test_clause_questions_read_the_knowledge_base() -> None:
    assert analyse("What does Clause 20.2.1 require?").should_search_knowledge_base is True


def test_document_type_hints_match_the_intent() -> None:
    assert "notice" in suggested_document_types(QueryIntent.NOTICE_COMPLIANCE)
    assert "payment_record" in suggested_document_types(QueryIntent.QUANTUM)
    assert "conditions_of_contract" in suggested_document_types(QueryIntent.CLAUSE_LOOKUP)
    assert suggested_document_types(QueryIntent.GENERAL) == frozenset()


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------


def test_full_analysis_of_a_realistic_question() -> None:
    result = analyse(
        "Was the Notice of Claim under Sub-Clause 20.2.1 served on the Engineer "
        "within 28 days of 14 March 2026, and is the USD 1,250,000 claim time-barred?"
    )

    assert result.intent is QueryIntent.NOTICE_COMPLIANCE
    assert result.clause_references == ["20.2.1"]
    assert result.dates
    assert result.amounts and result.amounts[0].currency == "USD"
    assert "Notice" in result.defined_terms
    assert result.should_use_clause_lookup is True
    assert result.should_search_knowledge_base is True


def test_describe_is_loggable() -> None:
    described = analyse("What does Clause 20.2.1 require?").describe()
    assert "intent=" in described
    assert "20.2.1" in described


def test_merge_clause_references_preserves_first_seen_order() -> None:
    assert merge_clause_references(["20.1", "20.2"], ["20.2", "8.5"]) == ["20.1", "20.2", "8.5"]
