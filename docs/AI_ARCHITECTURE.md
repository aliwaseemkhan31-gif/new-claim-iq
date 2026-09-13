# AI architecture

> **Status.** Provider interfaces, the model registry, hardware profiles and
> the grounding/citation validator are implemented and tested (58 tests). The
> concrete Ollama, embedding and reranker implementations, prompt management
> and the observability tables are `PLANNED`.

## 1. The boundary

**The AI is an assistant, not an authority.** It surfaces provisions, finds
correspondence, extracts obligations and drafts an analysis. It does not
determine entitlement. Every substantive output is traceable to a source
document, and human assessment is a separate, attributed field that AI output
never overwrites.

This is a product decision with architectural consequences, not a disclaimer.
It is why findings carry an epistemic status, why human assessment is a
different column rather than an edit, and why "insufficient evidence" is a
first-class result.

## 2. Provider abstraction

Five interfaces in `ai/domain/providers.py`:

```
AIProvider
├── LLMProvider        generate(model, request) -> GenerationResult
├── EmbeddingProvider  embed(model, texts) -> EmbeddingResult
├── RerankerProvider   rerank(model, query, documents) -> RerankResult
└── OCRProvider        extract(path, page_numbers) -> [ExtractedPage]
```

No service, task or domain module imports Ollama, sentence-transformers or
docTR. Two reasons: the administrator picks the model rather than the developer
(the prototype hardcoded `OLLAMA_MODEL = "mistral"`), and it is the seam that
keeps service extraction cheap (ADR 0003) — moving inference out later means
implementing these over HTTP, with nothing above the interface changing.

`ModelCapability` is declared, not assumed. Asking a model that cannot honour a
JSON schema for structured output produces prose that fails validation and
burns a retry; checking the capability first turns that into an immediate,
explicable error.

`OCRProvider.extract` takes `page_numbers` as a required part of the interface,
not an optional extra — it is what makes ingestion resumable, so a job that
fails at page 400 of 500 restarts at 400 rather than at 1.

## 3. Model registry

`ai/domain/model_registry.py`. No model name appears in application logic.

Resolution order:

1. **Configured model**, if the runtime has it. An administrator's explicit
   choice is honoured even when it is not recommended for the profile — they
   may know something the registry does not.
2. **Best available recommendation** for the hardware profile.
3. Otherwise `ModelNotConfiguredError`, naming what was expected, what was
   found, and the remedy. "No model configured" without that detail is a
   frustrating dead end.

Discovery is live: an air-gapped installation has whatever was provisioned onto
it, which the application cannot know in advance. `reconcile()` compares
recommendations against `list_models()` and reports the gap.

Quantisation suffixes are normalised, so `qwen2.5:14b-instruct-q4_K_M` satisfies
`qwen2.5:14b-instruct`. Quantisation is a deployment choice, not a different
model.

### Recommended models

Qwen 2.5 Instruct is the default family: strong instruction following, reliable
JSON under constrained decoding, genuinely multilingual, and — the property
that decided it — available at sizes spanning every profile from CPU to a 24 GB
GPU. One family across all profiles means prompts behave consistently as a
deployment scales.

| Profile | LLM | ~VRAM |
| --- | --- | --- |
| `cpu` | `qwen2.5:3b-instruct` | — |
| `gpu-entry` (~8 GB) | `qwen2.5:7b-instruct` | 5.5 GB |
| `gpu-mid` (~16 GB) | `qwen2.5:14b-instruct` | 10 GB |
| `gpu-high` (24 GB+) | `qwen2.5:32b-instruct` | 20 GB |

Embedding: **BGE-M3** (1024 dimensions) — long-input tolerant and multilingual,
which matters on projects where correspondence is not all in English.
Reranking: **BGE Reranker v2 M3**.

`all-MiniLM-L6-v2` is registered at low preference solely so imported legacy
embeddings remain identifiable. It is not a recommended default.

These are **recommendations, not bundled assets**. Nothing is downloaded.

All LLM recommendations default to `temperature = 0.0`. Contractual analysis
should be reproducible; sampling variance is not a feature here. A test asserts
it across the catalogue.

Oversized selections **warn rather than refuse**. VRAM estimates are
approximate, and quantisation, offloading and shared memory all move the real
figure; the administrator is better placed to judge than a static table.

## 4. Grounding

Four independent mechanisms (ADR 0005), because a prompt instruction with no
check is a hope. The prototype's prompt already said *"Never invent clause
numbers"* and had nothing to detect when that was ignored.

### Structured output

The model returns JSON against a declared schema, via constrained decoding
where supported. `verdict` is an enum with a database constraint, not prose.
Invalid output is retried once, then fails the job — never partially parsed by
regex. The prototype scraped its verdict from free text with a **browser-side
regex**, so any rephrasing silently changed the answer shown.

### Closed-world citation identifiers

Sources are presented as `S1`…`Sn`, assigned at assembly time. A citation is a
dictionary lookup, not a string to be parsed, so `S47` against eight sources is
caught immediately.

### Post-generation validation

`ai/domain/citations.py` checks every citation:

- the identifier was in the assembled context, else `UnknownCitationError`;
- a cited knowledge-base source matches the scope's edition, else
  `EditionMixingError`;
- any verbatim quotation occurs in the cited source after normalising
  whitespace and typography, else `UnsupportedQuotationError`.

Quotation matching is lenient about typography and strict about words. A model
reproducing a provision with a straight apostrophe has fabricated nothing; one
changing "28 days" to "42 days", or "shall" to "may", has:

```python
def test_modal_change_is_caught():
    assert verify_quotation("The Notice may be given as soon as practicable",
                            SOURCE_TEXT) is False
```

### Epistemic status

```python
class EpistemicStatus(str, Enum):
    FACT       # directly supported by cited text — citation required
    INFERENCE  # reasoned from cited facts — citation required
    OPINION    # professional judgement, not presented as established
    UNKNOWN    # the sources do not establish this
```

`FACT` and `INFERENCE` without a citation raise `UncitedFactError`. `UNKNOWN` is
a valid, expected outcome — the schema has a branch for it, so the model has a
legitimate way to decline rather than being forced into a shape that only fits
an answer. That is the structural form of "must say *insufficient evidence*".

Validation collects **all** problems rather than stopping at the first, so an
operator sees the full picture; `enforce_grounding` then raises the most severe.

## 5. Observability — `PLANNED`

Every AI operation will record: model and version, prompt template version,
retrieval strategy, retrieved chunk ids with scores, reranker scores, final
assembled context, response, latency, token counts, and the grounding report.

**Chain-of-thought is not stored.** Only structured rationale and the citations
supporting it. Storing raw reasoning traces creates a record that reads as
authoritative while being unvalidated, which is precisely the confusion this
architecture exists to prevent.

## 6. What the AI must never do

| Rule | Enforcement |
| --- | --- |
| Invent a clause number, date, amount or correspondence | Citation validation; unsupported statements rejected |
| Quote text that does not exist | Verbatim verification against source |
| Cite a source that was not retrieved | Closed-world identifiers |
| Mix contract editions | Scope validation, assembly filter, citation check |
| Present uncertainty as certainty | Typed epistemic status |
| Be the final authority | Human assessment stored separately and attributed |
| Reach the internet | No external code path; offline flags in settings and image |
