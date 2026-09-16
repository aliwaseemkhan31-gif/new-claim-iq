"""Structured answer schema and parsing.

The model returns JSON against a declared schema; this module owns the schema
and the parse into typed objects that :mod:`claimiq.ai.domain.citations` then
validates.

Two prototype defects this addresses directly:

- The verdict — the most consequential output in the product — was recovered by
  a regex in the browser (`../frontend/src/pages/Ask.jsx`, ``extractVerdict``).
  Any change in model phrasing silently changed the verdict shown to the user.
  Here it is an enum field with a fixed set of values.
- There was no representable way to say "the sources do not answer this". The
  prompt asked for an assessment and the output format only fitted one, so the
  model was pushed toward producing one. Here ``insufficient_evidence`` is a
  first-class outcome with its own required explanation.

Parsing is strict. A response that does not conform is a
:class:`StructuredOutputError`, never a partial parse — recovering "most of" a
contractual assessment by regex is exactly the failure mode being removed.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from claimiq.ai.domain.citations import (
    Citation,
    EpistemicStatus,
    Finding,
    humanise_refs,
)
from claimiq.core.domain.errors import StructuredOutputError


class AnswerConfidence(str, Enum):
    """How much weight the reader should give the answer as a whole.

    Separate from per-finding epistemic status: an answer can be built entirely
    from facts and still be low-confidence because the sources are thin.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class StructuredAnswer:
    """A parsed, not-yet-grounding-validated model response."""

    summary: str
    findings: list[Finding] = field(default_factory=list)
    confidence: AnswerConfidence = AnswerConfidence.LOW
    insufficient_evidence: bool = False
    missing_information: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def cited_refs(self) -> set[str]:
        return {c.ref for f in self.findings for c in f.citations}

    def facts(self) -> list[Finding]:
        return [f for f in self.findings if f.status is EpistemicStatus.FACT]

    def unknowns(self) -> list[Finding]:
        return [f for f in self.findings if f.status is EpistemicStatus.UNKNOWN]


#: JSON schema handed to the provider for constrained decoding.
#:
#: `additionalProperties: false` throughout — a model inventing a field is a
#: signal it has misunderstood the contract, and silently accepting it makes
#: that invisible.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "confidence", "insufficient_evidence"],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "A short answer to the question. If the sources are "
                "insufficient, say so here rather than speculating."
            ),
        },
        "insufficient_evidence": {
            "type": "boolean",
            "description": (
                "True when the sources do not answer the question. A valid and "
                "expected outcome, not a failure."
            ),
        },
        "confidence": {"type": "string", "enum": [c.value for c in AnswerConfidence]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "status", "citations"],
                "properties": {
                    "statement": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [s.value for s in EpistemicStatus],
                        "description": (
                            "fact: directly supported by a cited source. "
                            "inference: reasoned from cited facts. "
                            "opinion: professional judgement. "
                            "unknown: the sources do not establish this."
                        ),
                    },
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["ref"],
                            "properties": {
                                "ref": {
                                    "type": "string",
                                    "description": (
                                        "A source identifier from the list "
                                        "provided, e.g. S1. Never invent one."
                                    ),
                                },
                                "quotation": {
                                    "type": "string",
                                    "description": (
                                        "Exact text from that source. Verified "
                                        "against it; must match character for "
                                        "character apart from whitespace."
                                    ),
                                },
                            },
                        },
                    },
                },
            },
        },
        "missing_information": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What would be needed to answer more completely.",
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
}


def _require(payload: Mapping[str, Any], key: str, expected: type) -> Any:
    if key not in payload:
        raise StructuredOutputError(
            f"The model response is missing the required field {key!r}.",
            details={"field": key},
        )
    value = payload[key]
    if not isinstance(value, expected):
        raise StructuredOutputError(
            f"The model response field {key!r} has the wrong type.",
            details={"field": key, "expected": expected.__name__, "got": type(value).__name__},
        )
    return value


def _parse_status(raw: Any) -> EpistemicStatus:
    try:
        return EpistemicStatus(str(raw))
    except ValueError:
        raise StructuredOutputError(
            f"Unknown epistemic status {raw!r} in the model response.",
            details={"valid": [s.value for s in EpistemicStatus]},
        ) from None


def _parse_citation(raw: Any, index: int) -> Citation:
    if not isinstance(raw, Mapping):
        raise StructuredOutputError(
            f"Citation {index} is not an object.", details={"index": index}
        )
    ref = raw.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise StructuredOutputError(
            f"Citation {index} has no source identifier.", details={"index": index}
        )
    quotation = raw.get("quotation")
    return Citation(
        ref=ref.strip(),
        quotation=quotation.strip() if isinstance(quotation, str) and quotation.strip() else None,
    )


def parse_answer(raw: str | Mapping[str, Any]) -> StructuredAnswer:
    """Parse a model response into a :class:`StructuredAnswer`.

    Args:
        raw: The response, as a JSON string or an already-decoded mapping.

    Raises:
        StructuredOutputError: on anything that does not conform. There is no
            partial parse and no regex fallback: recovering most of a
            contractual assessment from malformed output is the failure mode
            this design removes.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise StructuredOutputError("The model returned an empty response.")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(
                "The model response is not valid JSON.",
                details={"reason": exc.msg, "position": exc.pos},
            ) from None
    else:
        payload = raw

    if not isinstance(payload, Mapping):
        raise StructuredOutputError(
            "The model response is not a JSON object.",
            details={"got": type(payload).__name__},
        )

    summary = _require(payload, "summary", str).strip()
    if not summary:
        raise StructuredOutputError("The model response has an empty summary.")

    insufficient = bool(payload.get("insufficient_evidence", False))

    confidence_raw = payload.get("confidence", AnswerConfidence.LOW.value)
    try:
        confidence = AnswerConfidence(str(confidence_raw))
    except ValueError:
        raise StructuredOutputError(
            f"Unknown confidence value {confidence_raw!r}.",
            details={"valid": [c.value for c in AnswerConfidence]},
        ) from None

    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, Sequence) or isinstance(raw_findings, (str, bytes)):
        raise StructuredOutputError("The model response field 'findings' is not a list.")

    findings: list[Finding] = []
    for index, entry in enumerate(raw_findings):
        if not isinstance(entry, Mapping):
            raise StructuredOutputError(
                f"Finding {index} is not an object.", details={"index": index}
            )
        statement = entry.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise StructuredOutputError(
                f"Finding {index} has no statement.", details={"index": index}
            )
        raw_citations = entry.get("citations", [])
        if not isinstance(raw_citations, Sequence) or isinstance(raw_citations, (str, bytes)):
            raise StructuredOutputError(
                f"Finding {index} has a malformed citations list.",
                details={"index": index},
            )
        findings.append(
            Finding(
                # The prompt's own source labels are internal scaffolding, and
                # a reader cannot act on "SOURCE ID: S1 establishes...".
                statement=humanise_refs(statement.strip()),
                status=_parse_status(entry.get("status")),
                citations=tuple(
                    _parse_citation(c, i) for i, c in enumerate(raw_citations)
                ),
            )
        )

    # An answer claiming sufficiency while producing no findings is
    # self-contradictory: it asserts the sources answered the question and then
    # cites nothing from them.
    if not findings and not insufficient:
        raise StructuredOutputError(
            "The model returned no findings but did not report insufficient "
            "evidence. One or the other must be true.",
            details={"remedy": "Set insufficient_evidence when the sources do not answer."},
        )

    return StructuredAnswer(
        summary=summary,
        findings=findings,
        confidence=confidence,
        insufficient_evidence=insufficient,
        missing_information=[
            str(m) for m in payload.get("missing_information", []) if str(m).strip()
        ],
        caveats=[str(c) for c in payload.get("caveats", []) if str(c).strip()],
    )


def insufficient_evidence_answer(reason: str, missing: Sequence[str] = ()) -> StructuredAnswer:
    """Construct the 'sources do not answer this' result directly.

    Used when retrieval returns nothing, so no model call is made at all.
    Calling a model with no sources and asking it not to speculate is asking it
    to do something it is poorly suited to, and paying for the privilege.
    """
    return StructuredAnswer(
        summary=reason,
        findings=[Finding(statement=reason, status=EpistemicStatus.UNKNOWN)],
        confidence=AnswerConfidence.LOW,
        insufficient_evidence=True,
        missing_information=list(missing),
    )
