# ClaimIQ Enterprise

An offline, local-first **Contract & Claims Intelligence Platform** for
construction projects: contract intelligence, claims analysis, document
intelligence, evidence management and AI-assisted analysis that is always
traceable back to source documents.

> **Relationship to the legacy prototype.** The ClaimIQ prototype is a
> **read-only reference**, kept in its own separate checkout. It is not
> modified, migrated in place, or refactored by this project, and it remains
> independently runnable. This application is architecturally independent;
> where the prototype's data is worth keeping, it is brought across by explicit
> import tooling (`docs/MIGRATION.md`).
>
> Paths written as `../backend/...` throughout the documentation are citations
> into the prototype's source, identifying where a defect lived. They date from
> when this project was a subdirectory of that repository and no longer resolve
> from here. Set `CLAIMIQ_LEGACY_ROOT` to point the import tooling and its
> tests at your prototype checkout; without it they look in the conventional
> sibling locations and skip cleanly if absent.

---

## Status

This is an in-progress build. The table below reflects what is **actually
implemented and verified**, not what is planned. Nothing is marked done that
has not been run.

| Area | Status | Verification |
| --- | --- | --- |
| Architecture, ADRs, domain design | **Done** | `docs/ARCHITECTURE.md`, `docs/adr/` |
| Domain layer — clause detection, chunking, KB validation, grounding, retrieval scope + fusion, query understanding, permissions, editions, notice compliance, evidence gaps, chronology, pipeline state machine, quality scoring, analysis strands, confidence rules, review resolution | **Done** | **944 tests passing** on Python 3.9 |
| Data model and migrations — accounts, projects, documents, ingestion, knowledge, claims, correspondence, analysis | **Verified** | Applied to PostgreSQL 17 + pgvector 0.8.0; HNSW indexes present |
| Hybrid retrieval — clause-exact, lexical, vector, fusion, edition verification | **Verified** | SQL executed against pgvector; edition isolation checked by SQL |
| Grounded answering (Phase 4) — Ollama LLM + embeddings, closed-world citations, quotation checks | **Verified** | Live against `qwen2.5:3b-instruct` and `bge-m3` |
| Claims intelligence (Phase 5) — claims, issues, evidence, notices, compliance, chronology | **Verified** | API and computations run against the real database |
| Claim analysis engine (Phase 6) — seven strands, confidence caps, immutable findings, append-only review | **Verified** | See below |
| Ingestion pipeline — extraction, OCR, tables, chunking, embeddings, indexing | **Verified** | Runs on real PDFs, scans, images and DOCX; OCR via RapidOCR (offline, CPU) |
| Knowledge base (Phase 7) — upload a standard form, process, validate, publish | **Verified** | FIDIC Red Book 1987 ingested and published; 103 clauses; retrieval cites it |
| Screens (Phase 7) — projects, documents, viewer, claims, evidence, correspondence, timeline, clauses, AI, knowledge base, reports, admin, dashboard, notifications | **Done** | No "not available yet" placeholders remain |
| Reports, notifications, dashboard, administration APIs | **Verified** | Reports render to PDF and DOCX from a frozen snapshot |
| Docker, compose, nginx, CI | **Authored (not run)** | Docker is not installed on the build host |
| Audit log, login lockout, API tokens, performance, backups | Not started | Phase 8 |

### What has genuinely been verified

```
944 passed               # backend/tests/domain on Python 3.9  (ADR 0001 guard)
116 passed               # backend/tests/integration — Django, no database
184 passed               # frontend — vitest, jsdom
                         # counts as of October 2026; see docs/TESTING.md to re-run
                         # manage.py check clean; lint clean; production build OK
                         # Phase 6 live: claim analysis through the HTTP API
                         #   against PostgreSQL + pgvector and Ollama
                         #   (qwen2.5:3b-instruct, bge-m3) — 7 strands in 80s
                         # Phase 7 live: FIDIC Red Book 1987 uploaded, OCR'd
                         #   where scanned, chunked, embedded, validated and
                         #   published; search and an AI answer then cited
                         #   Clause 53.1 p.31 for the 28-day notice period
                         # Phase 7 live: claim assessment report generated to
                         #   PDF (3 pages) and DOCX from a frozen snapshot
```

### What has *not* been verified, and why

- **The Docker stack has never been started.** Docker is not installed on the
  build host. The compose file and Dockerfiles are authored and unverified.
- **Retrieval and analysis quality are unmeasured.** The evaluation harness in
  `docs/RAG.md` §6 does not exist. Tests show the mechanism is correct; they say
  nothing about whether a 3B model's analysis of a real claim is any good. Every
  AI finding is therefore unreviewed until a person reviews it.
- **No real contract has been ingested on this host.** Live checks use short
  fixture passages written for the test, not the contract text.

This distinction is deliberate and is maintained throughout the documentation.
See `docs/ARCHITECTURE.md` §4.

---

## The problems this exists to solve

Each of these is a defect observed in the prototype, with the design response.
The full assessment is in `docs/ARCHITECTURE.md` §1.

| Prototype behaviour | Consequence | Response here |
| --- | --- | --- |
| `edition: str = "2017"` as a default | The fully-ingested, verified FIDIC 1987 knowledge base was unreachable from the app; a 2017 default silently decided which contract governs | `RetrievalScope` requires an explicit edition; no default exists anywhere (ADR 0004) |
| `/fidic/clause/{n}` applied no edition filter | A clause lookup could return 1987 text for a 2017 project | Edition is validated at construction *and* re-verified after retrieval |
| 422 contents/guidance chunks in the KB, removed by hand | Contents lines retrieved as context look authoritative but contain no provision text | Automated, blocking KB validation on every ingest |
| Prompt said "never invent clause numbers"; nothing checked | An instruction without a check is a hope | Closed-world citation ids, quotation verification, typed fact grounding (ADR 0005) |
| `return f"FIDIC KB unavailable: {e}"` used as LLM context | Infrastructure failures read to the model as contract text | Typed errors; a failure raises and never becomes context |
| Verdict scraped from prose by a browser regex | The most consequential output broke on any rephrasing | Structured output; verdict is a typed field with a DB constraint |
| `db.json`, 452 KB, rewritten per mutation | No concurrency, transactions, integrity or indexes | PostgreSQL with FK constraints and indexes |
| No authentication on any endpoint | Every contract readable and deletable by anyone | Django auth, org/project scoping, deny-by-default DRF |
| Zero tests | No regression safety on contractual assertions | 471 domain + 17 integration + 84 frontend tests |

---

## Architecture at a glance

Modular monolith with background workers (ADR 0003). Layering is strict:

```
interface (DRF)  ->  service  ->  repository  ->  PostgreSQL
                          |
                       domain  (pure Python, no Django)
```

The domain layer imports only the standard library. That keeps the
highest-value logic exhaustively unit-testable, and it is why all 944 of
them run in under two seconds on a host with no database. It is also enforced mechanically: domain
modules are written to run on Python 3.9, so importing Django there breaks the
domain test run (ADR 0001).

Everything runs locally. There is no code path to OpenAI, Anthropic, Google,
Azure, a hosted OCR service or a cloud vector database.

- **Store:** PostgreSQL 16 + pgvector — one system of record for entities *and*
  vectors, so permission filtering is a `JOIN` rather than a convention (ADR 0002)
- **Retrieval:** hybrid — PostgreSQL lexical + dense vector + metadata filter +
  rerank
- **Inference:** Ollama, model chosen by an administrator at runtime; no model
  name is hardcoded
- **Workers:** Celery on separate `ingestion` / `ai` / `default` queues

---

## Repository layout

```
claimiq-enterprise/
├── backend/
│   ├── config/                 Django project: settings, urls, celery, asgi
│   ├── claimiq/
│   │   ├── core/               base models, errors, logging, error envelope
│   │   ├── accounts/           organizations, users, RBAC
│   │   ├── projects/           projects, members, parties
│   │   ├── documents/          documents, versions, pages, sections, chunks
│   │   ├── ingestion/          processing pipeline + clause detection, chunking
│   │   ├── knowledge/          knowledge bases, editions, KB validation
│   │   ├── clauses/  claims/  correspondence/
│   │   ├── search/             retrieval scope, hybrid retrieval
│   │   ├── ai/                 providers, prompts, citations, observability
│   │   ├── analysis/  reports/  notifications/  audit/  imports/
│   │   └── */domain/           pure Python, framework-free, 3.9-compatible
│   └── tests/
│       ├── domain/             944 tests, no I/O, run anywhere
│       └── integration/        require Django + PostgreSQL
├── frontend/                   Vue 3 + Vite SPA
├── docker/                     Dockerfiles, nginx, Postgres init
├── docs/                       architecture, ADRs, operations
├── docker-compose.yml
└── .env.example
```

---

## Running it

### Prerequisites

Docker with Compose v2. Nothing else — no internet access is required at
runtime, though images and model weights must be present.

### Quick start

```bash
cp .env.example .env
```

Set the two required secrets in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
```

Then:

```bash
docker compose up -d --build
```

The `migrate` service runs migrations and seeds reference data, and every other
service waits for it to complete — so nothing ever starts against a
half-migrated schema.

Create the first administrator:

```bash
docker compose exec backend python manage.py createsuperuser
```

Pull a model into Ollama (nothing is bundled; models are chosen per deployment):

```bash
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

Then set the active model in the administration interface. It is deliberately
not a deployment constant.

| Service | URL |
| --- | --- |
| Application | http://localhost:8080 |
| API | http://localhost:8080/api/v1/ |
| Liveness | http://localhost:8080/api/v1/health/live |
| Readiness | http://localhost:8080/api/v1/health/ready |

Ports are offset from the defaults (backend `8100`, Postgres `5532`, Redis
`6479`, Ollama `11534`) so this stack runs alongside the legacy prototype
without collision. Host ports bind to `127.0.0.1` unless `BIND_ADDRESS` says
otherwise.

### Running the domain tests without the stack

No database, broker or container runtime needed — any Python 3.9+:

```bash
cd backend
python -m venv .venv-domain
./.venv-domain/Scripts/python -m pip install pytest
./.venv-domain/Scripts/python -m pytest tests/domain -q
```

### Running locally without Docker (Windows)

Verified end to end on Windows 11 with PostgreSQL 17 + pgvector 0.8.0,
Python 3.12 and Node 22: sign-in, dashboard health checks and the Settings page
all working against the real backend.

**Prerequisites:** Python 3.12, Node 22, PostgreSQL with the `vector` extension
installed, and (optional, for AI answers) Ollama with `qwen2.5:3b-instruct` and
`bge-m3` pulled. Redis is **not** required locally.

**One-time setup**

1. Create the database role, database and extensions. Run as the `postgres`
   superuser (psql prompts for that password), choosing a password for the
   application's own `claimiq` role:
   ```powershell
   psql -U postgres -v app_password="choose-a-strong-password" -f scripts\setup_local_db.sql
   ```
   Then in `.env` set `POSTGRES_PASSWORD` to that same value and
   `POSTGRES_HOST=localhost`. (`copy .env.example .env` first if you have no
   `.env`, and fill in `DJANGO_SECRET_KEY`.)
2. Backend environment:
   ```powershell
   cd backend
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements-local.txt
   python manage.py migrate
   python manage.py bootstrap_admin --email you@example.com --organization "Your Company"
   ```
   `bootstrap_admin` prompts for a password (12+ characters). Use it rather
   than `createsuperuser`, which creates a user with no organization — who can
   then sign in but do nothing.
3. Frontend dependencies: `cd frontend` then `npm install`.

**Every time**

- Terminal 1: `cd backend`, `.venv\Scripts\activate`,
  `python manage.py runserver 8100`
- Terminal 2: `cd frontend`, `npm run dev`
- Open http://localhost:5273 and sign in.

The port must be **8100**: the Vite dev server proxies `/api` there.
`manage.py` uses `config.settings.dev`, which reads `.env` itself and, without
Redis, uses an in-process cache and runs background jobs inline — so a document
upload blocks until that document is processed.

Tests: `npm run test:run` and `npm run lint` in `frontend`;
`python -m pytest tests -q` in `backend`.

---

## Documentation

| Document | Contents |
| --- | --- |
| `docs/ARCHITECTURE.md` | Prototype assessment (12 numbered defects), target architecture, layering, roadmap, environment constraints |
| `docs/adr/` | Seven architecture decisions with rationale and trade-offs |
| `docs/DATABASE.md` | Entity map, the citation chain, indexes, constraints, migration policy |
| `docs/DOCUMENT_PROCESSING.md` | The ingestion pipeline stage by stage |
| `docs/RAG.md` | Hybrid retrieval, fusion, assembly, edition safety, evaluation plan |
| `docs/AI_ARCHITECTURE.md` | Provider abstraction, model registry, the four grounding mechanisms |
| `docs/SECURITY.md` | Threat model, authorisation model, upload handling, known gaps |
| `docs/TESTING.md` | Both suites, what each covers, what is not covered |
| `docs/DEPLOYMENT.md` | Install, air-gapped transfer, backup, first-run validation list |
| `docs/OPERATIONS.md` | Verification ledger, health endpoints, common situations |
| `docs/MIGRATION.md` | Legacy import: what moves, what deliberately does not, dry-run process |

`API.md` is written once the endpoint layer exists, so that it documents routes
rather than intentions.

Every document marks what is implemented against what is authored but
unverified. `docs/OPERATIONS.md` opens with the verification ledger.

---

## Design commitments

1. **The AI is an assistant, not an authority.** Every substantive conclusion
   carries a source reference, and human assessment is a separate, attributed
   field that AI output never overwrites.
2. **"Insufficient evidence" is a first-class result.** The response schema has
   a branch for it, so the model has a valid way to decline rather than being
   pushed into a shape that only fits an answer.
3. **Editions are never mixed.** Not by default, not by omission, not by a
   dropped filter — checked at scope construction and again after retrieval.
4. **No fabricated features.** No mock data presented as real, no fake
   progress, no placeholder answers. Where something is unimplemented it is
   stated plainly, here and in the UI.
