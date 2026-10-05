# Document processing

> **Status.** The pipeline is wired end to end. Clause detection, chunking,
> upload safety, the stage machine and quality scoring are implemented and
> covered by the pure-domain suite (see `docs/TESTING.md` for how to run it and
> for the current count). Table extraction and embedding **are** implemented;
> both remain capability-conditional, so a deployment without the capability
> records them as `SKIPPED` rather than failing — see
> §Capability-conditional stages. Stage executors, providers, `ProcessingJob`
> persistence and Celery tasks need PostgreSQL and Redis to exercise, and the
> integration suite does not yet cover them end to end.

## Why this is not "extract text and split every N characters"

The prototype did exactly that: pdfplumber (or docTR for scans), then a
`RecursiveCharacterTextSplitter` at 800 characters with 150 overlap, storing
`{"page": n, "source": "contract"}` per chunk. Four consequences:

1. **Chunks straddled clause boundaries.** A window could end mid-sentence in
   Sub-Clause 20.2.1 and continue into 20.2.2, so one retrieved chunk contained
   two different obligations with nothing separating them.
2. **Citations stopped at the page.** With only a page number, a citation could
   say "page 127" but not "Clause 20.2.1", and could not highlight a passage.
3. **Contents pages were indexed as content.** A TOC line is
   line-leading-number-then-title, indistinguishable from a heading without an
   explicit check. This produced the 422 contaminated chunks later removed by
   hand.
4. **Headings and cross-references were conflated.** `20.2.1 Notice of Claim`
   and `as required by Sub-Clause 20.2.1` produced identical records, so the
   clause index frequently recorded the wrong page as a clause's location.

## Pipeline

```
UPLOAD
  → VALIDATE            file safety, before any parser opens the file
  → ANALYSE             per-page digital vs scanned; table detection
  → EXTRACT_TEXT        pdfplumber, digital pages only
  → OCR                 conditional — docTR, page-ranged and resumable
  → CLASSIFY_PAGES      content vs contents/index/front matter
  → DETECT_CLAUSES      headings, hierarchy, cross-references
  → EXTRACT_TABLES      conditional — pdfplumber, digital pages with tables
  → ASSEMBLE_SECTIONS   persists the DocumentSection tree
  → CHUNK               clause-boundary respecting
  → EMBED               conditional — needs a configured embedding provider
  → INDEX               tsvector for lexical retrieval
  → VALIDATE_QUALITY    scores the extraction, flags low-confidence documents
```

`STAGE_ORDER` in `ingestion/domain/pipeline.py` is the single source of truth,
and a test fails if a stage is added to the enum but not to the order — which
would otherwise mean a stage that silently never runs.

### Why the pipeline is data, not a function

Modelled as an ordered list of stages with recorded outcomes rather than a
straight-line function, for three properties a function cannot offer:

**Resumability.** A 500-page scanned set can fail at page 400. The OCR stage
checkpoints `last_completed_page` after every batch and commits those pages, so
a retry resumes at 401. Re-running from the start would be hours of GPU time.

**Idempotency.** Celery uses `acks_late`, so a worker crash re-queues the task
and a stage may run twice. Every executor deletes-then-writes its own output
rather than appending, so a second run converges instead of duplicating every
chunk. `complete()` on an already-completed stage is a no-op, because a
redelivered task legitimately re-reports success.

**Inspectability.** "Why has this been processing for twenty minutes" is
answered with a stage name and a percentage, not a spinner. `StageLog` keeps
every attempt with its duration, so a slow deployment can be diagnosed after
the fact rather than reproduced.

### Capability-conditional stages

Three stages are conditional. `OCR` depends on a *document* fact — are there
scanned pages. `EXTRACT_TABLES` and `EMBED` depend on a *deployment* fact —
is the capability available at all.

Both are implemented. Each is still skipped when the deployment cannot run it
— no embedding provider configured, or no digital page carrying a table — and
that is recorded as `SKIPPED` with an explicit reason rather than failing the
pipeline or quietly succeeding:

```
skip_reason: "no embedding provider is configured; the document is
              searchable lexically but not by vector similarity"
```

That distinction is deliberate. Failing would make every document unusable over
a capability most deployments do not yet have. Silently succeeding would mark a
document fully processed while it is invisible to vector search. `SKIPPED` is a
distinct status from `COMPLETED` for exactly this reason, and the state machine
exposes `is_retrievable` (lexical) separately from `is_vector_searchable`, so
the UI can state precisely which kind of search works.

The executors for both stages still exist and raise if called. If someone flips
the capability flag without implementing the provider, they get a clear error
rather than silence.

## Stage 1 — Validation and file safety

`ingestion/domain/file_safety.py`, 41 tests. Detailed in `SECURITY.md`.

The essential property: **every check runs before a parsing library opens the
file.** Parsers are large C-backed attack surfaces and the documents come from
opposing parties. Content decides the type; a client-supplied `Content-Type` is
a claim, not evidence.

## Stage 2 — Extraction, and the scanned-document decision

The prototype's heuristic: extract from the first 5 pages, and if the total is
under `page_count * 50` characters, treat the document as scanned and OCR the
whole thing.

The *decision point* is right; the implementation has two flaws worth naming,
because both appear in real contract sets:

- **Sampling only the first five pages.** Contract sets routinely open with a
  digital cover and agreement, then continue with scanned appendices. Sampling
  the front concludes "digital" and the appendices extract as blank.
- **All-or-nothing.** A document is classified as one thing, so a mixed
  document is always handled wrong somewhere.

The design here classifies **per page** and records `extraction_method` on each
`DocumentPage`, with `HYBRID` at the version level when both occur. Pages are
OCR'd individually, which is also what makes the stage resumable —
`OCRProvider.extract` takes `page_numbers` as part of the interface rather than
as an optional extra.

## Stage 3 — Page classification

`clause_detection.classify_page`. A page is excluded from clause detection and
chunking when a meaningful share of its non-empty lines are contents-style
entries, or when it is topped by a front-matter marker.

Two contents signatures are recognised:

```python
"1.1 Definitions ................ 12"    # dot leaders
"20.2.1  Notice of Claim    145"         # wide gap, trailing page number
"20.2.1 Notice of Claim"                 # body heading — not contents
```

Excluded pages are retained with `is_content_page = False`. They are still
viewable and searchable; they are simply not treated as provisions. Deleting
them would lose a real part of the document.

## Stage 4 — Clause detection

`ingestion/domain/clause_detection.py`, 31 tests.

**Headings versus citations.** A heading locates a clause; a citation points at
one. Separated by an additive confidence score over independent signals — a
keyword prefix, title length, absence of a terminal full stop, capitalisation
ratio, presence of sentence connectives, nesting depth.

Explicit and inspectable rather than learned, for three reasons: it runs
offline, it must be debuggable from a single line of text, and its failures
have to be explainable to a user who disagrees with the extracted structure.

```python
"20.2.1 Notice of Claim"                              -> heading
"20.2 The Contractor shall submit within 84 days."    -> not a heading
"...required by Sub-Clause 20.2.1, the Contractor..." -> citation
```

Detection confidence is persisted on `DocumentSection`, so the UI can show
which structure was inferred rather than certain.

**Hierarchy.** `20` / `20.2` / `20.2.1` nest. Duplicates resolve to the
highest-confidence occurrence, which stops a running header displacing the real
heading. Orphans attach to the nearest present ancestor rather than being
dropped — real extraction is imperfect, and losing the sub-clause costs more
than re-parenting it.

**Numeric sorting.** `20.10` sorts after `20.2`, which string ordering gets
wrong.

**False positives excluded.** Bare integers are not clause references, and a
dotted number followed by a unit is not either:

```python
"The delay cost is USD 1.5 million and the shaft is 2.5 m in diameter."
# no clause references
```

## Stage 5 — Chunking

`ingestion/domain/chunking.py`, 18 tests.

Text is segmented by clause first, then chunked within each segment. **Overlap
is never applied across a clause boundary** — bleeding one clause's text into
another is the specific defect this exists to prevent.

A clause that fits in one chunk is emitted whole and marked
`is_complete_clause`, which the retrieval assembler prefers: a partial
obligation is a misleading citation.

Each chunk carries its clause number, title, heading path and character spans,
which is what lets a citation resolve to a highlightable region rather than to
a page.

Chunks are embedded with their clause context prepended:

```
Clause 20.2.1 Notice of Claim

The claiming Party shall give a Notice to the Engineer...
```

A bare fragment reading "shall give a Notice within 28 days" is nearly
meaningless to a retriever. Prefixed, it is precisely locatable. A few extra
tokens for materially better clause-scoped recall.

Preamble text before the first heading is retained with `clause_number = None`.
Recitals are substantive.

Parameters (`ChunkingConfig`) are administrator-configurable, not hardcoded —
the right values depend on the embedding model and on how verbose a given
deployment's contracts are.

## Final stage — Quality validation

`ingestion/domain/quality.py`, 35 tests. Each version gets an
`extraction_quality` score from four signals: text recovery, mean OCR
confidence, clause coverage against a skeleton, and text integrity.

The purpose is not a dashboard number. It is to mark a document whose citations
should be treated with caution, so a poorly-OCR'd scan is visibly less reliable
rather than silently trusted. `needs_verification` drives that.

Signals that do not apply are excluded and the remaining weights renormalised.
A digital document has no OCR confidence, and scoring that absence as perfect
would let it inflate an otherwise mediocre extraction.

**The score is capped by its weakest critical signal.** A plain weighted
average turned out to be the wrong aggregation here, and the test suite caught
it: a scan with 300 characters per page scores full marks for text recovery
even when every character is garbled, which rated a visibly unreadable document
as `GOOD`. Since the downside of this assessment being wrong is *trusted
garbage* — a citation into unreadable text still looks like evidence — the
score is additionally bounded by `0.5 + 0.5 × min(ocr_confidence,
text_integrity)`. A document is only as reliable as its worst dimension.

Severity within the integrity signal is measured as the **proportion of
characters affected**, not the number of regex matches. The spaced-letter
pattern is greedy, so an entirely garbled page produces one enormous match;
counting matches scored that as a single minor blemish.

## Background execution

All processing runs in Celery workers on the `ingestion` queue. Never in a
request — the prototype ran OCR and inference inline, blocking the HTTP worker
for the duration.

`ProcessingJob` is an entity, not a Celery task id: status, progress, current
stage, serialised pipeline state, error code and timings. A unique constraint
permits only one queued or running job per version, so two concurrent uploads
of the same document cannot duplicate every chunk.

**One stage per task.** `process_document` advances the job by a single stage
and re-dispatches if more remain, rather than looping inside one task. A worker
yields between stages, so cancellation stays responsive and a soft time limit
kills at most one stage's work instead of a whole document's. A dispatch
counter guards against a state machine that fails to advance — better to stop
and be visibly stuck than to spin a worker indefinitely.

**Cancellation is cooperative.** A running stage checks `cancel_requested`
between units of work. There is no safe way to interrupt mid-page, and killing
the worker would leave the state machine inconsistent.

**Stalled jobs are swept.** A worker killed by the OOM killer leaves a job
`RUNNING` with no task attached. `acks_late` re-queues in most cases, but not
when the broker lost the task too. A Celery beat task re-dispatches jobs that
have stopped advancing — safe precisely because stages are idempotent and
checkpointed.

## Error handling

Stage failures are recorded on the job with a typed code and an operator-facing
message. An unexpected exception is caught at the runner boundary, logged in
full with a stack trace, and stored as a generic `internal_error` — the message
kept for the operator never contains internals. Nothing is swallowed, and no
failure is ever returned as an answer or fed to a model.
