"""The shape ``services.screening.payload`` hands the screen.

No database: ``payload`` is pure over a :class:`ScreeningReport`. It lives here
rather than in the domain suite only because the module it comes from imports
Django.

What matters is the hierarchy. The screen is built on Event > Notice > Claim,
with the contractual basis, the records and the relief inside the Claim rather
than beside it, and the counts a stage shows must be the sum of what it holds —
a stage that reported its own, separately computed figure would drift.
"""
from __future__ import annotations

from datetime import date

import pytest

from claimiq.claims.domain.screening import (
    STAGE_AREAS,
    Area,
    Stage,
    ScreeningInput,
    screen_claim,
)
from claimiq.claims.services.screening import payload

pytestmark = pytest.mark.integration


def _report(**kwargs):
    """A claim recorded with nothing on it — plenty outstanding to count."""
    defaults = dict(
        claim_type="eot",
        today=date(2026, 9, 27),
        edition_code="",
        edition_label="",
        contractual_basis=(),
        clauses_found=(),
        knowledge_base_published=False,
        has_claimant=False,
        has_respondent=False,
        notice_requirements_registered=False,
        notice_findings=(),
        event_date=None,
        awareness_date=None,
        description="",
        instruction_linked=False,
        evidence_count=0,
        document_count=0,
        required_records=(),
        amount_claimed=None,
        currency="",
        time_claimed_days=None,
    )
    defaults.update(kwargs)
    return screen_claim(ScreeningInput(**defaults))


def test_stages_are_event_then_notice_then_claim() -> None:
    data = payload(_report())
    assert [s["code"] for s in data["stages"]] == ["event", "notice", "claim"]
    assert [s["label"] for s in data["stages"]] == ["Event", "Notice", "Claim"]


def test_the_claim_stage_nests_the_parts_of_a_detailed_claim() -> None:
    """Sub-Clause 20.2.4: the submission, then (b), (c) and (d)."""
    data = payload(_report())
    claim = next(s for s in data["stages"] if s["code"] == "claim")
    assert [a["code"] for a in claim["areas"]] == [
        "submission",
        "entitlement",
        "records",
        "relief",
    ]


def test_event_and_notice_stages_hold_their_single_area() -> None:
    data = payload(_report())
    for code in ("event", "notice"):
        stage = next(s for s in data["stages"] if s["code"] == code)
        assert [a["code"] for a in stage["areas"]] == [code]


def test_every_stage_carries_a_label_and_a_description() -> None:
    for stage in payload(_report())["stages"]:
        assert stage["label"].strip()
        assert stage["description"].strip()


def test_stage_counts_are_the_sum_of_their_areas() -> None:
    """Not a second calculation that can drift from the one below it."""
    for stage in payload(_report())["stages"]:
        for key in ("blocking_outstanding", "outstanding", "barred", "lapsed"):
            assert stage[key] == sum(a[key] for a in stage["areas"]), (
                stage["code"],
                key,
            )


def test_report_totals_are_the_sum_of_the_stages() -> None:
    data = payload(_report())
    assert data["blocking_outstanding"] == sum(
        s["blocking_outstanding"] for s in data["stages"]
    )


def test_the_flat_area_list_is_retained_for_clients_mid_deploy() -> None:
    """Dropping it would break a cached bundle served during a rollout."""
    data = payload(_report())
    assert [a["code"] for a in data["areas"]] == [a.value for a in Area]


def test_the_flat_list_and_the_nested_one_are_the_same_objects() -> None:
    """Same dicts, not copies: two renderings of one set of results."""
    data = payload(_report())
    nested = [a for s in data["stages"] for a in s["areas"]]
    assert sorted(id(a) for a in nested) == sorted(id(a) for a in data["areas"])


def test_every_check_appears_exactly_once_under_the_stages() -> None:
    data = payload(_report())
    codes = [c["code"] for s in data["stages"] for a in s["areas"] for c in a["checks"]]
    assert len(codes) == len(set(codes))
    flat = [c["code"] for a in data["areas"] for c in a["checks"]]
    assert sorted(codes) == sorted(flat)


def test_no_area_is_orphaned_from_the_stages() -> None:
    nested = {a for areas in STAGE_AREAS.values() for a in areas}
    assert nested == set(Area)


def test_areas_keep_their_check_payload_shape() -> None:
    """The check dict is what the panel renders; it has not changed."""
    data = payload(_report())
    check = data["stages"][0]["areas"][0]["checks"][0]
    assert set(check) == {
        "code",
        "question",
        "why",
        "weight",
        "status",
        "detail",
        "items",
        "remedy",
    }


def test_entitlement_reads_as_part_of_the_claim() -> None:
    data = payload(_report())
    claim = next(s for s in data["stages"] if s["code"] == "claim")
    basis = next(a for a in claim["areas"] if a["code"] == "entitlement")
    assert "Claim" in basis["label"]


def test_a_lapse_is_counted_separately_from_a_time_bar() -> None:
    """Two mechanisms, and the screen distinguishes them."""
    for stage in payload(_report())["stages"]:
        for area in stage["areas"]:
            assert "barred" in area
            assert "lapsed" in area
