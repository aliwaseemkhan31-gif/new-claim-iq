# Testing

## Two suites, and why

| Suite | Location | Needs | Runs on this host |
| --- | --- | --- | --- |
| Domain | `backend/tests/domain/` | Python 3.9+ and pytest. No database, broker, model runtime or container. | **Yes** |
| Integration | `backend/tests/integration/` | Django 5.2, Python 3.12, PostgreSQL 16 + pgvector, Redis. | **No** — see Constraints |

The split follows the layering rule in `ARCHITECTURE.md` §2.2. The domain layer
holds the logic most worth testing exhaustively — clause detection, chunking,
knowledge-base validation, grounding, retrieval scope, permissions, taxonomy,
editions, upload safety, model selection, legacy import — and none of it
imports Django. So it tests in 0.39 seconds with no infrastructure, and every
one of those tests actually ran.

That is also why the domain layer is written to Python 3.9 syntax (ADR 0001): a
domain module that imports Django stops being 3.9-runnable and breaks the
domain run. The architectural rule is enforced by the test suite rather than by
review.

## Current state

```
321 passed in 0.39s
```

Breakdown:

| Module under test | Tests | What it covers |
| --- | --- | --- |
| `test_clause_detection.py` | 31 | Headings vs cross-references, contents-page exclusion, hierarchy, orphan re-parenting |
| `test_file_safety.py` | 41 | Path traversal, magic-byte detection, type mismatch, PDF active content |
| `test_grounding.py` | 30 | Unknown citations, quotation verification, fact grounding, edition agreement |
| `test_kb_validation.py` | 26 | Contents contamination, guidance text, duplicates, running headers, coverage |
| `test_permissions.py` | 24 | Role/permission resolution, scope separation, least privilege |
| `test_legacy_import.py` | 32 | Skeleton parsing, `db.json`, dry-run planning, real prototype files |
| `test_model_registry.py` | 28 | Hardware profiles, availability reconciliation, selection, warnings |
| `test_fusion.py` | 28 | RRF, agreement bonus, diversification, budget, reranking |
| `test_editions_and_taxonomy.py` | 27 | Edition clause divergence, absent-clause quirks, document taxonomy |
| `test_chunking.py` | 18 | Clause-boundary integrity, provenance spans, embedding context |
| `test_retrieval_scope.py` | 16 | Mandatory edition, access control, narrowing-only semantics |

### Running the domain suite

```bash
cd backend
python -m venv .venv-domain
./.venv-domain/Scripts/python -m pip install pytest
./.venv-domain/Scripts/python -m pytest tests/domain -q
```

### Running the integration suite

```bash
docker compose run --rm backend pytest tests/ --ds=config.settings.test
```

## What the tests are for

Most of them encode a specific defect observed in the legacy prototype, so a
regression fails a test rather than reaching a user.

**Structural regression guards.** Some tests assert on code shape rather than
behaviour, where the defect was structural:

```python
def test_no_retrieval_entry_point_defaults_an_edition() -> None:
    """Regression guard for the prototype's `edition: str = "2017"`."""
    for name, obj in vars(scope_module).items():
        ...
        assert param.default is inspect.Parameter.empty
```

A future contributor cannot reintroduce a defaulted edition without this
failing. The prototype's bug was not a wrong value — it was that a default
existed at all, so the test asserts on the absence of a default.

**Tests against the real prototype.** `test_legacy_import.py` parses the actual
`../backend/fidic_1987_skeleton.py` and `db.json`. Those reads are read-only,
and one test asserts it:

```python
def test_import_reads_do_not_modify_the_legacy_tree() -> None:
    before = {p: p.stat().st_mtime_ns for p in (...)}
    for path in before:
        parse_skeleton_file(path)
    assert before == {p: p.stat().st_mtime_ns for p in before}
```

They skip cleanly when the legacy tree is absent.

**Safety properties, not just happy paths.** An executable renamed to `.pdf` is
rejected on content. A quotation that changes "28 days" to "42 days" fails
verification. A citation to a source that was not retrieved raises. Each is a
test, not a comment.

## Conventions

- One behaviour per test; the name states the behaviour.
- Where a test encodes a past defect, the docstring says which. `git blame`
  should not be needed to understand why a test exists.
- `pytest.mark.parametrize` for input tables; separate tests for separate
  behaviours.
- No mocking inside the domain suite. Domain code has no I/O to mock, which is
  the point of the layering.
- Fixtures are plain factory functions rather than pytest fixtures where the
  construction is trivial — a helper reads better than indirection.

## Planned coverage

Not yet written, and named here so their absence is not mistaken for coverage:

| Area | Type | Status |
| --- | --- | --- |
| API permission enforcement per endpoint | Integration | Awaits the API layer |
| Ingestion pipeline end-to-end with fixture PDFs | Integration | Awaits the pipeline |
| Hybrid retrieval against a seeded corpus | Integration | Awaits pgvector wiring |
| RAG evaluation harness (recall, MRR, citation accuracy, unsupported-claim rate) | Evaluation | Awaits retrieval; see `RAG.md` |
| Frontend component and E2E | Frontend | In progress |

The RAG evaluation harness is the significant one. Unit tests show the
retrieval *mechanism* is correct; they say nothing about whether it retrieves
the right clause for a real question. Until that harness exists and reports
numbers, no claim about retrieval quality is made anywhere in this
documentation.

## Constraints on this build host

| Constraint | Effect |
| --- | --- |
| Python 3.9 only; Django 5.2 needs 3.10+ | The integration suite has not been executed here. It is authored, not verified. |
| Docker not installed | The compose stack has never been started. |
| No GPU | Nothing model-dependent has been benchmarked. |

Everything above the domain suite is therefore **authored but unverified**, and
is described that way throughout. "It parses" is not "it runs".
