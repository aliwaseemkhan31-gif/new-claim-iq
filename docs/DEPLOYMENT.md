# Deployment

> **Status: AUTHORED, NOT VERIFIED.** Docker is not installed on the machine
> this was built on, so the stack described here has **never been started**.
> The compose file, Dockerfiles and nginx configuration are written against the
> documented behaviour of the images they use. Treat this document as a
> deployment design to be validated on first run, not as a tested runbook.
> Everything unverified is marked. See `OPERATIONS.md` for the first-run
> checklist.

## Target

A standalone LAN server, optionally air-gapped. One machine, `docker compose
up`, administered by people who are not platform engineers.

## Requirements

| | Minimum | Recommended |
| --- | --- | --- |
| CPU | 4 cores | 8+ cores |
| RAM | 16 GB | 32 GB |
| Disk | 100 GB SSD | 500 GB+ NVMe |
| GPU | none (CPU profile) | 16 GB+ VRAM |
| OS | Any with Docker + Compose v2 | Linux |

Disk is dominated by original documents plus extracted text plus vectors —
budget roughly 3× the raw corpus, and more if scanned PDFs are retained at full
resolution.

## Isolation from the legacy prototype

Both stacks can run simultaneously. Every name and port is offset:

| | Legacy | This stack |
| --- | --- | --- |
| Backend | 8000 | **8100** |
| Frontend | 5173 | **5273** dev / **8080** served |
| PostgreSQL | — | **5532** |
| Redis | — | **6479** |
| Ollama | 11434 | **11534** |
| Network | — | `claimiq-enterprise-net` |
| Volumes | — | `claimiq-ent-*` |

Host ports bind to `127.0.0.1` unless `BIND_ADDRESS` says otherwise —
LAN exposure is opt-in, not the default.

## Install

```bash
cd claimiq-enterprise
cp .env.example .env
```

Set the two required secrets. The stack refuses to start without them rather
than falling back to an insecure default:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
```

Then:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8080/api/v1/health/ready | jq
```

The `migrate` service runs migrations, collects static files and seeds
reference data, then exits. Every other service declares
`depends_on: migrate: condition: service_completed_successfully`, so nothing
starts against a half-migrated schema.

Create the first administrator:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Models

Nothing is bundled. Models are provisioned per deployment:

```bash
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama list
```

Then select them in the administration interface. Deliberately not a deployment
constant — the application discovers what the runtime has and offers those.

### Hardware profile

`HARDWARE_PROFILE` is read at **build time** (it selects the torch wheel) and at
**runtime** (it filters model recommendations). Changing it requires a rebuild:

```bash
HARDWARE_PROFILE=gpu-mid docker compose build backend
docker compose up -d
```

`cpu` is the default and pulls the CPU-only torch wheel — roughly 2 GB smaller
than the CUDA build, and correct for a host that has no GPU to use it with.

For GPU, the host needs the NVIDIA Container Toolkit, and the `ollama` service
needs a device reservation added. **Unverified.**

## Air-gapped installation

```bash
# On a connected machine
docker compose build
docker save -o claimiq-images.tar \
    claimiq-enterprise/backend:latest \
    claimiq-enterprise/frontend:latest \
    pgvector/pgvector:pg16 redis:7-alpine nginx:1.27-alpine ollama/ollama:latest

# Export models: pull them, then archive the ollama volume
docker run --rm -v claimiq-ent-ollama-data:/data -v "$PWD":/out \
    alpine tar czf /out/claimiq-models.tar.gz -C /data .
```

Transfer both archives, then:

```bash
docker load -i claimiq-images.tar
docker volume create claimiq-ent-ollama-data
docker run --rm -v claimiq-ent-ollama-data:/data -v "$PWD":/in \
    alpine tar xzf /in/claimiq-models.tar.gz -C /data
docker compose up -d
```

The images set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, so a missing
model fails loudly rather than silently attempting a download that cannot
succeed.

## Backup

```bash
# Database
docker compose exec -T postgres pg_dump -U claimiq -Fc claimiq > backup-$(date +%F).dump

# Uploaded documents
docker run --rm -v claimiq-ent-media:/data -v "$PWD":/out \
    alpine tar czf /out/media-$(date +%F).tar.gz -C /data .
```

Back up **both**. The database holds extracted text, chunks and vectors; the
media volume holds the original files. Either alone is an incomplete restore —
without the originals, citations cannot be resolved to a source page.

### Restore

```bash
docker compose stop backend worker-ingestion worker-ai worker-default beat
docker compose exec -T postgres pg_restore -U claimiq -d claimiq --clean --if-exists < backup.dump
docker run --rm -v claimiq-ent-media:/data -v "$PWD":/in \
    alpine sh -c "rm -rf /data/* && tar xzf /in/media.tar.gz -C /data"
docker compose up -d
curl -fsS http://localhost:8080/api/v1/health/ready
```

**Untested.** Rehearse a restore on a non-production host before relying on it.

## TLS

Place `fullchain.pem` and `privkey.pem` in `docker/nginx/certs/`, add a `443`
server block to `gateway.conf`, then set in `.env`:

```
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
```

These default to `false` because a genuinely isolated LAN deployment may run
plain HTTP behind a firewall, and forcing a redirect there makes the product
unreachable rather than more secure.

## Scaling

Workers scale independently per queue — the expensive work is already there:

```bash
docker compose up -d --scale worker-ingestion=4
```

Keep `AI_CONCURRENCY=1` unless the GPU has headroom; concurrent inference on one
device usually reduces total throughput. `CELERY_WORKER_PREFETCH_MULTIPLIER=1`
and `acks_late` mean a worker takes one task at a time and a crash re-queues it
rather than losing it.

## What must be validated on first run

Nothing below has been executed. In order:

1. `docker compose build` completes for both images.
2. `pgvector/pgvector:pg16` runs `docker/postgres/init/01-extensions.sql` and
   the `claimiq_english` text-search configuration is created.
3. `migrate` completes and exits 0.
4. `HnswIndex` from `pgvector.django` produces valid DDL in the generated
   migration.
5. `/api/v1/health/ready` returns 200 with all four checks `ok`.
6. Every `depends_on` condition resolves and no service starts early.
7. The frontend image builds and nginx serves the SPA with history fallback.
8. An upload larger than `FILE_UPLOAD_MAX_MEMORY_SIZE` streams to disk rather
   than buffering.
9. `torch` imports in the backend image under the CPU profile.
10. Celery workers register on their queues and `inspect ping` succeeds.
