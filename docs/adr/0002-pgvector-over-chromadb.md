# ADR 0002 — PostgreSQL + pgvector as the retrieval substrate

**Status:** Accepted
**Date:** 2026-09-12

## Context

The prototype used ChromaDB as a parallel persistence layer alongside a JSON
file. That split caused three concrete defects:

1. **No referential integrity between vectors and entities.** A deleted contract
   left its Chroma collection behind (`../backend/rag.py:352` swallows the failure).
2. **Access control could not be applied to retrieval.** Chroma metadata filters
   have no concept of project membership, so scoping had to be reimplemented
   above the store — and in practice was not implemented at all.
3. **Filtering and joining were impossible.** "Chunks from clause 20.2 of the
   contract for project X that are cited by claim Y" is a relational question.
   It cannot be expressed against a metadata dict.

The vector store is also not the only retrieval mechanism we need. Clause-number
lookup must be exact, and lexical/BM25-style retrieval is required for
terminology that embeddings handle poorly (defined terms, party names,
document references, currency amounts).

## Decision

**PostgreSQL 16 with pgvector is the single system of record, including for
embeddings.**

- Dense vectors: `pgvector` `vector` columns with HNSW indexes.
- Lexical retrieval: PostgreSQL `tsvector` + GIN, with `ts_rank_cd` scoring.
- Exact clause lookup: ordinary indexed relational query on the `Clause` table.
- Access control: applied as SQL predicates in the same query as retrieval, so
  it cannot be bypassed by a retrieval path that forgot to filter.

ChromaDB is not a dependency of the new system.

## Consequences

**Positive.**
One transactional boundary. Deleting a document deletes its chunks and its
vectors atomically via FK cascade — D1/D12 in the assessment cannot recur.
Permission filtering is a `JOIN`, not a convention. Hybrid retrieval fuses
lexical and dense results computed in the same database.

**Negative.**
pgvector HNSW index builds are slower than Chroma's for very large corpora, and
pgvector lacks some purpose-built ANN tuning knobs. At the target scale
(thousands of documents per deployment, not billions of vectors) this is not the
binding constraint; correctness and filterability are.

**Note on `ts_rank_cd` vs true BM25.** PostgreSQL full-text ranking is not BM25;
it lacks document-length normalisation of the same form. For this corpus —
chunks of bounded, similar size — the difference is small, and the operational
simplicity of staying in one engine outweighs it. If evaluation (`RAG.md`) shows
lexical recall is the limiting factor, the escape hatch is a `paradedb`/BM25
extension, which stays inside PostgreSQL and does not reintroduce a second store.
