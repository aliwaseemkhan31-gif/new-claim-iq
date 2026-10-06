from __future__ import annotations

import random
import re

import pytest

from claimiq.core.domain.errors import UnsupportedMediaTypeError
from claimiq.ingestion.domain import chunking as ck
from claimiq.ingestion.domain import clause_detection as cd
from claimiq.ingestion.domain import file_safety as fs

pytestmark = pytest.mark.domain


def fault(why):
    return pytest.mark.xfail(strict=True, reason=why)


def case(id_, *vals, why=None):
    return pytest.param(*vals, id=id_, marks=fault(why) if why else ())


def pg(n, t):
    return cd.PageText(page_number=n, text=t)


def run(*texts, cfg=None):
    pages = [pg(i, t) for i, t in enumerate(texts, 1)]
    keep = [p for p in pages if cd.classify_page(p)]
    hs, _ = cd.detect_headings(keep)
    return ck.chunk_document(keep, hs, cfg)


def heads(t):
    hs, _ = cd.detect_headings([pg(1, t)])
    return hs


def nums(t):
    return [h.number for h in heads(t)]


def flat(t):
    return re.sub(r"\s+", " ", t)


def sents(n, k):
    return " ".join(
        f"Sentence {i} of block {k} states that the Contractor shall comply with the requirements."
        for i in range(n)
    )


def toc(n):
    return "\n".join(f"{i}.1 Clause Title Number {i} {'.' * 20} {i + 4}" for i in range(1, n + 1))


BODY = "The Contractor shall give notice to the Engineer within 28 days after becoming aware of the event."

BOQ = "\n".join(
    f"{i} {w} Cum {i * 37}.5 {i * 11}.25 {i * 4001}"
    for i, w in enumerate(
        ["Excavation", "Embankment", "Aggregate Base", "Prime Coat", "Concrete Class A", "Reinforcement"], 1
    )
)

KEEP = [
    case(
        "gc_running_header",
        "General Conditions © FIDIC 2017\n4.12.2 Engineer’s inspection and investigation\n"
        "The Engineer shall inspect and investigate the physical conditions within 7 days after receiving the Notice.",
    ),
    case(
        "gc_title_then_clause_one",
        "General Conditions\n1\nGeneral Provisions\n1.1 Definitions\n"
        "In the Conditions of Contract the following words and expressions shall have the meanings stated.",
    ),
    case(
        "annex_with_evidence",
        "Annex A\nSchedule of Delay Events\nThe delay caused by the landslide of 12 July 2022 affected Section 3 of the Works for 41 days.",
    ),
    case(
        "appendix_site_records",
        "Appendix 2 Daily Site Records\nDate: 12 July 2022. Rain 120mm. No work possible on the embankment.",
    ),
    case(
        "contents_of_the_notice",
        "Contents of the Notice\nThe notice shall describe the event and state the contractual basis of the claim.",
    ),
    case(
        "index_linked_adjustment",
        "Index Linked Adjustment\nPrices shall be adjusted using the index published by the Bureau of Statistics.",
    ),
    case(
        "contents_colon_sentence",
        "Contents: the Contractor shall submit the list of materials.\nMore provision text follows on this page.",
    ),
    case(
        "table_with_trailing_integers",
        "\n".join(f"{i} Item description number {i}   {i * 3}" for i in range(1, 15)),
    ),
    case(
        "schedule_with_trailing_integers",
        "\n".join(f"Section {i} Completion Date   {i + 10}" for i in range(1, 10)),
    ),
    case(
        "claim_letter_annex_reference",
        "Annex 1 - Letter dated 12 March 2021 from Contractor to Engineer regarding extension of time.\nDear Sir we write to notify.",
    ),
    case("annexure_style", "ANNEXURE B  Cost Claim Summary\nTotal cost of damages PKR 91,484,000"),
    case("plain_clause_page", "20.1 Claims\n" + BODY),
]

DROP = [
    case("dot_leader_toc", toc(12)),
    case("toc_without_leaders", "\n".join(f"{i} Some General Title {i}   {i * 2}" for i in range(1, 12))),
    case("contents_header_list", "contents\n" + "\n".join(f"{i}.1 Title {i}" for i in range(1, 10))),
    case("toc_single_space_page_number", "\n".join(f"{i}.2 Contractor's Obligations {i + 7}" for i in range(1, 12))),
    case("index_of_sub_clauses", "INDEX OF SUB-CLAUSES\nSub-Clause Page\n1.1 Definitions 8\n1.2 Interpretation 9"),
]


@pytest.mark.parametrize("t", KEEP)
def test_body_page_is_kept(t):
    assert cd.classify_page(pg(1, t))


@pytest.mark.parametrize("t", DROP)
def test_contents_page_is_dropped(t):
    assert not cd.classify_page(pg(1, t))


HEAD_OK = [
    case("plain", "20.2.1 Notice of Claim", "20.2.1"),
    case("integer", "20 Claims, Disputes and Arbitration", "20"),
    case("dot_after", "20. Claims Disputes And Arbitration", "20"),
    case("paren_after", "20) Claims And Disputes", "20"),
    case("keyword", "Clause 20 - Claims", "20"),
    case("article", "Article 5 Payment", "5"),
    case("tab", "20.2.1\tNotice of Claim", "20.2.1"),
    case("en_dash", "20.2.1 – Notice of Claim", "20.2.1"),
    case("all_caps_title", "20.2.1 NOTICE OF CLAIM", "20.2.1"),
    case("leading_zero", "08.02 Time for Completion", "8.2"),
    case("trailing_colon", "20.2.1 Notice of Claim:", "20.2.1"),
    case("five_levels", "20.2.1.4.5 Deep Heading", "20.2.1.4.5"),
]

HEAD_MISS = [
    case("caps_clause", "CLAUSE 20 CLAIMS", "20", why="keyword match is case sensitive"),
    case("caps_clause_colon", "CLAUSE 20: DEFINITIONS", "20", why="keyword match is case sensitive"),
    case("caps_subclause", "SUB-CLAUSE 20.1 CLAIMS", "20.1", why="keyword match is case sensitive"),
    case("article_roman", "ARTICLE IV PAYMENT", None, why="roman numerals are not supported"),
    case("letter_number", "A.1 General Requirements", "A.1", why="lettered numbering is not supported"),
    case("lowercase_title", "5.1 notice of claim", "5.1", why="title must start with a capital"),
    case("digit_title", "4.1 2017 Edition Amendments", "4.1", why="title must start with a capital letter"),
    case("ocr_no_spaces", "20.2.1NoticeofClaim", "20.2.1", why="OCR dropped the spaces"),
    case("ocr_letter_o_for_zero", "2O.2.1 Notice of Claim", "20.2.1", why="OCR confused O with 0"),
    case("ocr_letter_l_for_one", "l4.1 Payment Terms", "14.1", why="OCR confused l with 1"),
    case("non_breaking_space", "20.2.1\u00a0Notice of Claim", "20.2.1", why="separator class excludes U+00A0"),
    case(
        "number_alone_on_line",
        "20.1\nClaims\nA Claim may arise:\n(a) if the Employer considers that the Employer is entitled",
        "20.1",
        why="number and title on separate lines (FIDIC 2017 and Silver 1999 layout)",
    ),
    case(
        "margin_title_ends_lowercase_of",
        "Valuation of 52.1 All variations referred to in Clause 51 and any additions to the Contract Price",
        "52.1",
        why="margin title ending in a connective scores below the threshold",
    ),
    case(
        "margin_title_ends_lowercase_or",
        "Increase or 70.1 There shall be added to or deducted from the Contract Price such sums",
        "70.1",
        why="margin title ending in a connective scores below the threshold",
    ),
]

HEAD_FP = [
    case("date_with_word", "1 January 2023 Notice", why="any number plus capitalised words is a heading"),
    case("street_address", "12 Main Street Karachi", why="any number plus capitalised words is a heading"),
    case("boq_row_decimals", "2 Prime Coat Sm 4233.60 12.5 52920"),
    case("boq_row_thousands", "7 Concrete Class A Cum 120 15000 1,800,000"),
    case("boq_row_text", "3 Excavation In Rock Cum", why="table row accepted"),
    case("quantity_line", "4 Nos Culverts Installed", why="table row accepted"),
    case("amount_line", "5 Million Rupees Only", why="amount accepted"),
    case("table_header", "1 Description Of Item Unit Quantity", why="table header accepted"),
    case("phone_prefix", "92 Pakistan Telecom Limited", why="any number plus capitalised words is a heading"),
    case("speed_spec", "100 Mbps Internet Line Item", why="any number plus capitalised words is a heading"),
    case("boq_block", BOQ),
    case("date_only", "15 March 2021"),
    case("page_footer", "Page 12 of 40"),
    case("numbered_sentence", "1. We refer to your letter dated 12 March 2021 regarding the delay."),
    case("year_prefix", "2021 Annual Report"),
]


@pytest.mark.parametrize("t,n", HEAD_OK)
def test_valid_heading_is_detected(t, n):
    assert nums(t) == [n]


@pytest.mark.parametrize("t,n", HEAD_MISS)
def test_known_heading_layout_is_detected(t, n):
    got = nums(t)
    assert len(got) == 1
    assert n is None or got[0] == n


@pytest.mark.parametrize("t", HEAD_FP)
def test_non_heading_line_is_rejected(t):
    assert nums(t) == []


def test_table_block_yields_no_complete_clause_chunks():
    assert not any(c.is_complete_clause for c in run(BOQ))


@pytest.mark.parametrize(
    "raw,want", [("20.2.1.", "20.2.1"), ("08.02", "8.2"), ("1.10", "1.10"), ("007", "7"), ("5)", "5")]
)
def test_clause_number_normalisation(raw, want):
    assert cd.normalise_clause_number(raw) == want


@pytest.mark.parametrize(
    "nl",
    [
        "\n",
        case("crlf", "\r\n"),
        "\u2028",
        "\x0b",
        "\x0c",
    ],
)
def test_heading_offset_points_at_its_number(nl):
    t = nl.join(["20.1 Claims", BODY, "20.2 Notice of Claim", BODY, "20.3 Evidence", BODY])
    hs = heads(t)
    assert len(hs) == 3
    for h in hs:
        assert t[h.char_offset :].startswith(h.number)


def test_chunk_span_slices_raw_page_text():
    raw = (
        "20.1 Claims\n"
        + "The Contractor shall   give notice.   " * 3
        + "\n\n20.2 Notice of Claim\nThe  Contractor shall   give notice  within 28 days.\n"
    )
    cs = run(raw)
    assert cs
    for c in cs:
        s = c.spans[0]
        assert ck._normalise_whitespace(raw[s.start_offset : s.end_offset]) == c.text


def test_split_chunks_have_distinct_spans():
    cs = run("5.1 Payment\n" + "\n\n".join(sents(8, i) for i in range(6)))
    assert len(cs) > 1
    assert len({c.spans for c in cs}) == len(cs)


P1 = (
    "19 Force Majeure\n19.1 Definition\n"
    + "Force Majeure means an exceptional event or circumstance which is beyond a Party's control. " * 8
    + "The Party affected shall give notice within 14 days of"
)
P2 = "becoming aware of the event. Failure to notify shall bar the claim.\n19.2 Notice of Force Majeure\nThe Party shall give notice."


def test_sentence_across_page_break_stays_in_one_chunk():
    assert any("within 14 days of becoming aware" in flat(c.text) for c in run(P1, P2))


def test_no_chunk_starts_mid_sentence_after_page_break():
    assert not any(c.text[:1].islower() for c in run(P1, P2))


@fault("a page-end fragment is flagged as a complete clause")
def test_fragment_is_not_flagged_complete():
    for c in run(P1, P2):
        assert not c.is_complete_clause or c.text.rstrip()[-1] in ".:;"


def test_continuation_text_keeps_page_provenance():
    cs = run(P1, P2)
    c = next(c for c in cs if "becoming aware" in c.text)
    assert any(s.page_number == 2 for s in c.spans)


def test_heading_at_page_foot_is_not_a_standalone_chunk():
    cs = run(
        "Earlier text of the previous clause about payments to the Contractor.\n14.7 Payment\n",
        "The Employer shall pay the Contractor the amounts certified within 56 days after the Engineer receives the Statement.",
    )
    assert all(len(c.text) >= 60 for c in cs if c.clause_number)


def test_chunking_is_deterministic():
    a = run(P1, P2)
    b = run(P1, P2)
    assert [(c.text, c.spans, c.clause_number) for c in a] == [(c.text, c.spans, c.clause_number) for c in b]


def test_sequence_is_contiguous():
    cs = run("1.1 One\n" + sents(40, "a"), "1.2 Two\n" + sents(40, "b"))
    assert [c.sequence for c in cs] == list(range(len(cs)))


def test_no_sentence_is_lost():
    r = random.Random(1)
    for k in range(60):
        body = "5.1 Payment\n" + "\n\n".join(
            sents(r.randint(1, 6), f"{k}.{i}") for i in range(r.randint(1, 30))
        )
        out = flat(" ".join(c.text for c in run(body)))
        for s in re.split(r"(?<=\.)\s+", flat(body)):
            assert s in out


def test_overlap_never_crosses_clauses():
    cs = run(f"6.1 First\n{sents(40, 'AAA')}\n6.2 Second\n{sents(40, 'BBB')}")
    assert all("block AAA" not in c.text for c in cs if c.clause_number == "6.2")


def test_text_before_first_heading_is_kept_without_a_clause():
    cs = run("AGREEMENT made between the Employer and the Contractor on this day for the Works described.\n1 Definitions\nIn this Agreement words have meanings.")
    assert cs[0].clause_number is None
    assert not cs[0].is_complete_clause


def test_empty_pages_produce_no_chunks():
    assert run("   \n\n", "") == []


def test_unbroken_text_terminates_and_is_fully_covered():
    cs = run("3.1 Test\n" + "A" * 6000)
    assert sum(c.text.count("A") for c in cs) >= 6000


@fault("an unbroken paragraph is split inside its own heading")
def test_heading_is_not_detached_from_its_body():
    assert run("3.1 Test\n" + "A" * 6000)[0].text.startswith("3.1 Test")


@fault("overlap pushes some chunks past max_chars")
def test_no_chunk_exceeds_max_chars():
    r = random.Random(7)
    mx = ck.ChunkingConfig().max_chars
    for _ in range(200):
        body = "7.1 Variations\n" + "\n\n".join(
            "X" * r.randint(900, 1990) + " end of paragraph." for _ in range(r.randint(2, 6))
        )
        assert all(len(c.text) <= mx for c in run(body))


def hd(n, t):
    return cd.ClauseHeading(
        number=n, title=t, page_number=1, line_index=0, char_offset=0, confidence=0.9, depth=cd.clause_depth(n)
    )


def test_tree_orders_clauses_numerically():
    tree = cd.build_hierarchy([hd("1.10", "Ten"), hd("1.2", "Two"), hd("1", "One")])
    assert [c.number for c in tree[0].children] == ["1.2", "1.10"]


def test_tree_collapses_duplicate_numbers():
    tree = cd.build_hierarchy([hd("1", "One"), hd("1.2", "Two"), hd("1.2", "Two again")])
    assert len(tree[0].children) == 1


def test_tree_keeps_orphans_as_roots():
    tree = cd.build_hierarchy([hd("1", "One"), hd("3.4.5", "Orphan")])
    assert [n.number for n in tree] == ["1", "3.4.5"]


ALLOWED = frozenset({"application/pdf", "image/png"})
PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n"


def rep(name="a.pdf", ct="application/pdf", size=1000, head=PDF, mx=100 * 1024 * 1024):
    return fs.validate_upload(
        filename=name, declared_content_type=ct, size_bytes=size, header=head, allowed_types=ALLOWED, max_bytes=mx
    )


def test_clean_pdf_is_accepted():
    r = rep()
    assert not r.is_rejected
    assert not r.is_suspicious


@pytest.mark.parametrize(
    "kw",
    [
        {"head": b"MZ\x90\x00" + b"\x00" * 100},
        {"head": b"<html><script>alert(1)</script>"},
        {"ct": "application/pdf", "head": b"\x89PNG\r\n\x1a\n" + b"\x00" * 50},
        {"size": 200 * 1024 * 1024},
        {"size": 0, "head": b""},
    ],
    ids=["exe_as_pdf", "html_as_pdf", "png_declared_pdf", "oversize", "empty"],
)
def test_hostile_upload_is_rejected(kw):
    assert rep(**kw).is_rejected


@pytest.mark.parametrize(
    "tail,size",
    [(b"/JS (x) /JavaScript", 1000), (b"/OpenAction /Launch", 1000), (b"A" * 70000 + b"/JavaScript", 80000)],
    ids=["javascript", "launch_action", "javascript_past_header_window"],
)
def test_active_content_pdf_is_flagged_not_rejected(tail, size):
    r = rep(head=PDF + tail, size=size)
    assert r.is_suspicious
    assert not r.is_rejected


def test_rejection_raises_typed_error():
    with pytest.raises(UnsupportedMediaTypeError):
        fs.enforce_safety(rep(head=b"<html>x</html>"))


@pytest.mark.parametrize(
    "raw,want",
    [("../../etc/passwd", "passwd"), ("C:\\x\\a.pdf", "a.pdf"), ("a\x00.pdf", "a.pdf"), ("..", "unnamed"), (".hidden.pdf", "hidden.pdf")],
)
def test_filename_is_sanitised(raw, want):
    assert fs.sanitise_filename(raw) == want


def bx(x0, y0, x1, y1, t):
    return ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], t, 0.97)


def test_ocr_skewed_page_keeps_lines_whole():
    from claimiq.ingestion.domain.layout import assemble_ocr_lines

    boxes = []
    for i in range(6):
        y = 100 + i * 20
        boxes += [
            bx(50, y + 6, 200, y + 21, f"l{i}a"),
            bx(220, y + 3, 370, y + 18, f"l{i}b"),
            bx(390, y, 540, y + 15, f"l{i}c"),
        ]
    out = assemble_ocr_lines(boxes)[0].splitlines()
    assert out == [f"l{i}a l{i}b l{i}c" for i in range(6)]


@fault("columns are interleaved line by line")
def test_ocr_two_columns_are_read_one_after_the_other():
    from claimiq.ingestion.domain.layout import assemble_ocr_lines

    boxes = [
        bx(50, 100, 290, 115, "The Contractor shall give notice"),
        bx(320, 100, 560, 115, "20.2 Notice of Claim"),
        bx(50, 120, 290, 135, "within 28 days after becoming aware"),
        bx(320, 120, 560, 135, "The Employer shall give notice"),
        bx(50, 140, 290, 155, "of the event or circumstance."),
        bx(320, 140, 560, 155, "as soon as practicable."),
    ]
    t = assemble_ocr_lines(boxes)[0]
    assert t.index("of the event or circumstance.") < t.index("20.2 Notice of Claim")


@pytest.fixture(scope="module")
def pdf_pages(tmp_path_factory):
    pytest.importorskip("pdfplumber")
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    from reportlab.lib.pagesizes import A4

    w, h = A4
    path = tmp_path_factory.mktemp("pdf") / "t.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)

    def furniture(n):
        c.setFont("Helvetica", 8)
        c.drawString(50, h - 30, "General Conditions (c) ACME 2024 - Confidential")
        c.drawString(w / 2 - 20, 25, f"Page {n} of 40")

    furniture(1)
    c.saveState()
    c.translate(25, h / 2)
    c.rotate(90)
    c.setFont("Helvetica", 9)
    c.drawString(0, 0, "GENERAL FORMS CONDITIONS")
    c.restoreState()
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, h - 70, "20.1 Claims")
    c.setFont("Helvetica", 10)
    left = [
        "The Contractor shall give notice to the Engineer",
        "within 28 days after the Contractor became aware",
        "of the event. If the Contractor fails to give no-",
        "tice within this period, the time for completion",
    ]
    right = [
        "20.2 Notice of Claim",
        "The Employer shall give notice to the Contractor",
        "as soon as practicable and in any event within 28",
        "days after the Employer became aware of the event.",
    ]
    for col, x in ((left, 50), (right, 310)):
        y = h - 100
        for line in col:
            c.drawString(x, y, line)
            y -= 14
    c.showPage()
    furniture(2)
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 70, "shall not be extended and the Contractor shall not be entitled to additional payment.")
    c.drawString(50, h - 90, "20.3 Evidence")
    rows = [["Item", "Description", "Unit", "Qty", "Rate"], ["1", "Excavation", "Cum", "120", "450.00"], ["2", "Prime Coat", "Sm", "4233", "12.50"]]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            c.rect(50 + ci * 90, h - 150 - ri * 20, 90, 20)
            c.drawString(54 + ci * 90, h - 144 - ri * 20, cell)
    c.save()
    from claimiq.ingestion.providers.extraction import PdfPlumberExtractor

    return [p.text for p in PdfPlumberExtractor().extract(str(path))]


def test_pdf_text_layer_is_extracted(pdf_pages):
    assert "20.1 Claims" in pdf_pages[0]
    assert "20.3 Evidence" in pdf_pages[1]


@fault("rotated margin text is emitted reversed")
def test_pdf_rotated_sidebar_is_not_emitted_reversed(pdf_pages):
    assert "SNOITIDNOC" not in pdf_pages[0]


@fault("running header and footer are left in the page text")
def test_pdf_running_header_and_footer_are_removed(pdf_pages):
    for t in pdf_pages:
        assert "Confidential" not in t
        assert not re.search(r"Page \d+ of 40", t)


@fault("two columns are interleaved line by line")
def test_pdf_columns_are_read_one_after_the_other(pdf_pages):
    t = pdf_pages[0]
    assert t.index("the time for completion") < t.index("20.2 Notice of Claim")


@fault("hyphenated line breaks are not rejoined")
def test_pdf_hyphenated_word_is_rejoined(pdf_pages):
    assert "notice within this period" in flat(pdf_pages[0])


def test_pdf_table_rows_are_not_headings(pdf_pages):
    assert nums(pdf_pages[1]) == ["20.3"]
