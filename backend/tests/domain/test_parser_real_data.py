from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from claimiq.ingestion.domain import chunking as ck
from claimiq.ingestion.domain import clause_detection as cd

pytestmark = pytest.mark.domain

SRC = os.environ.get("CLAIMIQ_PAGES_JSON")
BASE = Path(__file__).parent / "data" / "parser_baseline.json"

if not SRC or not Path(SRC).is_file():
    pytest.skip("set CLAIMIQ_PAGES_JSON to a page export", allow_module_level=True)

DOCS = json.loads(Path(SRC).read_text(encoding="utf-8"))
TITLES = [d["title"] for d in DOCS]
BY = {d["title"]: d for d in DOCS}
BL = json.loads(BASE.read_text(encoding="utf-8")) if BASE.is_file() else {}

KEYS = {
    "Yellow Book 2017": [
        "1.9", "4.12.4", "8.6", "13.3.1", "19.1", "19.2", "20.1", "20.2", "20.2.1", "20.2.2", "20.2.5",
    ],
    "Silver Book 1999": [
        "1.1", "1.9", "2.1", "4.12", "8.4", "8.5", "13.1", "13.7", "14.1", "17.4", "19.1", "19.2",
        "19.4", "20.1", "20.2", "20.3", "20.4", "20.5", "20.6",
    ],
    "Red Book 1987": [
        "12.2", "44.1", "44.2", "51.1", "52.1", "53.1", "53.2", "53.3", "53.4", "53.5", "60.1",
        "67.1", "67.2", "67.3", "70.1",
    ],
}

TARGET = {"junk": 0.05, "tiny": 0.10, "cont": 0.10, "mid": 0.05, "over": 0.0}
SLACK = 0.01
NUM = re.compile(r"\d[\d,.]*")
UNITS = re.compile(r"\b(?:Cum|Sqm|Sm|Rm|Nos|Kg|Lm|Ltr)\b")


def junk(t):
    ns = [x for x in NUM.findall(t) if len(re.sub(r"\D", "", x)) >= 2]
    return len(ns) >= 2 or bool(re.search(r"\d{1,3},\d{3}", t)) or bool(UNITS.search(t))


def words(t):
    return len(re.findall(r"[A-Za-z]{3,}", t))


def pipe(d):
    pages = [cd.PageText(page_number=p["n"], text=p["text"]) for p in d["pages"]]
    keep = [p for p in pages if cd.classify_page(p)]
    hs, _ = cd.detect_headings(keep)
    return pages, keep, hs, ck.chunk_document(keep, hs)


def measure(d):
    pages, keep, hs, cs = pipe(d)
    n = max(len(cs), 1)
    tw = sum(words(p.text) for p in pages) or 1
    kw = sum(words(p.text) for p in keep)
    prev = None
    cont = 0
    for c in cs:
        if (
            prev
            and c.clause_number
            and c.clause_number == prev.clause_number
            and c.spans[0].page_number != prev.spans[0].page_number
        ):
            cont += 1
        prev = c
    have = {c.clause_number for c in cs if c.clause_number}
    key = next((v for k, v in KEYS.items() if k in d["title"]), [])
    return {
        "dropped_words": round(1 - kw / tw, 4),
        "junk": round(sum(1 for h in hs if junk(h.title)) / max(len(hs), 1), 4),
        "tiny": round(sum(1 for c in cs if c.clause_number and len(c.text) < 60) / n, 4),
        "cont": round(cont / n, 4),
        "mid": round(sum(1 for c in cs if c.text[:1].islower()) / n, 4),
        "over": round(sum(1 for c in cs if len(c.text) > ck.DEFAULT_MAX_CHARS) / n, 4),
        "missing": sorted(k for k in key if k not in have),
    }


@pytest.fixture(scope="module")
def M():
    return {t: measure(BY[t]) for t in TITLES}


@pytest.mark.skipif(not os.environ.get("CLAIMIQ_WRITE_BASELINE"), reason="baseline write is opt-in")
def test_write_baseline(M):
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(json.dumps(M, indent=2, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize("t", TITLES)
def test_pipeline_is_deterministic(t):
    a = [(c.text, c.spans, c.clause_number) for c in pipe(BY[t])[3]]
    b = [(c.text, c.spans, c.clause_number) for c in pipe(BY[t])[3]]
    assert a == b


@pytest.mark.parametrize("t", TITLES)
def test_metrics_do_not_regress(t, M):
    if t not in BL:
        pytest.skip("no baseline for this document")
    for k in ("dropped_words", "junk", "tiny", "cont", "mid", "over"):
        assert M[t][k] <= BL[t][k] + SLACK, k
    assert set(M[t]["missing"]) <= set(BL[t]["missing"])


@pytest.mark.parametrize("m", sorted(TARGET))
@pytest.mark.parametrize("t", TITLES)
def test_metric_meets_target(t, m, M, request):
    if BL.get(t, {}).get(m, 0) > TARGET[m]:
        request.applymarker(pytest.mark.xfail(strict=True, reason="baseline is above target"))
    assert M[t][m] <= TARGET[m]


@pytest.mark.parametrize("t", [x for x in TITLES if any(k in x for k in KEYS)])
def test_key_claim_clauses_are_present(t, M, request):
    if BL.get(t, {}).get("missing"):
        request.applymarker(pytest.mark.xfail(strict=True, reason="baseline is missing key clauses"))
    assert M[t]["missing"] == []
