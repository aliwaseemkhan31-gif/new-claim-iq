# ClaimIQ Enterprise — Architecture

> **Status legend used throughout this document and all sibling docs:**
> `IMPLEMENTED` — code exists, tests pass.
> `PARTIAL` — foundation exists, gaps named explicitly.
> `PLANNED` — designed, not yet built. No code.
>
> Nothing in these docs describes behaviour that does not exist. Where a
> capability is `PLANNED`, it is labelled as such rather than described in the
> present tense.

---

## 1. Current-System Assessment (legacy ClaimIQ)

The legacy prototype lives at the repository root (`../backend`, `../frontend`)
and is **read-only** for the purposes of this project. It is not modified,
migrated in place, or refactored. It is a reference implementation.

### 1.1 What the prototype does well (domain knowledge worth preserving)

| Capability | Where | Why it matters |
| --- | --- | --- |
| Digital-vs-scanned PDF detection with OCR fallback | `../backend/contract_parser.py:83` | The heuristic (sampled text length vs `page_count * 50`) is crude but the *decision point* is correct and is preserved as an explicit pipeline stage. |
| Clause-number regex tuned to construction contracts | `../backend/contract_parser.py:14` | Recognises `14.3`, `14.3.1`, `Clause 14`, `Sub-Clause 20.2`, `Article 5`, `Section 3`. Real domain tuning; carried forward and extended. |
| Edition-tagged FIDIC KB | `../backend/rag.py:119` | The insight that FIDIC editions must never be conflated is the single most important domain lesson in the prototype. |
| KB contamination cleanup | `../backend/deleted_2017_guidance_ids.txt` | 422 chunks (TOC, front-matter, guidance notes) were manually purged from the 2017 KB. This proves KB validation must be **automated and continuous**, not a one-off. |
| Hand-verified clause skeletons | `../backend/fidic_1987_skeleton.py`, `fidic_2017_skeleton.py` | Authoritative clause-number → title maps for both editions, including hand-annotated source quirks (1987 has no Clause 26; numbering jumps 25 → 27). High-value seed data. |
| Retrieval verification dumps | `../backend/verification_1987.txt`, `verification_2017.txt` | Evidence of a manual retrieval-quality pass. Formalised here into an automated evaluation harness. |

### 1.2 Defects and limitations that drive the redesign

| # | Defect | Evidence | Consequence | Redesign response |
| --- | --- | --- | --- | --- |
| D1 | JSON file as system of record | `../backend/db.json` (452 KB, full page text inline) | No concurrency, no transactions, no integrity, no indexes. Whole file rewritten per mutation. | PostgreSQL, normalised, with FK constraints and indexes. |
| D2 | Edition selection hardcoded | `../backend/rag.py:288` `edition: str = "2017"` | The fully-ingested, verified 1987 KB is **unreachable** from the running app. | Edition is a mandatory element of every retrieval scope. No default. |
| D3 | Point lookup ignores edition | `../backend/main.py` `/fidic/clause/{n}` | Can silently return 1987 text for a 2017 project. Directly contradicts D2's own intent. | Retrieval scope is a validated object; a query with no edition is rejected, not defaulted. |
| D4 | Errors returned as answers | `../backend/rag.py:273` `return f"FIDIC KB unavailable: {e}"` | Internal exception text is fed into the LLM prompt as if it were contract context. Failures masquerade as findings. | Typed error hierarchy; retrieval failure raises, never degrades into context. |
| D5 | Clause retrieval by embedding hack | `../backend/rag.py:187` `query_texts=[f"Sub-Clause {clause} {clause}"]` | Clause-number lookup depends on repeating the number to bias a dense vector. Unreliable and unexplainable. | Clause is a **first-class relational entity**. Number lookup is an indexed SQL query, not a similarity search. |
| D6 | Verdict parsed client-side by regex | `../frontend/src/pages/Ask.jsx` `extractVerdict` | The single most important output (VALID/INVALID) is scraped from free text in the browser. Breaks on any phrasing change. | LLM returns validated structured output; verdict is a typed field with a DB constraint. |
| D7 | Dead code | `fidic_*_skeleton.py` — zero importers | Careful domain work (clause titles, source quirks) unused. | Skeletons imported as seed data for the clause registry with provenance. |
| D8 | Undeclared dependency | `python-doctr` used, absent from `requirements.txt` | Clean install breaks on any scanned PDF. | Fully locked dependencies; OCR engine behind a provider interface. |
| D9 | No authentication | Every endpoint open | Any caller can list/read/delete every contract and claim. | Django auth + org/project scoping + permission checks at API *and* service layer. |
| D10 | No tests | Zero test files in the entire tree | No regression safety on a system making contractual assertions. | Mandatory test suite; RAG grounding tests treated as first-class. |
| D11 | Blocking LLM/OCR in request path | `../backend/main.py` `/contract/{id}/ask` | Long OCR/inference blocks the HTTP worker. | Celery workers; jobs are entities with status, progress, retry. |
| D12 | Chunk-only citations | Metadata is `{page, source}` | Cannot resolve a citation to a clause or a bounding box. | Citations resolve to document → page → section → clause, with offsets. |

### 1.3 Explicitly preserved, redesigned, or dropped

- **Preserved (concept):** edition-aware KB; OCR fallback; construction-tuned clause regex; contract + claim + FIDIC three-way context assembly; offline-only operation.
- **Preserved (data, via import tooling):** FIDIC clause skeletons for 1987/2017; extracted contract text and clause indexes from `db.json`. See `MIGRATION.md`.
- **Redesigned:** persistence, retrieval, citation, error handling, verdict production, job execution, access control.
- **Dropped:** ChromaDB as primary store; JSON persistence; client-side verdict parsing; the `Sub-Clause {n} {n}` query hack; the `pysqlite3` shim (unnecessary on PostgreSQL).

---

## 2. Target Architecture

### 2.1 Shape: modular monolith + background workers

A modular monolith is chosen over microservices deliberately (see `adr/0003`).
The domain is highly interconnected — a claim references clauses, correspondence,
evidence and events, and analysis traverses all of them. Distributing that early
would buy deployment complexity and pay for it with distributed joins.

The constraint that makes this safe: **document processing and AI inference are
isolated behind provider interfaces and executed by workers, not by the web
process.** They can be extracted into separate services later without touching
the domain layer.

```
┌──────────────────────────────────────────────────────────────────┐
│ nginx (TLS termination, static, upload streaming, rate limiting) │
└───────────────┬──────────────────────────────┬───────────────────┘
                │                              │
      ┌─────────▼──────────┐        ┌──────────▼───────────┐
      │ Vue 3 SPA (static) │        │ Django + DRF (ASGI)  │
      └────────────────────┘        │  api/v1/*            │
                                    │  ─────────────────   │
                                    │  interface layer     │  serializers, viewsets, permissions
                                    │  service layer       │  use-cases, transactions
                                    │  domain layer        │  pure Python, no Django
                                    │  repository layer    │  ORM queries
                                    └──────┬────────┬──────┘
                                           │        │
                        ┌──────────────────▼─┐   ┌──▼─────────────────┐
                        │ PostgreSQL 16      │   │ Redis 7            │
                        │  + pgvector        │   │  broker + cache    │
                        │  + tsvector/GIN    │   └──┬─────────────────┘
                        │  system of record  │      │
                        └────────────────────┘      │
                                           ┌────────▼──────────────┐
                                           │ Celery workers        │
                                           │  ingest / ai / default│
                                           └────────┬──────────────┘
                                                    │
                                           ┌────────▼──────────────┐
                                           │ Ollama (local LLM)    │
                                           │ local embed/rerank    │
                                           └───────────────────────┘
```

Every arrow terminates inside the deployment boundary. There is no egress.

### 2.2 Layering rules (enforced, not aspirational)

| Layer | Location | May import | Must not import |
| --- | --- | --- | --- |
| Domain | `claimiq/*/domain/` | stdlib, typing | Django, DRF, Celery, ORM models |
| Repository | `claimiq/*/repositories.py` | Django ORM, domain | DRF, Celery |
| Service | `claimiq/*/services/` | repositories, domain, other services | DRF, request/response objects |
| Interface | `claimiq/*/api/` | services, serializers | domain internals, raw ORM writes |
| Tasks | `claimiq/*/tasks.py` | services | DRF |

The domain layer is deliberately framework-free. Two reasons: it is the part
worth unit-testing exhaustively, and it keeps the eventual extraction of the
processing/AI services cheap. This is enforced by an import-linter contract in
CI (`PLANNED`, phase 8) and by convention until then.

A practical consequence used throughout: **domain modules are written to run on
Python 3.9** even though the application targets 3.12. This lets the domain test
suite execute on hosts without the full stack. See `adr/0001`.

### 2.3 Django app map

| App | Responsibility |
| --- | --- |
| `core` | Base models (UUID PK, timestamps, soft delete, audit fields), typed errors, pagination, standard response envelope. |
| `accounts` | Organization, User, Role, Permission, OrganizationMembership. |
| `projects` | Project, ProjectMember, Party, project-scoped access resolution. |
| `documents` | Document, DocumentVersion, DocumentType taxonomy, DocumentCollection, DocumentPage, DocumentSection, DocumentChunk. |
| `ingestion` | The processing pipeline and its stages; ProcessingJob; OCR/layout providers. |
| `clauses` | Clause, SubClause hierarchy, ContractualObligation, Deadline. |
| `knowledge` | KnowledgeBase, edition registry, KB documents/chunks, KB validation rules. |
| `claims` | Claim, ClaimEvent, ClaimIssue, ClaimPosition, Evidence. |
| `correspondence` | Correspondence, Notice, thread/chain reconstruction. |
| `search` | Hybrid retrieval: lexical + vector + metadata filter + rerank + assemble. |
| `ai` | Provider abstractions, model registry, prompt versioning, structured output, citation validation, AI observability. |
| `analysis` | Claim analysis engine (entitlement, notice, causation, quantum, gaps, counterarguments). |
| `reports` | Report definitions, versioned generation, PDF/DOCX rendering. |
| `notifications` | In-app notification model and delivery. |
| `audit` | AuditLog and the write path that populates it. |
| `imports` | Legacy ClaimIQ import: dry-run, validation report, commit, rollback. |

### 2.4 Cross-cutting decisions

- **UUID v4 primary keys** on every domain entity. Avoids ID enumeration (a real
  defect in the prototype, D9) and makes import/merge from external systems safe.
- **Soft delete** (`deleted_at`) on documents, claims, evidence and correspondence.
  Contractual records must not vanish; audit trails must survive deletion.
- **Every AI-derived field carries provenance**: which model, which prompt version,
  which retrieved chunks, what confidence. Stored, not logged and lost.
- **Human-in-the-loop is modelled, not bolted on**: AI output and human assessment
  are separate columns with separate authorship, never overwritten in place.

---

## 3. Implementation Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Architecture, repo structure, DB design, Django core, auth, organizations, projects, documents, Vue shell, Docker, health checks | See `README.md` for live status |
| 2 | Ingestion pipeline, extraction, OCR, pages, sections, clause extraction, processing jobs | |
| 3 | Search: lexical, pgvector, embeddings, hybrid retrieval, reranking | |
| 4 | AI infrastructure: provider abstraction, model registry, prompts, structured output, citations, observability | |
| 5 | Claims intelligence: claims, events, obligations, notices, evidence, correspondence, timeline | |
| 6 | AI claim analysis: entitlement, notice compliance, causation, quantum, gaps, counterarguments, confidence | |
| 7 | Enterprise UX: workspace, document viewer, AI workspace, dashboard, search, reports, notifications | |
| 8 | Hardening: security, testing, performance, audit, backup, deployment, documentation | |

Each phase ends with: tests run, lint run, migrations verified, health checks
verified, and this document updated to reflect what actually exists.

---

## 4. Known Environment Constraints

These are properties of the machine this was built on, recorded so that later
readers do not mistake unverified for verified.

| Constraint | Impact | Mitigation |
| --- | --- | --- |
| Host has Python 3.9 only | Django 5.2 test suite cannot execute on the host | Domain layer written 3.9-compatible and tested on the host; full suite runs in the Python 3.12 container |
| Docker not installed on host | Compose stack cannot be started or verified here | Compose/Dockerfiles authored and statically validated; `OPERATIONS.md` records exactly what remains unverified |
| No GPU assumed | OCR and inference must work on CPU | Hardware profiles (`adr/0007`); CPU profile is the default and is the one assumed correct |
