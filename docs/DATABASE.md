# Database

> **Status.** `accounts`, `projects` and `documents` models are written.
> **No migrations have been generated** — that requires a live database, which
> the build host does not have. Models for clauses, claims, correspondence,
> evidence, AI observability, audit and notifications are `PLANNED`.

PostgreSQL 16 with pgvector is the single system of record, including for
embeddings (ADR 0002).

## Conventions

| Convention | Applied to | Reason |
| --- | --- | --- |
| UUIDv4 primary keys | every domain entity | Sequential ids leak record counts and invite enumeration. The prototype exposed 8-character id prefixes on unauthenticated endpoints, making every contract reachable by guessing. UUIDs also make cross-deployment import safe. |
| `created_at` / `updated_at` | every entity | `TimestampedModel` |
| `created_by` / `updated_by` | every entity | `AuthoredModel` — who owns a record, without a join |
| `deleted_at` / `deleted_by` | documents, claims, evidence, correspondence, projects, organizations | Contract records carry evidential weight, and an audit trail referencing a vanished row is not an audit trail |
| JSONB | only where keys are open-ended and never queried relationally | `Project.metadata`, `Party.aliases`, `User.preferences` |

Soft-deleted rows are hidden by the default manager. `Model.objects` returns
live rows; `Model.all_objects` returns everything. Making the safe query the
default means a view that forgets to filter still shows the right thing.

## Entity map

```
Organization
├── OrganizationMembership ── User
├── ApiToken
└── Project
    ├── ProjectMember ── User
    ├── Party                       employer / contractor / engineer / …
    ├── DocumentCollection
    └── Document
        └── DocumentVersion         one per upload; supersession tracked
            ├── DocumentPage        text, extraction method, is_content_page
            ├── DocumentSection     self-referential clause hierarchy
            └── DocumentChunk       text + tsvector + vector(1024)

KnowledgeBase ── edition ── KnowledgeBaseDocument ── KnowledgeBaseChunk   [PLANNED]
Claim ── ClaimEvent / ClaimIssue / ClaimPosition / Evidence               [PLANNED]
Correspondence ── Notice ── thread reconstruction                         [PLANNED]
Clause ── ContractualObligation ── Deadline                               [PLANNED]
AIAnalysis / AIConversation / AIMessage / ModelConfiguration              [PLANNED]
ProcessingJob / AuditLog / Notification                                   [PLANNED]
```

## The citation chain

```
Document → DocumentVersion → DocumentPage → DocumentSection → DocumentChunk
```

This is the structural reason a citation can resolve. Every chunk knows its
page, its clause, and its character offsets within that page, so
`[Contract.pdf — Clause 20.2.1 — p.127]` navigates a viewer to the exact
passage and highlights it. The prototype stored `{page, source}` per chunk and
could go no further than "page 127".

## Notable columns

### `Project.contract_edition`

```python
contract_edition = models.CharField(max_length=64, blank=True, db_index=True)
```

The field that makes ADR 0004 workable. Retrieval scope derives the governing
edition from the project rather than from a constant. Nullable, because a
project may exist before the contract is known — but knowledge-base retrieval
is **refused with an actionable error** until it is set, never defaulted.

### `DocumentPage.is_content_page`

Marks contents pages, indexes and front matter. Excluded from chunking. This
one boolean is what prevents the contamination that required a manual purge of
422 chunks from the legacy knowledge base. Such pages are retained and remain
viewable — they are simply not treated as provisions.

### `DocumentChunk.embedding` and `embedding_model`

```python
embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
embedding_model = models.CharField(max_length=128, blank=True)
```

Recording the producing model is not bookkeeping. Vectors from different models
are not comparable, so without this a model change silently degrades retrieval
in a way that surfaces much later as inexplicably poor results. With it, a
change is detectable and the affected chunks can be re-embedded.

`EMBEDDING_DIMENSIONS` is configuration. Changing it after documents are
embedded requires a re-embed, and the migration should refuse rather than
corrupt the index.

### `DocumentVersion.extraction_quality`

A 0–1 score. Its purpose is to mark a document whose citations deserve caution,
so a poorly-OCR'd scan is visibly less reliable rather than silently trusted.

### `DocumentSection.detection_confidence`

Clause structure is *inferred*. Persisting the confidence lets the UI show which
parts of the hierarchy are certain and which were a judgement call.

## Indexes

| Index | Table | Purpose |
| --- | --- | --- |
| `GinIndex(search_vector)` | `document_chunk`, `document_page` | Lexical retrieval |
| `HnswIndex(embedding, vector_cosine_ops, m=16, ef_construction=64)` | `document_chunk` | ANN vector search |
| `(version, clause_number)` | `document_chunk`, `document_section` | Exact clause lookup — a relational query, not a similarity search |
| `(project, document_type)` | `document` | Type-filtered retrieval |
| `(project, -document_date)` | `document` | Chronology |
| `(user, is_active)` | `project_member`, `organization_membership` | Permission resolution on every request |
| `checksum_sha256` | `document_version` | Duplicate-upload detection |

## Constraints

Enforced in the database, not only in application code:

- `uniq_org_membership` — one membership per user per organization
- `uniq_project_member` — one role per user per project
- `uniq_document_version` — version numbers unique per document
- `uniq_version_page`, `uniq_version_chunk` — no duplicate page or chunk sequence
- `uniq_project_code_per_org` — partial, excluding soft-deleted rows and blanks,
  so a deleted project does not permanently reserve its code

## Extensions

Created by `docker/postgres/init/01-extensions.sql`:

`vector`, `pg_trgm`, `unaccent`, `pg_stat_statements`, plus a `claimiq_english`
text-search configuration that applies `unaccent` before stemming so "Böhler"
and "Bohler" match — which matters for party names on international projects.

## Migration policy

- Migrations are committed and reviewed like code.
- CI runs `makemigrations --check --dry-run`; an un-migrated model change fails
  the build.
- Destructive operations (column drops, type narrowing) are split across
  releases: add, backfill, switch reads, then remove.
- Index creation on large tables uses `CONCURRENTLY` via
  `AddIndexConcurrently`, so an index build does not lock ingestion out of the
  chunk table.

## Unverified

No migration has been generated or applied. The specific risks on first run:

1. `HnswIndex` from `pgvector.django` producing valid DDL in a generated
   migration.
2. `SearchVectorField` population — currently no trigger or `SearchVector`
   update path is wired.
3. The partial unique constraint on `Project` rendering correctly.
4. `settings.AI_SETTINGS["EMBEDDING_DIMENSIONS"]` being read at model-definition
   time, which makes the vector width a deploy-time constant. Changing it
   without a re-embed must be blocked by a migration guard that does not yet
   exist.
