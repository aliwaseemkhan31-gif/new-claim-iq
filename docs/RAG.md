# Retrieval architecture

> **Status.** The pipeline is implemented end to end: query understanding,
> scope validation, three retrievers, fusion, reranking, diversification,
> assembly and citation validation. Orchestration is tested with the
> repository layer mocked (17 tests); the domain layer has 471.
>
> **The SQL has never been executed.** The build host runs PostgreSQL 17 but
> without the pgvector extension, which needs elevation to install. So the
> retrievers in `search/repositories.py` are authored and type-correct but
> unverified against a database. No claim about retrieval *quality* is made
> either — that needs the evaluation harness in §6, which does not exist.

## 1. Why not top-k similarity

The prototype ran a similarity search and passed the results to the model
(`../backend/rag.py:310`). That fails in four ways that matter for contracts:

**Embeddings are weak on the tokens that carry contractual meaning.** Defined
terms ("the Works", "Taking-Over Certificate"), party names, document
references and clause numbers are near-arbitrary strings. Dense retrieval finds
paraphrase; it does not reliably find *the* clause.

**Clause lookup was a similarity hack.** To retrieve Sub-Clause 20.2.1, the
prototype queried the string `"Sub-Clause 20.2.1 20.2.1"` — repeating the
number to bias the vector (`../backend/rag.py:187`). A clause number is exact
data. It deserves an indexed relational lookup, not a nudge to an approximate
search.

**No permission filtering was possible.** Chroma metadata filters have no
concept of project membership, so scoping lived above the store — and was not
implemented.

**Top-k was frequently one document.** A single verbose clause could occupy
every slot, so correspondence that contradicted it never reached the model.

## 2. The pipeline

```
                        USER QUERY
                            │
                  ┌─────────▼──────────┐
                  │ SCOPE RESOLUTION   │  RetrievalScope: org, project,
                  │                    │  edition, doc types, permissions
                  └─────────┬──────────┘  Refuses; never defaults. ADR 0004
                            │
                  ┌─────────▼──────────┐
                  │ QUERY UNDERSTANDING│  extract clause refs, dates,
                  │                    │  parties, amounts; classify intent
                  └─────────┬──────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼──────┐  ┌─────────▼────────┐  ┌───────▼────────┐
│ CLAUSE EXACT │  │ LEXICAL          │  │ VECTOR         │
│ indexed SQL  │  │ tsvector + GIN   │  │ pgvector HNSW  │
│ on clause_no │  │ ts_rank_cd       │  │ cosine         │
└───────┬──────┘  └─────────┬────────┘  └───────┬────────┘
        └───────────────────┼───────────────────┘
                            │  All three carry the same permission predicate,
                            │  in the same SQL. Not a convention — a JOIN.
                  ┌─────────▼──────────┐
                  │ FUSION (RRF)       │  ranks, not scores
                  └─────────┬──────────┘
                  ┌─────────▼──────────┐
                  │ RERANK             │  cross-encoder over the shortlist
                  └─────────┬──────────┘
                  ┌─────────▼──────────┐
                  │ DIVERSIFY          │  per-document cap
                  └─────────┬──────────┘
                  ┌─────────▼──────────┐
                  │ ASSEMBLE           │  budget, opaque refs S1..Sn
                  └─────────┬──────────┘
                  ┌─────────▼──────────┐
                  │ LLM                │  schema-constrained
                  └─────────┬──────────┘
                  ┌─────────▼──────────┐
                  │ CITATION VALIDATION│  refs, quotations, editions, facts
                  └─────────┬──────────┘
                        RESPONSE
```

## 2a. Query understanding

`search/domain/query.py`, 45 tests. Runs before any retriever, and its output
chooses *which* retrievers run rather than merely rewriting the query.

- **Clause references** route to exact relational lookup. The prototype
  extracted the same references and then repeated them in the embedding query
  string to bias a similarity search; the instinct was right, the mechanism was
  not.
- **Intent** selects the corpus. An evidence or chronology question is about
  what happened on *this* project, so the knowledge base is excluded — the
  standard form has nothing to say about it, and including it spends context
  budget on provisions nobody asked about.
- **Constraints** — dates, amounts, defined terms — become metadata filters
  rather than bag-of-words tokens.

Defined terms are matched **case-sensitively**. "Notice" is a defined term with
a specific contractual meaning; "notice" in ordinary prose is not, and
conflating them turns a search for correspondence into a search for the
definitions clause.

Query expansion is deliberately absent. Contracts use defined terms precisely,
and expanding "Notice" into near-synonyms actively harms precision when the
question is whether a contractual Notice was given.

Amount extraction excludes numbers inside a detected date and bare four-digit
year-like numbers — a test caught "14 March 2026" being reported as a monetary
amount of 2026.

## 3. Three retrievers

### Clause-exact

An indexed query on `DocumentChunk.clause_number` / `DocumentSection`. When the
user asks about Clause 20.2.1, the text of 20.2.1 is retrieved because it *is*
20.2.1 — not because it embeds near the phrase. This carries a large fusion
bonus (`CLAUSE_EXACT_BONUS = 2.0`) precisely because it is exact evidence
rather than a similarity estimate.

### Lexical

PostgreSQL `tsvector` + GIN, ranked with `ts_rank_cd`, using a custom
`claimiq_english` configuration that applies `unaccent` before stemming.

This is not BM25 — PostgreSQL lacks BM25's document-length normalisation. For
chunks of bounded, similar size the difference is small, and staying in one
engine buys a single permission filter and a single transactional boundary
(ADR 0002). If evaluation shows lexical recall is the limiting factor, the
escape hatch is a BM25 extension inside PostgreSQL rather than a second store.

### Vector

pgvector with an HNSW index (`m=16`, `ef_construction=64`,
`vector_cosine_ops`). Chunks are embedded with their clause context prepended:

```python
def embedding_text(self) -> str:
    # "shall give a Notice within 28 days" is nearly meaningless alone;
    # "Clause 20.2.1 Notice of Claim\n\n..." is precisely locatable.
```

The `embedding_model` column records which model produced each vector, so
vectors from different models are never compared and a model change is
detectable.

## 4. Fusion

Reciprocal Rank Fusion. A cosine similarity of 0.82 and a `ts_rank_cd` of 0.41
are not comparable and cannot be meaningfully averaged; RRF combines **ranks**,
which sidesteps normalisation entirely.

```
score(chunk) = Σ  weight_i / (k + rank_i)        k = 60
```

Three multiplicative adjustments, all in `search/domain/fusion.py`:

| Adjustment | Factor | Reason |
| --- | --- | --- |
| Found by several retrievers | 1.15 | Independent agreement is a genuine relevance signal |
| Exact clause match | 2.00 | Exact evidence outranks similarity |
| Complete clause, not a fragment | 1.10 | A partial obligation is a misleading citation |

## 5. Assembly

Diversification caps how many chunks one document contributes
(`max_per_document`, default 4), then a character budget selects the final set.
The assembler reports what it dropped and why — `dropped_for_diversity`,
`dropped_for_budget`, `candidates_considered` — so selection is inspectable in
AI observability rather than opaque.

Sources are then relabelled with opaque identifiers `S1`…`Sn`
(`ai/domain/citations.py::assign_refs`). The model never sees database ids, and
the citation space is closed and small enough that an invented reference is
immediately detectable.

### Failure behaviour of each retriever

Vector search degrades; it does not fail the request. With no embedding
provider configured, or with the runtime unreachable, lexical and clause-exact
retrieval still run and the trace records why the result set is thin:

```
"Vector search skipped: no embedding provider is configured.
 Results are lexical and clause-exact only."
```

A partial answer with a recorded caveat beats no answer. What is never
acceptable is the failure text becoming context — a test asserts the note stays
in the trace and out of the assembled chunks.

Matches beyond `MAX_COSINE_DISTANCE` (0.75) are discarded rather than returned
with a low score. A weak match handed to a model as "context" is an invitation
to use it, and fusion would still rank it above nothing.

"No results" and "nothing indexed" are distinguished. An empty result set
carries a reason — documents still processing, or no published knowledge base
for the scoped edition — because sending a user to look for a document that was
never ingested wastes their time.

### Edition safety, in three places

1. **Scope construction** — a knowledge-base scope without an edition raises
   `ScopeValidationError`. There is no `edition=` parameter with a default
   anywhere, asserted by a structural test.
2. **Assembly** — foreign-edition chunks are dropped, with a count.
3. **Citation validation** — a cited chunk whose edition differs from the scope
   raises `EditionMixingError` and the response is not returned.

Three layers because the prototype had the mechanism right and lost it to a
default in one place and an omitted filter in another.

## 6. Evaluation — `PLANNED`

Unit tests prove the mechanism is correct. They say nothing about whether the
right clause comes back for a real question. Until this harness exists, this
document makes no quality claim.

The harness will use a fixed question set with known-correct answers:

| Question shape | Tests |
| --- | --- |
| "What notice period does Clause 20.2.1 require?" | Clause-exact retrieval |
| "Which documents support the contractor's EOT claim?" | Multi-document recall |
| "Was the notice submitted within the contractual period?" | Correspondence + clause join |
| "Which clause governs this event?" | Semantic clause identification |
| "Which correspondence contradicts this claim?" | Adversarial retrieval; diversity |
| "What does the 1987 form require here?" | Edition isolation |

Metrics: Recall@k, Precision@k, MRR, citation accuracy (cited chunk actually
supports the statement), answer grounding rate, and **unsupported-claim rate** —
the share of responses containing an assertion no retrieved source supports.
That last one is the number that matters most for this product; it is the
measurable form of "the AI must not fabricate".

## 7. Configuration

Tunable per deployment, not hardcoded (`RETRIEVAL_SETTINGS`):

| Setting | Default | Purpose |
| --- | --- | --- |
| `LEXICAL_CANDIDATES` | 50 | Lexical shortlist |
| `VECTOR_CANDIDATES` | 50 | Vector shortlist |
| `RERANK_CANDIDATES` | 30 | Cross-encoder input |
| `FINAL_CONTEXT_CHUNKS` | 12 | Chunks in the prompt |
| `RRF_K` | 60 | Fusion damping |
| `MAX_CHUNKS_PER_DOCUMENT` | 4 | Diversity cap |

## 8. Failure behaviour

A retrieval failure **raises**. It does not become context, an answer, or a
degraded result. The prototype returned `f"FIDIC KB unavailable: {e}"` as
retrieval context, so an infrastructure failure could be read by the model as
contract text. The typed error hierarchy in `core/domain/errors.py` makes that
class of bug unrepresentable.
