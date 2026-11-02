"""TOC-guided section recovery (Section_Recovery.md, tests 1-11).

The damaged fixtures are made from tests/fixtures/fx_03_modified.pdf with PyMuPDF and go
through the real loader: a "Section N." number is erased, and for a glued heading the
caption is moved onto the end of the previous line, as in the I-81 Regular draft
("...representative of such party.TIFIA Loan Amount.  The principal ...").
"""

import re
from pathlib import Path

import pymupdf
import pytest

from tests.helpers import FIXTURES, NON_PROJECT, ROOT, TEMPLATE, segment_pdf
from tifia_review.cli import structure_status
from tifia_review.loader import DocumentLoader
from tifia_review.models import BoundarySource, Section, StructureReport, TocEntry
from tifia_review.segment.recovery import HeadingRecovery
from tifia_review.segment.structure import StructureCheck
from tifia_review.segment.titles import TitleNormalizer
from tifia_review.segment.toc import TocParser
from tifia_review.segmenter import PREAMBLE_ID, PageMap, Segmenter, body_texts
from tifia_review.store import RunStore

CLEAN = FIXTURES / "fx_03_modified.pdf"
AGREEMENTS = ROOT / "data" / "agreements"
I81_BODY = AGREEMENTS / "tla_2022_ctb_i81_regular_draft_body.pdf"
HRTAC = AGREEMENTS / "tla_2025_hrtac_draft_body.pdf"
DRLE = AGREEMENTS / "downtown-redmond-link-extension-project-TIFA-loan-agreement-executed-9.10.21.pdf"
FIRST_BODY_PAGE = 5  # 0-based: fx_03's cover and TOC come before

needs_fixture = pytest.mark.skipif(not CLEAN.exists(), reason="fixture not present")


# --- building damaged fixtures -----------------------------------------------------------

def _heading_rect(doc, n: int) -> tuple[pymupdf.Page, pymupdf.Rect]:
    """The page and rect of the body's "Section n." heading (indented, after the TOC)."""
    for page in list(doc)[FIRST_BODY_PAGE:]:
        hits = [r for r in page.search_for(f"Section {n}.") if 100 < r.x0 < 115]
        if hits:
            return page, hits[0]
    raise AssertionError(f"Section {n}. not found")


def _lines(page) -> list[tuple[pymupdf.Rect, str]]:
    return [(pymupdf.Rect(line["bbox"]), "".join(s["text"] for s in line["spans"]))
            for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]


def _redact(page, rects):
    for r in rects:
        page.add_redact_annot(pymupdf.Rect(r.x0 - 1, r.y0 + 3, r.x1 + 1, r.y1 - 3))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def _ascii(text: str) -> str:
    return (text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
            .replace("\xa0", " "))


def damage(out: Path, erase=(), glue=(), drop_caption=()) -> Path:
    """A copy of fx_03 with "Section N." erased for every N in erase, glue and
    drop_caption; glue also moves the heading line onto the end of the line before it;
    drop_caption also erases the caption, so the section cannot be found by title."""
    doc = pymupdf.open(CLEAN)
    for n in erase:
        page, rect = _heading_rect(doc, n)
        _redact(page, [rect])
    for n, caption in drop_caption:
        page, rect = _heading_rect(doc, n)
        caps = [r for r in page.search_for(caption) if abs(r.y0 - rect.y0) < 4]
        _redact(page, [rect] + caps)
    for n in glue:
        page, rect = _heading_rect(doc, n)
        _glue(page, rect)
    doc.save(out)
    return out


def _glue(page, rect):
    """Re-typeset the page line by line (inserted text is extracted in insertion order, so
    the whole page is redone to keep reading order), dropping the "Section N." at rect
    and appending its caption line to the end of the line before it."""
    lines = [line for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", [])
             if "".join(s["text"] for s in line["spans"]).strip()]
    text = lambda line: _ascii("".join(s["text"] for s in line["spans"]))
    number = next(i for i, ln in enumerate(lines) if abs(ln["bbox"][1] - rect.y0) < 4
                  and ln["bbox"][0] < rect.x1 and text(ln).strip().startswith("Section"))
    head = next(i for i, ln in enumerate(lines) if abs(ln["bbox"][1] - rect.y0) < 4
                and ln["bbox"][0] > rect.x1)
    prev = max((i for i, ln in enumerate(lines) if ln["bbox"][3] <= rect.y0 + 2
                and ln["bbox"][0] < 100), key=lambda i: lines[i]["bbox"][3])
    page.add_redact_annot(page.rect)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for i, line in enumerate(lines):
        if i in (number, head):
            continue
        content = text(line).rstrip()
        if i == prev:
            content += text(lines[head]).strip()
        span = next(s for s in line["spans"] if s["text"].strip())
        font = "tibo" if span["flags"] & 16 else "tiit" if span["flags"] & 2 else "tiro"
        size = span["size"]
        x = line["bbox"][0]
        while i == prev and pymupdf.get_text_length(content, font, size) > 545 - x:
            size -= 0.25
        page.insert_text((x, span["origin"][1]), content, fontname=font, fontsize=size)


def segment(path: Path, recovery: dict | None = None, reference=None):
    doc = DocumentLoader().load(str(path), path.stem, "executed")
    return doc, Segmenter(recovery).segment(doc, reference)


def top(seg) -> dict[str, Section]:
    return {s.section_id: s for s in seg.sections if s.level == 1}


@pytest.fixture(scope="module")
def glued(tmp_path_factory):
    """Sections 3 and 6 glued to the line before; Section 7's number erased (its caption
    wraps onto a second line)."""
    path = damage(tmp_path_factory.mktemp("recovery") / "glued.pdf", erase=[7], glue=[3, 6])
    return path, *segment(path)


# --- 1. clean fixture ----------------------------------------------------------------------

@needs_fixture
def test_clean_fixture_is_not_recovered_and_unchanged():
    _, seg = segment(CLEAN)
    _, plain = segment(CLEAN, {"enabled": False})
    assert seg.structure.status == "complete" and seg.structure.recovered == []
    assert seg.sections == plain.sections and seg.defined_terms == plain.defined_terms
    assert all(s.boundary_source is BoundarySource.NUMBERED for s in seg.sections)


# --- 2, 6, 8, 9: damaged fixture -----------------------------------------------------------

@needs_fixture
def test_glued_headings_are_recovered_from_the_toc(glued):
    path, _, seg = glued
    sections = top(seg)
    assert seg.structure.status == "recovered"
    assert sorted(seg.structure.recovered, key=int) == ["3", "6", "7"]
    assert sections["3"].boundary_source is BoundarySource.TOC_TITLE
    assert sections["6"].boundary_source is BoundarySource.TOC_TITLE
    assert sections["3"].heading == "TIFIA Loan Amount"
    assert sections["3"].text.startswith("TIFIA Loan Amount.")
    assert sections["2"].text.endswith("representative of such party.")  # split mid-line
    assert sections["6"].text.startswith("Interest Rate.")
    assert sections["5"].text.endswith("irrevocably paid in full in cash.")
    # the same sections as the undamaged fixture, clauses included: clause parsing ran
    # again inside the recovered sections
    clean = [s.section_id for s in segment_pdf(CLEAN)[1].sections]
    assert [s.section_id for s in seg.sections] == clean


@needs_fixture
def test_wrapped_body_heading_is_found(glued):
    _, _, seg = glued
    s7 = top(seg)["7"]
    assert s7.boundary_source is BoundarySource.TOC_TITLE
    assert re.match(r"Outstanding TIFIA Loan Balance; Revisions to Exhibit G and Loan\s+"
                    r"Amortization Schedule\.", s7.text)


@needs_fixture
def test_text_coverage_after_recovery(glued):
    _, doc, seg = glued
    assert seg.structure.text_coverage >= 0.98
    # every non-space character of the body after the TOC (footnotes cut, as the
    # segmenter does) is in exactly one top-level section
    toc_end = TocParser().parse([p.text for p in doc.pages]).toc_end
    body = "".join(body_texts(doc)[0][toc_end:])
    in_sections = "".join(s.text for s in seg.sections if s.level == 1)
    chars = lambda t: sum(not c.isspace() for c in t)
    assert abs(chars(in_sections) - chars(body)) <= 0.02 * chars(body)


@needs_fixture
def test_recovered_sections_cite_the_page_their_title_is_on(glued):
    path, _, seg = glued
    pdf = pymupdf.open(path)
    sections = top(seg)
    for sid, title in (("3", "party.TIFIA Loan Amount."), ("6", "cash.Interest Rate.")):
        (page,) = [p.number + 1 for p in list(pdf)[FIRST_BODY_PAGE:] if p.search_for(title)]
        assert sections[sid].pdf_start == page, sid
    # and every recovered section cites the pages the undamaged fixture's section has
    clean = top(segment_pdf(CLEAN)[1])
    for sid in ("3", "6", "7"):
        got, want = sections[sid], clean[sid]
        assert (got.pdf_start, got.label_start) == (want.pdf_start, want.label_start), sid


# --- 3, 4: traps ---------------------------------------------------------------------------

def _recover(text: str, title: str, sid: str = "36"):
    return HeadingRecovery().recover(text, [TocEntry(sid, title)], {}, 0, len(text),
                                     BoundarySource.TOC_TITLE)


def test_cross_reference_is_not_a_heading():
    text = ("delivered in accordance with Section 36 (Notices; Payment Instructions). The "
            "Borrower shall pay.\nSection 35. Counterparts. This Agreement may be signed. "
            "Notices; Payment Instructions. Notices hereunder shall be in writing.")
    result = _recover(text, "Notices; Payment Instructions")
    assert [b.pos for b in result.boundaries] == [text.rindex("Notices; Payment Instructions.")]


def test_plain_phrase_is_not_a_heading():
    text = ("so that the TIFIA Loan Amount shall not exceed the cap, and so that a lower "
            "TIFIA Loan Amount. applies; at a Variable Interest Rate. The rest follows.\n"
            "such party.TIFIA Loan Amount.  The principal amount shall not exceed $1.")
    assert [b.pos for b in _recover(text, "TIFIA Loan Amount", "3").boundaries] == [
        text.index("TIFIA Loan Amount.  The principal")]
    assert _recover(text, "Interest Rate", "6").missing == ["6"]


@needs_fixture
def test_traps_in_the_real_text_are_skipped():
    """With nothing found and the whole body as the window, the first heading-form match of
    each title is still the real heading, not the cross-reference in Section 2."""
    doc = DocumentLoader().load(str(CLEAN), "clean", "executed")
    seg = segment_pdf(CLEAN)[1]
    sections = top(seg)
    text = PageMap(doc.pages).full_text
    start = PageMap(doc.pages).offsets[FIRST_BODY_PAGE]
    assert "Section 36 \n(Notices; Payment Instructions)" in sections["2"].text
    result = HeadingRecovery().recover(
        text, [TocEntry("36", "Notices; Payment Instructions")], {}, start, len(text),
        BoundarySource.TOC_TITLE)
    (b,) = result.boundaries
    assert text[b.pos:].startswith("Section 36.")
    assert b.source is BoundarySource.SECTION_NUMBER


# --- 5: TOC ------------------------------------------------------------------------------

@needs_fixture
def test_wrapped_toc_entry_is_one_title():
    doc = DocumentLoader().load(str(CLEAN), "clean", "executed")
    parsed = TocParser().parse([p.text for p in doc.pages], [p.pdf_index for p in doc.pages])
    by_id = {e.section_id: e for e in parsed.numbered}
    assert len(by_id) == 39 and 0 < parsed.toc_end <= FIRST_BODY_PAGE
    assert by_id["7"].title == ("Outstanding TIFIA Loan Balance; Revisions to Exhibit G and "
                                "Loan Amortization Schedule")
    assert by_id["7"].toc_page in range(1, FIRST_BODY_PAGE + 1)


def test_title_normalizer_keeps_offsets():
    text = "Borrower’s  Authorized\nRepresentative — Notices"
    norm = TitleNormalizer().normalize(text)
    assert norm.text == "Borrower's Authorized Representative - Notices"
    pos = norm.text.index("Representative")
    assert text[norm.original(pos):].startswith("Representative")
    assert norm.newline[norm.text.index(" Representative")]
    assert TitleNormalizer().title("Reports and\nRecords.") == "Reports and Records"


# --- 7: title absent ---------------------------------------------------------------------

@needs_fixture
def test_absent_title_is_missing_and_degraded(tmp_path):
    path = damage(tmp_path / "absent.pdf", drop_caption=[(24, "No Third Party Rights.")])
    _, seg = segment(path)
    st = seg.structure
    assert st.status == "degraded" and st.missing == ["24"] and st.found == 38
    sections = top(seg)
    assert "24" not in sections
    assert "hereby agree that this Agreement" in sections["23"].text  # nothing lost
    assert st.text_coverage >= 0.98
    assert st.summary_line("x").endswith("[DEGRADED]")


def test_structure_check_flags_order_and_duplicates():
    secs = [Section("1", "A", "one", 1, 1), Section("3", "C", "three", 1, 1),
            Section("2", "B", "two", 1, 1), Section("2", "B", "again", 1, 1)]
    st = StructureCheck().check(secs, [TocEntry(i, "") for i in "123"], "toc",
                                "one three two again", recovered=["3"])
    assert st.status == "degraded"
    assert any("duplicate" in p for p in st.problems)
    assert any("out of order" in p for p in st.problems)


def test_template_titles_are_the_fallback_without_a_toc():
    seg = Segmenter()
    toc = [TocEntry(str(i), f"T{i}", "1") for i in range(1, 5)]
    ref = [TocEntry(str(i), f"R{i}") for i in range(1, 11)]
    assert seg._expected(toc, ref) == (ref, "template")      # 4 < 80% of 10
    assert seg._expected(toc * 3, ref)[1] == "toc"
    assert seg._expected([], ref) == (ref, "template")
    assert seg._expected([], None) == ([], "none")


def test_evaluator_skips_degraded_runs(tmp_path):
    store = RunStore(tmp_path, "run/doc")
    assert structure_status(store) is None
    report = StructureReport(39, 38, [], ["24"], 1.0, "degraded")
    store.save("structure", {"executed": report, "template": None})
    assert structure_status(store) == "degraded"


# --- 10, 11: real documents ----------------------------------------------------------------

@pytest.mark.skipif(not I81_BODY.exists(), reason="I-81 Regular draft not present")
def test_i81_regular_body_recovers_all_39_sections():
    doc, seg = segment(I81_BODY)
    parsed = TocParser().parse([p.text for p in doc.pages])
    toc_ids = [e.section_id for e in parsed.numbered]
    numbered = [s for s in seg.sections if s.level == 1 and s.section_id.isdigit()]
    assert toc_ids == [str(i) for i in range(1, 40)]
    assert [s.section_id for s in numbered] == toc_ids
    st = seg.structure
    assert st.status == "recovered" and st.found == st.expected == 39 and not st.missing
    assert st.numbered == 13 and len(st.recovered) == 26 and st.text_coverage >= 0.98
    sections = top(seg)
    assert sections["38"].text.startswith("Termination.")
    assert sections["37"].text.endswith("Date.")  # "...the Effective Date.Termination."
    assert sections["1"].heading == "Definitions" and len(seg.defined_terms) > 150
    assert sections[PREAMBLE_ID].pdf_end <= sections["1"].pdf_start


@pytest.mark.parametrize("path", [TEMPLATE, NON_PROJECT, HRTAC, DRLE],
                         ids=["template", "non_project", "hrtac", "drle"])
def test_clean_documents_segment_exactly_as_before(path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    _, seg = segment(path)
    _, plain = segment(path, {"enabled": False})
    assert seg.structure.status == "complete" and seg.structure.found == 39
    assert seg.sections == plain.sections and seg.defined_terms == plain.defined_terms
