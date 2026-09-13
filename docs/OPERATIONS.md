# Operations

> **Status: NOT RUNTIME-VERIFIED.** The stack has never been started — Docker
> is not installed on the machine this was built on. This document is a
> first-run and operations guide to be validated, not a tested runbook.

## Verification ledger

The single most useful thing this document can carry: an honest record of what
has and has not been executed.

| Component | State | Evidence |
| --- | --- | --- |
| Domain layer (11 modules) | **Verified** | 321 tests pass in 0.38s |
| Legacy import against real prototype files | **Verified** | Parses the actual skeletons; asserts mtimes unchanged |
| Python syntax, all backend modules | **Verified** | `compileall` clean |
| Legacy app untouched | **Verified** | `git status` and `git diff` clean on `backend/`, `frontend/` |
| Django settings load | Not verified | Needs Python 3.12 |
| Migrations | Not generated | Needs a database |
| DRF endpoints | Not verified | Needs the stack |
| Celery workers | Not verified | Needs Redis |
| Docker images | Not built | Docker absent |
| Compose stack | Never started | Docker absent |
| Ollama integration | Not verified | Needs the runtime |
| Backup/restore | Not rehearsed | Needs the stack |

Anything marked "not verified" is authored against documented behaviour. It has
not been observed working.

## First-run checklist

Work through in order; each step's failure is easier to diagnose before the
next runs.

```bash
# 1. Config
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
docker compose config --quiet          # compose file parses

# 2. Build
docker compose build                   # both images

# 3. Data layer first, alone
docker compose up -d postgres redis
docker compose exec postgres psql -U claimiq -d claimiq \
    -c "SELECT extname, extversion FROM pg_extension;"
# expect: vector, pg_trgm, unaccent, pg_stat_statements

# 4. Migrations
docker compose run --rm backend python manage.py makemigrations --check --dry-run
docker compose run --rm backend python manage.py migrate --noinput

# 5. Everything
docker compose up -d
docker compose ps
curl -fsS http://localhost:8080/api/v1/health/ready | jq

# 6. First user
docker compose exec backend python manage.py createsuperuser

# 7. Models
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama list
```

Step 3 before step 4 matters: a migration failing because pgvector is missing
looks like a Django problem and is not.

### Expected readiness response

```json
{
  "status": "ready",
  "checks": {
    "database":   {"ok": true, "latency_ms": 1.2},
    "pgvector":   {"ok": true, "version": "0.7.4"},
    "migrations": {"ok": true, "pending": 0},
    "cache":      {"ok": true, "latency_ms": 0.8}
  }
}
```

## Health endpoints

| Endpoint | Auth | Checks | Use |
| --- | --- | --- | --- |
| `/api/v1/health/live` | none | nothing external | Container liveness. Failure ⇒ restart |
| `/api/v1/health/ready` | none | DB, pgvector, migrations, cache | Traffic gating. Failure ⇒ wait, do **not** restart |
| `/api/v1/health/status` | required | the above plus configuration | Operator diagnosis |

Liveness deliberately touches nothing external. Restarting a healthy
application because PostgreSQL blinked turns a blip into an outage.

## Monitoring

Logs are one JSON object per line with `request_id`, `user_id`, `job_id`,
`duration_ms` and a `level`. There are no `print()` calls; the prototype used
them throughout, producing output that could not be filtered or correlated.

```bash
docker compose logs -f backend | jq 'select(.level=="ERROR")'
docker compose logs -f worker-ingestion | jq 'select(.job_id=="<id>")'
docker compose logs backend | jq 'select(.duration_ms > 5000)'
```

Worth watching: readiness failures, Celery queue depth, `error_code` frequency
by code, ingestion failure rate, and generation latency.

```bash
docker compose exec redis redis-cli llen ingestion
docker compose exec redis redis-cli llen ai
docker compose exec backend celery -A config.celery inspect active
```

## Common situations

**Readiness reports `pgvector: extension_not_installed`.** The init script only
runs on first cluster creation. If the volume predates it:

```bash
docker compose exec postgres psql -U claimiq -d claimiq -c "CREATE EXTENSION vector;"
```

**Readiness reports pending migrations.** The `migrate` service did not
complete. Check `docker compose logs migrate`; do not start workers until it
exits 0.

**An AI call returns `ai_model_not_configured`.** The error names what was
expected and what the runtime has. Usually the model was never pulled:

```bash
docker compose exec ollama ollama list
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

**A worker is killed repeatedly.** Almost always the OOM killer during OCR or
inference. Reduce `INGESTION_CONCURRENCY`, keep `AI_CONCURRENCY=1`, or lower
`CELERY_MAX_TASKS_PER_CHILD` so processes recycle sooner.

**An ingestion job is stuck.** `acks_late` means a crashed worker's task is
re-queued, not lost. Inspect before intervening:

```bash
docker compose exec backend celery -A config.celery inspect active
```

**A user reports an error.** Ask for the request id — it is in every error
envelope and every response header:

```bash
docker compose logs backend | jq 'select(.request_id=="<id>")'
```

## Routine maintenance

```bash
# Daily: database + media, both
docker compose exec -T postgres pg_dump -U claimiq -Fc claimiq > backup-$(date +%F).dump
docker run --rm -v claimiq-ent-media:/data -v "$PWD":/out \
    alpine tar czf /out/media-$(date +%F).tar.gz -C /data .

# Weekly
docker compose exec postgres vacuumdb -U claimiq -d claimiq --analyze
docker compose exec backend python manage.py validate_knowledge_base --all
```

Back up the database **and** the media volume. The database holds extracted
text, chunks and vectors; the media volume holds the original files. Without the
originals, citations cannot be resolved to a source page — the restore is
incomplete in a way that is not obvious until someone clicks a citation.

Rehearse a restore on a non-production host before relying on it. It has not
been tested here.

## Upgrades

```bash
docker compose exec -T postgres pg_dump -U claimiq -Fc claimiq > pre-upgrade.dump
git pull
docker compose build
docker compose up -d          # migrate runs first; others wait on it
curl -fsS http://localhost:8080/api/v1/health/ready
```

If `EMBEDDING_DIMENSIONS` changed, every stored vector is invalid and must be
regenerated:

```bash
docker compose run --rm backend python manage.py reembed --all
```

## Running alongside the legacy prototype

Both stacks coexist. Ports, container names, network and volumes are all
namespaced (`DEPLOYMENT.md`). The legacy application is never read from or
written to at runtime by this system — the only contact is the import tool,
which reads its files and is asserted read-only by test.
