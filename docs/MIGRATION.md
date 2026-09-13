# Migration from the legacy prototype

> **Status.** The parsing, validation and dry-run planning layer is implemented
> and tested (32 tests, including tests that parse the real prototype files).
> The commit step that writes into PostgreSQL is `PLANNED` — it needs a running
> database.

## Principle

The prototype is a **reference, not a base**. It is not refactored into this
system and it is not modified. Migration is an explicit, validated, reversible
import — data moves across; architecture does not.

Every function in `claimiq/imports/domain/legacy.py` takes a path and returns
parsed data. Nothing writes to the legacy tree, and a test asserts it:

```python
def test_import_reads_do_not_modify_the_legacy_tree() -> None:
    before = {p: p.stat().st_mtime_ns for p in (...)}
    for path in before:
        parse_skeleton_file(path)
    assert before == {p: p.stat().st_mtime_ns for p in before}
```

## What is imported

### 1. Clause skeletons — high value

`fidic_1987_skeleton.py` and `fidic_2017_skeleton.py` hold clause number → title
maps transcribed by hand from each source PDF's contents pages, with annotated
source quirks. Real domain work, and **dead code in the prototype** — nothing
imported them.

They are parsed with `ast.literal_eval`, never imported or executed. Running
code from a data file is not something an import tool should do, even when we
shipped the file. A test feeds the parser a module with `os.environ` mutation at
the top and asserts nothing happens.

The parser also recovers the annotated quirks automatically:

```python
def test_real_1987_skeleton_shows_the_missing_clause_26() -> None:
    skeleton = parse_skeleton_file(SKELETON_1987)
    assert "26" in skeleton.missing_from_sequence()
```

The 1987 reprint's numbering jumps 25 → 27. Detected, reported as `info`, and
carried into the edition registry as `absent_clauses` so knowledge-base
validation does not report a genuine source gap as a missing clause.

### 2. Extracted document text

`db.json` holds page text and clause indexes for already-processed documents.
Importing the text avoids re-OCRing a corpus, which on scanned contract sets is
hours of GPU time.

### 3. Clause indexes — as a baseline, not as truth

The prototype's clause index conflated headings with cross-references and
indexed contents pages. It is imported as a **comparison baseline**: running the
new detector over the same pages and diffing shows concretely what changed.
It is not treated as correct.

## What is deliberately not imported

**Embeddings.** The prototype used `all-MiniLM-L6-v2` at 384 dimensions; the
default here is BGE-M3 at 1024. Vectors from different models are not
comparable, and importing them would silently corrupt retrieval — the failure
would surface much later as inexplicably poor results. Text is imported and
re-embedded.

The plan states this explicitly rather than leaving it silent:

```
info: Legacy embeddings are not imported. They were produced by a
      384-dimension model and are not comparable with the current
      embedding model. Imported text is re-embedded after import.
```

**Chroma collections.** Superseded by pgvector (ADR 0002).

**`db.json` structure.** Records are mapped onto the relational model, not
copied.

## Process

### 1. Dry run

```bash
docker compose run --rm backend python manage.py import_legacy \
    --source /legacy --dry-run
```

Produces an `ImportPlan`: what would be created, and every validation finding.
Nothing is written. `can_proceed` is False if any error was raised.

### 2. Validation report

| Severity | Meaning | Examples |
| --- | --- | --- |
| `error` | Blocks the import | Duplicate clause numbers; two skeletons for one edition; a document with no text |
| `warning` | Proceeds, needs attention | Untitled clauses; page-count mismatch; >30% empty pages; unrecognised edition code |
| `info` | Observation | Numbering gaps; the embedding decision above |

Two skeletons for the same edition is an **error**, not a merge: importing both
would fuse two clause sets into one edition, which is the exact class of
confusion ADR 0004 exists to prevent.

### 3. Edition mapping

Legacy skeletons use bare `"1987"` and `"2017"`. The registry uses
`red-book-1987` and `red-book-2017`, because a bare year does not say which
contract form. Mapping is explicit; an unmapped edition is a warning, not a
guess.

### 4. Commit — `PLANNED`

```bash
docker compose run --rm backend python manage.py import_legacy \
    --source /legacy --organization <uuid> --project <uuid> --commit
```

Runs in a single transaction. Any error rolls back entirely — a partial import
leaving half a knowledge base is worse than no import.

### 5. Re-embed and validate

Imported text carries no vectors. After commit:

```bash
docker compose run --rm backend python manage.py reembed --all
docker compose run --rm backend python manage.py validate_knowledge_base --all
```

Knowledge-base validation is expected to flag contamination in imported legacy
KB content. That is the intended outcome: the prototype's 2017 KB had 422
contaminated chunks removed by hand, and its 1987 KB was never subjected to the
same pass. The quarantine list is produced automatically —
`ValidationReport.contaminated_chunk_ids()` is the generated equivalent of the
prototype's hand-written `deleted_2017_guidance_ids.txt`.

## Rollback

- **Before commit:** nothing was written.
- **After commit:** every imported row carries an import-batch reference, so a
  batch can be reversed. Documents are soft-deleted rather than removed, so the
  audit trail survives.
- **Legacy system:** unaffected in all cases. It is never written to, so
  "rollback" for the prototype means doing nothing.

## Verified against the real files

`tests/domain/test_legacy_import.py` parses the actual prototype files where
present, and skips cleanly where not:

| Test | Asserts |
| --- | --- |
| `test_real_1987_skeleton_parses` | Edition `1987`, >100 clauses |
| `test_real_1987_skeleton_shows_the_missing_clause_26` | Numbering gap detected |
| `test_real_1987_skeleton_notes_are_preserved` | Hand-written source notes retained |
| `test_real_2017_skeleton_parses_and_has_claims_at_clause_20` | Clauses 20 and 21 present |
| `test_both_real_skeletons_plan_cleanly_together` | >200 clauses, no errors |
| `test_real_legacy_db_parses` | `db.json` yields well-formed records |
| `test_import_reads_do_not_modify_the_legacy_tree` | mtimes unchanged |
