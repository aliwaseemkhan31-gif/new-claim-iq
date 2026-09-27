# Phase 7 — Screens and end-to-end integration

Status: **in progress**. This document is the audit that opened Phase 7 and the
decision record for it. It is updated as work lands; anything not marked
verified has not been run.

---

## 1. Audit: frontend against the Phase 1–6 backend

Sources reconciled: the current implementation, the reference material in
`docs/helping docs and resources`, and the legacy prototype at
`../Claim-IQ` (read only; not modified).

### 1.1 Screens

| Screen | State at audit | Backend at audit |
| --- | --- | --- |
| Dashboard | Recent projects + health only; metrics card "not available" | No aggregates endpoint |
| Projects list | Real list; **"New project" button disabled** | `POST /projects/` exists |
| Project workspace header | Reads fields the API does not return (`contract_reference`, `employer_name`, …) — always shows em dashes | Serializer returns `code`, `edition_label`, `parties` |
| Project overview | Tiles read `document_count`, `claim_count`, … which the summary endpoint does not return | Summary returns `documents`, `parties`, `members` only |
| Project documents | Real list; **Upload disabled** | Upload endpoint exists and works |
| Global documents | Placeholder | List endpoint exists |
| Claims (global + project tab) | Placeholder | Full CRUD, assess, timeline, gaps, notice compliance |
| Claim detail / analysis | **No route** | Phase 6 analysis + review API |
| Evidence | Placeholder | CRUD exists |
| Correspondence | Placeholder | **Models only — no API** |
| Timeline | Placeholder | Claim timeline exists; no project timeline route |
| Clauses | Placeholder | Sections stored per document; no API |
| AI workspace | Calls `/ai/ask/` but reads `answer.citations`, a field the API never returns — citations never rendered | Ask returns `answer.findings[].citations` and `sources` |
| Search | Calls `POST /search/` | **No search API** |
| Knowledge base | Placeholder | Models + validation rules only — **no ingestion, no API** |
| Reports | Placeholder | Empty app |
| Administration | Placeholder | No user/role API |
| Notifications (top bar) | Honest empty state | Empty app |
| Document viewer | **Does not exist** | Pages/chunks endpoints; no page image, no file access |

### 1.2 Ingestion gaps that block the contract workflow

- **OCR was not runnable.** The only OCR provider was docTR, which needs torch
  and is not installed. The real contract in the reference set (Indus Highway
  N-55, 294 pages), the Jaglot Skardu claim and the FIDIC Silver Book 2017 PDF
  have **no text layer at all** — every sampled page returned zero characters.
- **Embedding stage raised "not implemented"** and the capability check returned
  False, so no uploaded document was ever vector-searchable.
- **Table extraction raised "not implemented".**
- **Only PDFs could be analysed.** Images, DOCX and XLSX are accepted by upload
  validation and then failed at the first stage.
- **In local development ingestion ran inside the upload request** (Celery eager
  mode chains every stage inline). A 294-page OCR would hold the HTTP request for
  about an hour and be killed by the dev server's autoreload.

### 1.3 What the reference material establishes

- `Notice_Claim_Management_Hierarchy.docx` defines the claims workflow as eleven
  levels — receive, identify sender and request, withhold judgement, find the
  contractual basis (**Particular Conditions first, then General Conditions, then
  the event-specific clause**), understand the event, check notice, check
  evidence, test (entitlement, notice, cause, responsibility, causation, quantum,
  time impact), determine entitlement (accept / partial / reject / request
  information), prepare the response, record everything. It lists what a formal
  response must contain. This drives the claim detail screen and the claim
  report.
- **Particular Conditions govern over the standard form.** The retrieval and
  answer layers must therefore keep project contract text and standard-form text
  distinguishable, and say which governs when they differ.
- FIDIC PDFs present: Red Book 1987 (4th ed., digital, 58 pp.), Red Book 2017
  (digital, 236 pp.), Yellow Book 2017 (digital, 231 pp.), Silver Book 1999
  (digital, 125 pp.), Silver Book 2017 (**scanned**, 125 pp.). Only the three
  Red Book editions were registered.
- The 2017 Red and Yellow PDFs carry a distribution-restriction watermark on
  every page. Knowledge-base validation must treat it as a running header, not as
  contract text; it is also a licensing matter for the organization, not for the
  software.
- `C02 Events` is a real historical claims file: 62 delay/disruption events,
  contractor submissions and Engineer's evaluations 1999–2001, predominantly
  legacy Word 97 `.doc`, scanned `.jpg`, `.xls`, Primavera P3 schedules and CAD
  drawings.
- The pitch deck proposes AWS Textract, Google Vision and hosted LLMs. **Those
  conflict with the offline requirement and are not used.**

### 1.4 What the legacy prototype did, and what carries over

Upload contract → pdfplumber or docTR → ChromaDB collection per contract, plus
one shared `fidic_kb` collection tagged by edition → a single "ask" endpoint that
mixed contract, FIDIC and pasted claim text into one prompt → page text endpoint
for display. Carried over as intent: contract-plus-standard-form answering,
clause lookup, page display. Not carried over: the default edition (ADR 0004),
per-contract vector collections, unscoped endpoints, whole-document OCR
decisions.

---

## 2. Decisions

**D1 — Offline OCR is RapidOCR (PP-OCRv4, ONNX Runtime).** Models ship inside the
wheel, it runs on CPU with no download at runtime, and it needs no system binary.
docTR remains supported and is preferred when installed. Measured on the scanned
contract: 11–13 s per A4 page on this host's CPU, mean recognition confidence
0.95–0.97. Known weakness: it sometimes drops the space between words
("theContractor"), which lowers lexical-search recall on OCR'd pages; vector
search is less affected. Surfaced through the existing quality score, not hidden.

**D2 — Standard forms and project documents share one ingestion pipeline and
never share a retrieval corpus.** A FIDIC PDF uploaded to the knowledge base is
stored as an organization-level *reference* document (no project) and runs
through the same stages — OCR, page classification, clause detection, chunking,
embedding. When processing completes its chunks are materialised into the
edition's knowledge base, validated, and held until an authorised user publishes
them. Consequences:

- Project retrieval filters on the caller's accessible projects; a reference
  document has no project, so it can never appear as a project document.
- Knowledge-base retrieval reads only published, non-quarantined chunks for the
  project's declared edition.
- A project's own copy of its conditions of contract is a project document, and
  governs.

**D3 — Every source is labelled with its layer** ("project document" or "standard
form, edition X") in the prompt, the API response and the UI. The grounded-answer
prompt instructs that where the two differ on the same point the project document
governs and the difference must be stated.

**D4 — Legacy binary Office formats (`.doc`, `.xls`) and CAD/P3 files are accepted
for storage but not text-extracted**, with an explicit reason and remedy (convert to
PDF or DOCX/XLSX). No portable offline converter exists without a system
dependency; failing honestly beats extracting nothing silently.

**D5 — Local development runs ingestion in a background thread** when there is no
broker, the same pattern the Phase 6 analysis engine uses, so uploads return
immediately and a reload leaves the job resumable.

**D6 — Reports are immutable snapshots.** Generating a report freezes the claim,
its evidence, notice compliance, analysis findings (with their review state) and
the human assessment at that moment, and renders PDF and DOCX from the snapshot.
Unreviewed AI findings are labelled as unreviewed in every format.

**D7 — Notifications are emitted only by real state changes**: ingestion finished
or failed, knowledge base ready or failed, analysis finished, and computed notice
deadlines approaching or passed. Deduplicated per recipient and event.

---

## 3. Plan and status

| Step | Scope | Status |
| --- | --- | --- |
| A | OCR provider, image/DOCX/XLSX extraction, tables, embeddings, dev dispatch, page images, viewer endpoints | **done** |
| B | Knowledge base build → validate → publish; Yellow 2017 and Silver 1999/2017 editions | **done** |
| C | Source-layer labelling, search API, AI question history | **done** |
| D | Correspondence, parties, issues, project timeline, summary counts | **done** |
| E | Reports, notifications, dashboard, administration | **done** |
| F | All screens listed in §1.1 | **done** |
| G | Tests, and end-to-end verification with the real contract and FIDIC PDFs | in progress |

## 4. Defects found by running it

Each was found by processing the real documents, not by inspection.

**Stage output was discarded between stages.** `PipelineState.complete()` clears a
stage's checkpoint, and every stage read its *own* checkpoint rather than the
stage that recorded the data. So OCR never received the list of scanned pages
(a fully scanned file ended with "no pages were extracted") and detected clause
headings never reached section assembly or chunking. Data a later stage needs
now travels in stage metrics, which survive completion.

**Citations named the wrong clause.** In the FIDIC 1987 printing the clause
number sits inline after the marginal note — "Substantiation 53.3 Within 28
days…" — and the detector only recognised numbers at the start of a line. Its
contents pages have no dot leaders either, so contents entries were read as
headings and survived page classification. Result: 23 headings detected across
58 pages, 134 of 170 published passages labelled "Clause 2.2", and an answer
about the 28-day notice period cited "Clause 2.2, p.32" instead of Clause 53.1.
Both patterns are now recognised: 103 headings, 188 of 193 passages carrying a
clause number, and the same question cites Clause 53.1, p.31.

**Notices counted towards every requirement** regardless of the provision they
were given under, so a Clause 20.2.1 Notice of Claim satisfied the 20.2.4 fully
detailed claim that was never submitted (fixed in Phase 6's engine and in the
Phase 5 endpoint).

**OCR ran words together at 144 dpi.** Rendering at 216 dpi removed it (5
run-together words to 0 on a sampled page) for about 7% more time.

**`?format=pdf` collided with DRF content negotiation**, so report downloads
404'd. The format is now a path segment.

**Generation was unbounded.** No request set an output limit. Asking questions
over the 294-page N-55 contract, a schema-constrained generation from the 3B
model ran on after the client's 300 s timeout — Ollama does not stop on
disconnect — and a one-word probe then waited over ten minutes behind it.
Output is now capped (`LLM_MAX_OUTPUT_TOKENS`, default 1536) and the context
set explicitly (`LLM_CONTEXT_TOKENS`, default 8192): Ollama's 4096 default
silently drops the start of a longer prompt, which is where the grounding
instructions sit.

**Walking the UI on the real project** found: correspondence recorded from a
claim disappeared from that claim's list; three required elements (the
triggering event, cost incurred, the instruction) had no issue category and so
could never be established; re-filing evidence under an issue marked it
reviewed, which is what lets it establish an element; the prompt's source
labels ("SOURCE ID: S1", "S2 and S3 govern…") leaked into findings and
summaries; the dashboard said "computed for 0 claims" without saying the
project had no declared edition; OCR progress sat on one percentage for an
hour. All fixed, with tests.

### Open: answer quality on a real contract volume

The N-55 file is a whole tender volume — Instructions to Bidders, Particular
Conditions (Part II), Supplementary Conditions (Part III), specifications, BOQ.
Its General Conditions are FIDIC Red Book 1987, 4th ed., reprinted 1992
(p.14, p.70, p.109). Clause detection labels numbered items in every part as
clauses (193 distinct numbers; "1" and "2" from the bidding documents are the
most common), and nothing records which *part* a passage belongs to. With
qwen2.5:3b-instruct this produced a grounded-but-wrong finding: asked whether
the contract changes the standard form on the Engineer's authority, it cited a
bidding-document page as "Clause 36.1" and said it did not — Part II Sub-Clause
2.1 (p.72) does. Grounding verifies that citations exist and quotations match;
it cannot verify that a conclusion follows. Next step: segment contract volumes
into parts, so a clause number is qualified by its part and Particular
Conditions can be retrieved as amendments to a named General Condition.

### Measured: qwen2.5:3b-instruct against qwen2.5:7b-instruct

Same corpus, same retrieval, same prompts, temperature 0, fixed seed, on an
RTX 3050 (4 GB) with an i5-11400H. The 7B does not fit in 4 GB and runs mostly
on CPU.

| | 3B | 7B |
| --- | --- | --- |
| Question: notice for additional payment | 5 s, correct | 105 s, correct and fuller (53.1 and 53.3) |
| Question: limits on the Engineer's authority | 39 s, wrong — "does not change the standard form" | 170 s, found the Particular Conditions limit of Rs. 500,000 on variation orders and distinguished the two layers |
| Claim analysis | 84 s, 6 of 7 strands completed | 2059 s, 3 of 7 completed |

The 7B is materially better where it succeeds and roughly 10–25x slower. What
stops it is ours, not the model:

**Quotation verification punished the better model.** It matched on exact
spacing, and the scanned FIDIC reads "Notwithstandinga ny other provision". A
model quoting the provision as a person reads it was rejected as fabricating
while one parroting the OCR damage passed. Fixed: a failed match is retried
with spacing removed, so the characters must still be present.

**Remaining failures are elisions, and they are partly our fault.** The 7B
quotes "44.1 In the event of: (d) any delay… or (e) other special
circumstances…", dropping limbs (a) to (c). Eliding limbs of a contract
provision can invert its meaning, so the check rejects it — but the stored
clause text is itself interleaved with marginal-note fragments ("for",
"Completion") that section assembly spliced into the middle of the list, so a
contiguous quotation of that passage reads as nonsense. Telling the model not
to elide (GROUNDED_ANSWER 1.2.0) did not change its behaviour.

Two candidate fixes, in order: stop interleaving marginal notes into clause
bodies at assembly; and, where a quotation's fragments occur in the source in
order, display the source's own contiguous span rather than the model's
reconstruction, so what the reader sees is always literal source text.

Until then the 3B stays the default. A grounding failure now records the
quotation it objected to, which is how the above was diagnosed.

---

## Preliminary claim screening

Audited first: the five areas of a claims admissibility checklist — contractual
entitlement, notice, event occurrence and causation, contemporary records,
quantum — all existed in substance, spread across the evidence-gap engine, the
notice computation, the analysis strands and the 12-section report. What did
not exist was a *stage*. Every one of those runs late: the gap engine derives
its elements from the issue a piece of evidence is attached to, so a claim
raised this morning reports 0% established, which is also what a hopeless claim
reports. Entitlement and quantum had no deterministic component at all.

Screening fills that. 25 checks (`claims/domain/screening.py`), declared as
data and evaluated by one pure function over a snapshot of the claim as
recorded — no evidence assessment, no model, answerable the moment a claim is
entered. Computed on demand at `GET /claims/{id}/screening/`, alongside
evidence-gaps and notice-compliance; nothing is stored, because a screening
result written once goes stale as soon as the missing date is recorded.

Design constraints, all tested:

- **Only one check can be adverse to the claim.** NC4 returns BARRED for notice
  late under a condition precedent — the single conclusion the system can reach
  from recorded facts. The other 24 report on the state of the file. A test
  asserts no other check can return BARRED.
- **No score.** Four outcomes and a fixed caveat. A percentage would be read as
  a probability of success, and `GapReport.is_complete` already carries the
  warning about conflating record completeness with merit.
- **One cause, not a column of failures.** A missing awareness date makes six
  notice checks INDETERMINATE naming NC1, not six separate failures.
- **What cannot be computed says so.** Contemporaneity (RC3) and basis of
  calculation (QR5) are stated as uncomputable rather than inferred: a document
  date is the date printed on it, and an upload date is when someone got round
  to it.

Run against CL-001 on the N-55 project it returns "Assessable, with queries" and
finds five things, including two the existing screens did not surface together:
the notice went to the Employer where Clause 53.1 names the Engineer, and money
is claimed on an extension-of-time claim — usually a second claim hiding inside
the first. The live payload is captured as a fixture and asserted against the
component, so a renamed field fails a test rather than blanking the panel.
