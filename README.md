# ClaimIQ Enterprise

An offline, local-first **Contract & Claims Intelligence Platform** for
construction projects: contract intelligence, claims analysis, document
intelligence, evidence management and AI-assisted analysis that is always
traceable back to source documents.

> **Relationship to the legacy prototype.** The ClaimIQ prototype at the
> repository root (`../backend`, `../frontend`) is a **read-only reference**.
> It is not modified, migrated in place, or refactored by this project, and it
> remains independently runnable. This application is architecturally
> independent; where the prototype's data is worth keeping, it is brought
> across by explicit import tooling (`docs/MIGRATION.md`).

---

## Status

This is an in-progress build. The table below reflects what is **actually
implemented and verified**, not what is planned. Nothing is marked done that
has not been run.

| Area | Status | Verification |
| --- | --- | --- |
| Architecture, ADRs, domain design | **Done** | `docs/ARCHITECTURE.md`, `docs/adr/` (7 ADRs) |
| Domain layer — clause detection, chunking, KB validation, grounding, retrieval scope + fusion, query understanding, permissions, taxonomy, editions, upload safety, model registry, notice compliance, legacy import, pipeline state machine, quality scoring | **Done** | **471 tests passing** on both Python 3.9 and 3.12 |
| Ingestion pipeline — stage machine, executors, extraction/OCR providers, job persistence, Celery tasks | **Wired (executors not runtime-verified)** | State machine and quality scoring tested; executors need a database |
| Hybrid retrieval — query understanding, three retrievers, fusion, rerank, assembly, edition verification | **Wired (SQL not executed)** | Orchestration tested with mocked repositories (17 tests); SQL needs pgvector |
| Django runtime — system checks, migrations | **Verified** | `manage.py check` clean; migrations generated for 5 apps |
| Vue frontend — design system, app shell, router, API client, stores, core components, views | **Done** | **84 tests passing**, lint clean, build succeeds |
| Django project, settings, error envelope, structured logging, health checks | **Done** | Loads and passes `manage.py check` on Python 3.12 |
| Data model — accounts, projects, documents, ingestion, knowledge | **Done (migrations generated, not applied)** | Applying needs pgvector, absent on this host |
| Docker, compose, nginx, Postgres init, CI | **Authored (not run)** | Docker is not installed on the build host |
| API endpoint layer, claims engine, reports, AI answer generation | Not started | |

### What has genuinely been verified

```
471 passed in 0.62s      # backend/tests/domain on Python 3.9  (ADR 0001 guard)
471 passed in 1.07s      # backend/tests/domain on Python 3.12
 17 passed               # backend/tests/integration — Django, mocked repositories
 84 passed               # frontend — vitest, jsdom
                         # manage.py check: no issues
                         # migrations generated: accounts, projects, documents,
                         #   ingestion, knowledge
                         # frontend lint clean, production build succeeds
```

### What has *not* been verified, and why

- **No SQL has been executed.** The host runs PostgreSQL 17, but without the
  pgvector extension — installing it writes to Program Files and needs
  elevation this session does not have. So migrations are generated but never
  applied, and every query in `search/repositories.py` is unverified against a
  real database.
- **No AI provider has been called.** Ollama is not installed here, so the
  Ollama LLM and embedding providers are authored and unexercised.
- **The Docker stack has never been started.** Docker is not installed on the
  build host. The compose file and Dockerfiles are authored against the
  documented behaviour of the images they use, and are unverified.
- **Retrieval quality is unmeasured.** The evaluation harness in `docs/RAG.md`
  §6 does not exist. Unit tests show the mechanism is correct; they say nothing
  about whether the right clause comes back for a real question.

To unblock the first two: install pgvector for PostgreSQL 17 (needs an elevated
shell), or install Docker Desktop and use the bundled `pgvector/pgvector:pg16`
image.

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
highest-value logic exhaustively unit-testable, and it is why 471 tests run in
0.62s on a host with no database. It is also enforced mechanically: domain
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
│       ├── domain/             471 tests, no I/O, run anywhere
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

### Running the frontend against a local backend

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5273, proxies /api to http://localhost:8100
npm run test:run
npm run lint
```

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
