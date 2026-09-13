# ADR 0004 — Retrieval scope is an explicit object with no default edition

**Status:** Accepted
**Date:** 2026-09-12

## Context

This is the most important domain-safety decision in the system.

FIDIC editions differ materially. Under the 1987 Red Book, claims procedure sits
at Clause 53 and disputes at Clause 67. Under the 2017 Red Book, claims are
Clause 20 and disputes Clause 21. Notice periods, the role of the Engineer, and
the consequences of failing to notify differ between editions.

**Citing 1987 text against a 2017 contract is not a cosmetic bug. It is a wrong
answer to a contractual question, delivered with confidence.**

The prototype demonstrates exactly how this happens in practice. It went to the
trouble of tagging every KB chunk with an edition (`../backend/rag.py:137`), then:

- defaulted the parameter — `def query_with_claim(..., edition: str = "2017")`
  (`../backend/rag.py:288`), so the 1987 KB it had carefully ingested and verified
  became unreachable from the application; and
- omitted the filter entirely in `/fidic/clause/{clause_num}`
  (`../backend/main.py`), so a point lookup could return either edition.

The mechanism was correct. The **default** defeated it. A default is a silent
decision made on the user's behalf about which contract governs.

## Decision

1. **`RetrievalScope` is a required, validated value object.** Every retrieval
   entry point takes one. There is no `edition=` keyword argument with a default
   anywhere in the codebase.

2. **A scope that touches a knowledge base must name its edition.** Constructing
   a `RetrievalScope` that includes KB sources without an explicit
   `knowledge_base_edition` raises `ScopeValidationError`. It does not warn, log,
   or fall back.

3. **Edition is resolved from the project's contract, not from a constant.** A
   `Project` records which conditions of contract govern it. Scope construction
   for a project derives the edition from that record. If the project has not
   declared its governing edition, KB retrieval is refused with an actionable
   error rather than guessed.

4. **Results are verified post-retrieval.** The assembler asserts that every
   returned KB chunk's `edition` matches the scope. A mismatch raises
   `EditionMixingError` and the response is not produced. This is defence in
   depth: it catches a filter that was dropped by a future code change.

5. **Citations carry their edition.** A rendered citation is
   `[FIDIC Red Book 2017 — Clause 20.2.1 — p.127]`, never `[FIDIC — Clause 20.2.1]`.
   The edition is visible to the reader at the point of use.

## Consequences

**Positive.**
Edition mixing becomes structurally impossible rather than merely discouraged.
Both KB editions become equally reachable — fixing the prototype's D2 where
verified 1987 data was inert. The failure mode changes from "confidently wrong
answer" to "explicit, actionable error", which is the correct trade for a system
making contractual assertions.

**Negative.**
More friction. Callers cannot retrieve without deciding what governs, and
projects must declare their contract form before KB-backed questions work. This
friction is the feature — the decision is being surfaced rather than hidden.

**Extension.**
The same scope object carries project, document-type, clause and permission
filters, so this generalises beyond editions: any retrieval that must not
silently broaden gets the same treatment.
