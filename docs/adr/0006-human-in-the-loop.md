# ADR 0006 — Human-in-the-loop: AI output and human assessment are separate fields

**Status:** Accepted
**Date:** 2026-09-12

## Context

This system produces analysis that people use to decide commercial positions
worth substantial sums. A claim assessment influences whether a contractor
pursues a claim, whether an employer resists it, and what each side pays their
lawyers to argue.

The obvious implementation is a single `assessment` field that the AI populates
and a human edits. It is wrong, for reasons that only become visible later:

1. **Provenance is destroyed on first edit.** Once a human corrects an AI
   finding in place, nobody can tell which parts were the model's and which
   were the reviewer's. Six months on, in a dispute about the dispute, that
   distinction matters.
2. **Nothing can be learned.** The gap between what the AI concluded and what a
   quantity surveyor concluded is the most valuable signal the system produces
   about its own accuracy. Overwriting erases it.
3. **Silent authority.** A field that reads as a considered human judgement, but
   was in fact an unreviewed model output nobody got to, is the worst outcome
   this product can produce.

## Decision

**AI output and human assessment are separate, separately attributed fields.
AI output is never overwritten and never edited in place.**

Concretely:

- Every AI-derived conclusion is stored with its model, prompt version,
  retrieved sources, confidence and epistemic status.
- Human assessment is a distinct set of columns with its own author and
  timestamp.
- A reviewer **accepts**, **rejects**, or **supersedes** an AI finding. Each is
  a recorded event with an author, a timestamp and an optional reason. None of
  them mutates the original.
- The UI renders review state explicitly. An unreviewed AI finding is visually
  distinct from an accepted one; it is never presented as settled.
- Reports show both, labelled: what the AI concluded, and what the human
  determined.

Corrections to extracted metadata — document type, party attribution, dates,
clause classification — follow the same pattern: the extracted value is
retained alongside the corrected one.

## Consequences

**Positive.**
Provenance survives indefinitely: any conclusion can be traced to whether a
model or a person reached it, and when. The AI-versus-human delta becomes
measurable, which is the only honest basis for claims about accuracy. And the
dangerous middle state — unreviewed output that looks reviewed — is not
representable, because review is a recorded event rather than the absence of an
edit.

**Negative.**
More columns, more UI, and a genuine question at every list view: which value
is *the* value? Resolved by a consistent rule — the human assessment where one
exists, the AI finding marked unreviewed otherwise — but the rule has to be
applied everywhere, and forgetting it in one view reintroduces the ambiguity.

Reviewers also have to do something explicit. Accepting a correct finding is a
click that a single editable field would not require. That friction is the
point: it is what converts "nobody objected" into "somebody agreed".

**Boundary.**
This ADR governs how conclusions are stored and displayed. It does not make the
system a legal authority, and no arrangement of fields could. The product
states that boundary in the UI and in `AI_ARCHITECTURE.md` §1.
