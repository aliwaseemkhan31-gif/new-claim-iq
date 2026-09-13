# ADR 0003 — Modular monolith with isolated workers

**Status:** Accepted
**Date:** 2026-09-12

## Context

The requirement is an enterprise platform that must scale to thousands of
documents and concurrent users, deployed on a *standalone LAN server* — often a
single machine, sometimes air-gapped, administered by people who are not
platform engineers.

## Decision

A **modular monolith**: one Django application, strict internal module
boundaries, background work in Celery workers on separate queues.

Isolation is achieved by **interface boundaries, not process boundaries**:

- `ai.providers` — `LLMProvider`, `EmbeddingProvider`, `RerankerProvider`
- `ingestion.providers` — `TextExtractionProvider`, `OCRProvider`, `LayoutProvider`

Every call into AI or document processing goes through one of these. No domain
or service module imports Ollama, docTR, or any concrete engine directly.

## Consequences

**Positive.**
A single `docker compose up` on one server. One database, one migration history,
one transaction boundary — so a claim, its events, its evidence links and its
audit rows commit atomically. This matters more than deployment elegance for
records that carry contractual weight.

Cross-domain queries stay as SQL joins. The claim analysis engine reads clauses,
correspondence, evidence and events in one query; as microservices this becomes
an N-way distributed fan-out for no gain.

**Negative.**
Horizontal scaling is coarser: the web tier scales as a unit. Acceptable, because
the expensive work (OCR, embedding, inference) is already in workers that scale
independently per queue.

**Migration path preserved.**
Because every AI and processing call crosses a provider interface, extracting
`ingestion` or `ai` into a separate service later means implementing that
interface over HTTP/gRPC. The domain and service layers do not change. This is
the specific property the requirement asked to keep open, and the interfaces are
the mechanism that keeps it open.
