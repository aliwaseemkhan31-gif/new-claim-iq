# ADR 0005 — Grounding: structured output and post-hoc citation validation

**Status:** Accepted
**Date:** 2026-09-12

## Context

The requirement is unambiguous: the AI must never fabricate clause numbers,
dates, amounts, correspondence, or quotations, and must say "insufficient
evidence" when the sources do not support a conclusion.

Prompt instructions alone do not achieve this. The prototype's prompt already
says *"Never invent clause numbers or legal interpretations"*
(`../backend/rag.py:63`) and has no mechanism to detect when that instruction is
ignored. An instruction without a check is a hope.

Two further prototype defects are instructive:

- The verdict — the single most consequential output — was recovered by a regex
  in the browser (`../frontend/src/pages/Ask.jsx`, `extractVerdict`). Any change
  in model phrasing silently changes the verdict shown.
- Retrieval failures were returned as strings and injected into the prompt as
  context (`../backend/rag.py:273`), so `"FIDIC KB unavailable: ..."` could be
  read by the model as if it were contract text.

## Decision

Grounding is enforced by **four independent mechanisms**, not by prompt wording.

### 1. Structured output with schema validation

The LLM returns JSON conforming to a declared schema (Ollama structured outputs /
constrained decoding where the model supports it). Every substantive field is
typed. `verdict` is an enum with a database constraint, not prose. Output that
fails schema validation is retried once, then fails the job — it is never
partially parsed by regex.

### 2. Closed-world citation identifiers

Retrieved chunks are presented to the model with **opaque short identifiers**
(`S1`, `S2`, …) assigned at assembly time. The model must cite by identifier.
It cannot invent `S47` when only `S1`–`S8` were supplied, and a citation is a
lookup in a dict the application controls rather than a string to be parsed.

### 3. Post-generation citation validation

Before a response is returned, every citation is checked:

- the identifier was in the assembled context (else `UnknownCitationError`);
- the cited chunk's edition matches the scope (ADR 0004);
- any **verbatim quotation** is verified to occur in the cited chunk's source
  text, after whitespace normalisation. A quotation that does not appear in its
  source is a fabricated quotation and fails the response.

### 4. Claim-level grounding requirement

Findings are typed by epistemic status — `FACT`, `INFERENCE`, `OPINION`,
`UNKNOWN`. A finding typed `FACT` **must** carry at least one citation; the
validator rejects the response otherwise. `INFERENCE` must cite the facts it
reasons from. `UNKNOWN` is a first-class, expected outcome — the schema has an
`insufficient_evidence` branch so the model has a valid way to decline, rather
than being forced into a shape that only fits an answer.

### Error handling

Retrieval failure raises a typed exception. It never becomes context, never
becomes an answer, and never reaches the model. (Fixes D4.)

## Consequences

**Positive.**
Fabricated citations and fabricated quotations are detected mechanically rather
than trusted. "Insufficient evidence" becomes a representable, first-class result
instead of an outcome the output format discourages. The verdict is a typed
field with a DB constraint — D6 cannot recur.

**Negative.**
More round-trips on validation failure, and a stricter contract with the model
(smaller local models are worse at exact JSON). Mitigated by constrained decoding
where available, one bounded retry, and a hard failure rather than a degraded
answer.

**Deliberate limit.**
This validates *grounding* — that assertions trace to retrieved sources. It does
not validate *legal correctness*. Nothing here makes the system a legal authority;
`AI_ARCHITECTURE.md` states that boundary and the UI surfaces it.
