# ADR 0001 — Runtime and framework versions

**Status:** Accepted
**Date:** 2026-09-12

## Context

The build host has Python 3.9 only and no Docker. The deployment target is a
standalone LAN/air-gapped server where we control the container image.

Django 4.2 LTS supports Python 3.9 — it would run on the host — but its extended
support ended April 2026, which is in the past. Shipping a commercial product on
an end-of-life framework is not defensible.

Django 5.2 LTS is supported to April 2028 and requires Python 3.10+.

## Decision

- **Application runtime:** Python 3.12, Django 5.2 LTS, DRF 3.15+.
- **Domain layer:** written to remain **Python 3.9-compatible syntax**.
  - `from __future__ import annotations` in every domain module, so PEP 604
    (`int | None`) annotations parse on 3.9.
  - No `match` statements, no PEP 695 type parameter syntax, no `tomllib` in
    domain code.
  - Domain modules import only the standard library and each other.

## Consequences

**Positive.**
The domain layer — clause detection, layout analysis, chunking, citation
validation, KB validation, obligation parsing — is the highest-value code to
test and is testable on any host with any Python from 3.9 up, with no database,
no Redis, no Ollama and no container runtime. On this build host that is the
difference between a test suite that runs and a test suite that is only claimed
to run.

It also enforces the layering rule from `ARCHITECTURE.md` §2.2 mechanically: if a
domain module imports Django, it stops being 3.9-runnable and the domain test
run fails. The constraint is self-policing.

**Negative.**
Domain code cannot use 3.10+ syntax sugar. This is a small cost; the affected
constructs are conveniences, not capabilities.

**Accepted risk.**
The Django/DRF/Celery layers are not executable on this build host. Their tests
are authored but are verified in the container, not here. `OPERATIONS.md` records
this distinction explicitly rather than implying full local verification.
