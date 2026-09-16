"""Only assessing evidence counts as reviewing it.

Being reviewed is what lets supporting evidence establish a required element
rather than merely bear on it. Found by using the register: re-filing two items
under an issue marked both reviewed, and "The triggering event" became
established without anyone assessing anything.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from claimiq.claims.api.views import EvidenceViewSet


class _Serializer:
    def __init__(self, instance, validated_data):
        self.instance = instance
        self.validated_data = validated_data
        self.saved_with = None

    def save(self, **kwargs):
        self.saved_with = kwargs


def _update(validated_data):
    instance = SimpleNamespace(project_id="p1", relevance="supports", weight="moderate")
    serializer = _Serializer(instance, validated_data)
    view = EvidenceViewSet()
    view.request = SimpleNamespace(user="reviewer")
    context = SimpleNamespace(has=lambda code: True)
    with mock.patch("claimiq.claims.api.views.access_for", return_value=context):
        view.perform_update(serializer)
    return serializer.saved_with


def test_changing_relevance_is_attributed_as_a_review() -> None:
    saved = _update({"relevance": "contradicts"})
    assert saved["reviewed_by"] == "reviewer"
    assert saved["reviewed_at"] is not None


def test_changing_weight_is_attributed_as_a_review() -> None:
    saved = _update({"weight": "strong"})
    assert "reviewed_at" in saved


@pytest.mark.parametrize(
    "validated_data",
    [
        {"issue": object()},
        {"title": "Corrected title"},
        # Resubmitting the same assessment alongside a clerical change is not a new review.
        {"relevance": "supports", "issue": object()},
    ],
)
def test_clerical_edits_do_not_mark_evidence_reviewed(validated_data) -> None:
    saved = _update(validated_data)
    assert "reviewed_at" not in saved
    assert "reviewed_by" not in saved
    assert saved["updated_by"] == "reviewer"
