"""Prompt templates, versioned.

Every prompt carries a version recorded alongside the response it produced. Two
reasons this is not optional here:

1. **Reproducibility.** A claim analysis may be read months later, possibly in
   a dispute about the analysis itself. "Which prompt produced this" must be
   answerable.
2. **Change safety.** Prompt edits change outputs in ways tests catch only if
   the version is pinned to the evaluation run.

The prototype's prompts were f-strings in a module (`../backend/rag.py:32`),
with no version and no record of which one produced a given answer.

Templates are deliberately plain `str.format` rather than a templating engine:
the substitutions are simple, and a template language is another thing that can
silently swallow an error.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from claimiq.core.domain.errors import ValidationError


@dataclass(frozen=True)
class PromptTemplate:
    """A versioned prompt.

    Attributes:
        key: Stable identifier, e.g. ``grounded_answer``.
        version: Bumped on every substantive edit. Recorded with each response.
        system: System prompt, if the provider supports one.
        template: The user prompt, with ``{placeholder}`` substitutions.
        required_variables: Names that must be supplied. Checked at render time
            so a missing variable fails loudly rather than leaving a literal
            ``{sources}`` in the text sent to the model.
    """

    key: str
    version: str
    system: str
    template: str
    required_variables: frozenset[str] = frozenset()

    def render(self, **variables: Any) -> str:
        missing = self.required_variables - set(variables)
        if missing:
            raise ValidationError(
                f"Prompt {self.key!r} is missing required variables: "
                f"{', '.join(sorted(missing))}",
                details={"prompt": self.key, "missing": sorted(missing)},
            )
        try:
            return self.template.format(**variables)
        except KeyError as exc:
            raise ValidationError(
                f"Prompt {self.key!r} references an unsupplied placeholder: {exc}",
                details={"prompt": self.key},
            ) from None

    @property
    def identifier(self) -> str:
        return f"{self.key}@{self.version}"


# ---------------------------------------------------------------------------
# Shared system prompt
#
# States the boundary and the rules that the validators independently enforce.
# The instructions are not the mechanism — ADR 0005 is explicit that an
# instruction without a check is a hope — but a model told the rules produces
# fewer violations for the checks to catch.
# ---------------------------------------------------------------------------

_ANALYST_SYSTEM = """You are a construction contract analyst assisting a \
commercial or claims professional. You are not a legal authority and your \
output is not a determination.

Rules you must follow:

1. Use ONLY the numbered sources provided. If the sources do not establish \
something, say so.
2. Cite every factual statement with the source identifier it came from. The \
identifier is the value after "SOURCE ID:" and nothing else — cite exactly \
"S1", not the reference line beside it. Never cite an identifier that is not \
in the source list.
3. When you quote, quote exactly: one unbroken run of text, copied from a single source, with nothing left out of the middle. Do not paraphrase inside quotation marks, and do not stitch separate parts together — omitting the limbs between "in the event of" and a later sub-paragraph changes what the provision says. Quote a shorter piece instead, or cite the source without quoting it.
4. Distinguish what the sources establish (fact) from what you reason from \
them (inference) from your professional view (opinion). Where the sources are \
insufficient, say that instead of guessing.
5. Never invent a clause number, date, amount, party, or document.
6. The sources may come from a specific edition of a standard form. Do not \
apply provisions from a different edition.

Answer concisely and in the register of a professional writing for a \
colleague, not a chatbot."""


GROUNDED_ANSWER = PromptTemplate(
    key="grounded_answer",
    version="1.2.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"question", "sources"}),
    template="""Answer the question using only the sources below.

SOURCES
{sources}

Each source states its type. A project document is part of this project's \
own record, and its contract documents — including any Particular or \
Supplementary Conditions — govern where they differ from the standard form on \
the same point. Standard-form text states the unamended general conditions. \
When sources of the two types say different things about the same point, say \
so explicitly and state which governs; never present standard-form text as \
what this project's contract says without noting that it is the standard form.

QUESTION
{question}

Respond with JSON matching the required schema. Every finding must carry the \
identifiers of the sources that support it. If the sources do not answer the \
question, return a single finding with status "unknown" explaining precisely \
what is missing.""",
)


CLAUSE_EXPLANATION = PromptTemplate(
    key="clause_explanation",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"clause_number", "edition_label", "sources"}),
    template="""Explain what {clause_number} of the {edition_label} requires.

SOURCES
{sources}

Set out, where the sources establish it: who is obliged, what they must do, \
within what period, following what trigger, and what follows from failure to \
comply. Where the sources do not establish one of these, say so rather than \
inferring it from general practice.

Respond with JSON matching the required schema.""",
)


NOTICE_COMPLIANCE_ANALYSIS = PromptTemplate(
    key="notice_compliance",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"question", "sources", "computed_finding"}),
    template="""Assess notice compliance for the matter below.

SOURCES
{sources}

COMPUTED TIMING
{computed_finding}

The computed timing above was calculated from recorded dates, not by you. Do \
not recalculate it or contradict its arithmetic. Your task is to explain what \
it means contractually, identify what the sources do and do not establish, and \
flag any assumption in it that the sources do not support.

QUESTION
{question}

Respond with JSON matching the required schema.""",
)


#: Shared instruction for every claim-analysis prompt. The CLAIM block is the
#: claimant's own account, rendered from the claim record, and it is not a
#: source: it carries no identifier and cannot be cited. A model that restates
#: "the claim is for USD 125,000" as a fact is asserting something uncited, and
#: grounding validation rejects the whole answer. Saying so up front avoids
#: burning the one corrective attempt on it.
_CLAIM_IS_NOT_EVIDENCE = """The CLAIM section is what the claimant asserts, \
taken from the claim record. It is not evidence and it has no source \
identifier. Do not restate it as a finding. Every finding must rest on the \
SOURCES, and anything the SOURCES do not establish must be reported as unknown."""


ENTITLEMENT_ANALYSIS = PromptTemplate(
    key="entitlement_analysis",
    # 1.1.0: added the claim-is-not-evidence instruction.
    version="1.1.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources"}),
    template="""Assess the contractual basis for the claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

"""
    + _CLAIM_IS_NOT_EVIDENCE
    + """

Address, separately and only so far as the sources allow:
- which provisions are capable of establishing entitlement
- what the claiming party must show under those provisions
- what the sources establish and what they do not
- what a respondent could reasonably argue against the claim

Do not state a conclusion on entitlement. Set out the position and its gaps; \
the determination is the reader's.

Respond with JSON matching the required schema.""",
)


CAUSATION_ANALYSIS = PromptTemplate(
    key="causation_analysis",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources"}),
    template="""Assess causation for the claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

"""
    + _CLAIM_IS_NOT_EVIDENCE
    + """

Address, separately and only so far as the sources allow:
- the event said to have caused the effect, and what the sources establish \
about it occurring
- what the sources establish about the link between that event and the effect \
claimed
- other causes the sources mention, including concurrent delay or the \
claimant's own acts or omissions

Showing that an event and an effect both occurred does not show that one caused \
the other. Do not state a conclusion on causation.

Respond with JSON matching the required schema.""",
)


TIME_IMPACT_ANALYSIS = PromptTemplate(
    key="time_impact_analysis",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources"}),
    template="""Assess the time impact of the claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

"""
    + _CLAIM_IS_NOT_EVIDENCE
    + """

Address, separately and only so far as the sources allow:
- the provisions governing extension of the time for completion
- what the sources establish about the activities affected and whether they \
were critical to completion
- what the sources establish about the length of the delay

Never state a number of days the sources do not contain. Do not state a \
conclusion on the extension due.

Respond with JSON matching the required schema.""",
)


QUANTUM_ANALYSIS = PromptTemplate(
    key="quantum_analysis",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources"}),
    template="""Assess the quantum of the claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

"""
    + _CLAIM_IS_NOT_EVIDENCE
    + """

Address, separately and only so far as the sources allow:
- the provisions governing valuation and payment
- which costs the sources show were actually incurred
- which parts of the amount claimed the sources do not support

Never compute, total or restate an amount the sources do not contain. Do not \
state a conclusion on the amount due.

Respond with JSON matching the required schema.""",
)


COUNTERARGUMENTS_ANALYSIS = PromptTemplate(
    key="counterarguments_analysis",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources"}),
    template="""Identify the arguments a respondent could raise against the \
claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

"""
    + _CLAIM_IS_NOT_EVIDENCE
    + """

Consider contractual arguments (a condition not met, a time bar, a risk the \
claimant bears, an exclusion) and factual ones (a source that contradicts the \
claim, or fails to support a step it depends on).

Include an argument only if a source supports it, and cite that source. An \
argument with no support in the sources is speculation and must be left out. \
Mark arguments as inference, not fact.

Respond with JSON matching the required schema.""",
)


EVIDENCE_GAP_ANALYSIS = PromptTemplate(
    key="evidence_gap",
    version="1.0.0",
    system=_ANALYST_SYSTEM,
    required_variables=frozenset({"claim_summary", "sources", "required_elements"}),
    template="""Identify what evidence is missing for the claim below.

SOURCES
{sources}

CLAIM
{claim_summary}

ELEMENTS THAT MUST BE ESTABLISHED
{required_elements}

For each element, state whether the sources establish it, partly establish it, \
or do not address it. Name the specific document that would close each gap \
where you can, and say plainly where you cannot.

Respond with JSON matching the required schema.""",
)


ALL_PROMPTS: tuple[PromptTemplate, ...] = (
    GROUNDED_ANSWER,
    CLAUSE_EXPLANATION,
    NOTICE_COMPLIANCE_ANALYSIS,
    ENTITLEMENT_ANALYSIS,
    CAUSATION_ANALYSIS,
    TIME_IMPACT_ANALYSIS,
    QUANTUM_ANALYSIS,
    COUNTERARGUMENTS_ANALYSIS,
    EVIDENCE_GAP_ANALYSIS,
)

PROMPTS_BY_KEY: Mapping[str, PromptTemplate] = {p.key: p for p in ALL_PROMPTS}


def get_prompt(key: str) -> PromptTemplate:
    try:
        return PROMPTS_BY_KEY[key]
    except KeyError:
        from claimiq.core.domain.errors import NotFoundError

        raise NotFoundError(
            f"Unknown prompt: {key!r}",
            details={"known": sorted(PROMPTS_BY_KEY)},
        ) from None


def validate_prompts() -> list[str]:
    """Check the catalogue for internal consistency.

    Run as a test. Catches a template whose placeholders and declared required
    variables have drifted apart — which would otherwise surface as a
    ``ValidationError`` in production, or worse, as a literal ``{sources}``
    reaching the model.
    """
    import re

    problems: list[str] = []
    seen_keys: set[str] = set()

    for prompt in ALL_PROMPTS:
        if prompt.key in seen_keys:
            problems.append(f"Duplicate prompt key: {prompt.key!r}")
        seen_keys.add(prompt.key)

        placeholders = set(re.findall(r"\{(\w+)\}", prompt.template))
        undeclared = placeholders - prompt.required_variables
        if undeclared:
            problems.append(
                f"{prompt.key!r} uses undeclared placeholders: {sorted(undeclared)}"
            )
        unused = prompt.required_variables - placeholders
        if unused:
            problems.append(
                f"{prompt.key!r} declares unused variables: {sorted(unused)}"
            )
        if not prompt.version:
            problems.append(f"{prompt.key!r} has no version")

    return problems
