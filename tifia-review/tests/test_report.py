import importlib.util
import sys

import pytest

from tifia_review.classifier import FILL_IN_CATEGORY, NOT_CLASSIFIED
from tifia_review.models import (
    AlignMethod, Alignment, ChangeType, Deviation, Document, LabeledDeviation, Page, Section,
    SegmentedDocument,
)
from tifia_review.report import builder
from tifia_review.report.builder import (
    build_report_data, change_snippet, find_browser, render_html, render_pdf, word_diff,
)

MODIFIED, ADDED = ChangeType.MODIFIED, ChangeType.ADDED
OLD = "The Borrower shall give notice within thirty (30) days."
NEW = "The Borrower shall give notice within ten (10) days."


def has_renderer() -> bool:
    try:
        return importlib.util.find_spec("weasyprint") is not None or find_browser() is not None
    except ValueError:
        return find_browser() is not None


# --- word diff -------------------------------------------------------------------------

def test_word_diff_marks_changed_words_only():
    template, executed = word_diff(OLD, NEW)
    assert template == "The Borrower shall give notice within <del>thirty (30)</del> days."
    assert executed == "The Borrower shall give notice within <ins>ten (10)</ins> days."


def test_word_diff_ignores_quote_styles_and_escapes_html():
    template, executed = word_diff("the \u201cAgreement\u201d & <b>", 'the "Agreement" & <i>')
    assert "<del>" not in template.replace("<del>&lt;b&gt;</del>", "")
    assert template.endswith("<del>&lt;b&gt;</del>") and executed.endswith("<ins>&lt;i&gt;</ins>")
    assert "&amp;" in template


def test_change_snippet_shows_context_and_counts_the_rest():
    snippet = change_snippet(OLD, NEW)
    assert snippet == ("… shall give notice within <del>thirty (30)</del> <ins>ten (10)</ins> "
                       "days. …")
    many = change_snippet("a b c d e f g h i j k l", "a X c d e Y g h i Z k l")
    assert many.endswith("(1 more changes)")


# --- report content --------------------------------------------------------------------

def _docs():
    template_doc = Document("t", "template", [Page(n, str(n - 3) if n > 3 else None, "")
                                              for n in range(1, 11)])
    executed_doc = Document("e", "executed", [Page(n, str(n), "", needs_review=n == 9)
                                              for n in range(1, 11)])
    sections = [Section("1", "Definitions", "", 4, 4), Section("15", "Covenants", "", 6, 6),
                Section("15(g)", "Notice", "", 6, 6, level=2, parent_id="15"),
                Section("15(g)(i)", "", "", 6, 6, level=3, parent_id="15(g)")]
    added = Section("16", "Reporting", "", 8, 8)
    return (template_doc, executed_doc, SegmentedDocument("t", sections, {}),
            SegmentedDocument("e", sections + [added], {}))


def _dev(n, change=MODIFIED, t_sec="15(g)(i)", e_sec="15(g)(i)", term=None, old=OLD, new=NEW):
    return Deviation(f"d{n:04d}", change, t_sec, e_sec, old if t_sec else "",
                     new if e_sec else "", 6 if t_sec else None, 6 if e_sec else None, term)


def _labeled():
    return [
        LabeledDeviation(_dev(1, term="Act", t_sec="1", e_sec="1"), "Definitions", True, "x"),
        LabeledDeviation(_dev(2), "Covenants", True, "Notice period shortened.",
                         evidence="ten (10) days"),
        LabeledDeviation(_dev(3), "Covenants", False, "Wording only."),
        LabeledDeviation(_dev(4, ChangeType.FILL_IN, old="[Borrower]", new="City"),
                         FILL_IN_CATEGORY, False, "Filled in."),
        LabeledDeviation(_dev(5, ADDED, t_sec=None, e_sec="16", new="word " * 300), "Other",
                         True, "New reporting duty."),
        LabeledDeviation(_dev(6), "Covenants", True, "?", needs_review=True,
                         review_reason="evidence is not copied word for word"),
    ]


def _data(labeled):
    template_doc, executed_doc, template, executed = _docs()
    alignments = [Alignment(template.sections[0], executed.sections[0], AlignMethod.NUMBER, 1)]
    return build_report_data(labeled, template_doc, executed_doc, template, executed,
                             alignments, ["Definitions", "Covenants", "Other"],
                             {"agreement_id": "e", "template_id": "t", "run_id": "r"})


def test_each_deviation_lands_in_one_part():
    data = _data(_labeled())
    assert [e.d.deviation_id for e in data.definitions] == ["d0001"]
    assert [(g.name, [e.d.deviation_id for e in g.entries]) for g in data.groups] == [
        ("Covenants", ["d0002"])]
    assert [e.d.deviation_id for e in data.non_substantive] == ["d0003", "d0004"]
    assert [e.d.deviation_id for e in data.sections_added] == ["d0005"]
    assert [e.d.deviation_id for e in data.needs_review] == ["d0006"]
    assert data.counts["substantive"] == 3 and data.counts["needs_review"] == 1
    assert data.category_counts == [("Definitions", 1, 0, 0), ("Covenants", 1, 1, 1),
                                    ("Other", 1, 0, 0)]
    assert data.classified and data.unreadable_pages == [("Agreement", 9, "9")]


def test_citations_use_printed_pages_and_captions():
    data = _data(_labeled())
    entry = data.groups[0].entries[0]
    assert str(entry.template) == "Section 15(g)(i) (in 15(g) Notice), p. 3 (PDF p. 6)"
    assert str(entry.executed) == "Section 15(g)(i) (in 15(g) Notice), p. 6"
    assert entry.cites() == ("Section 15(g)(i), p. 3 (PDF p. 6)", "Section 15(g)(i), p. 6")
    added = data.sections_added[0]
    assert str(added.template) == "not present" and added.truncated_words == 50
    assert data.definitions[0].title == "\u201cAct\u201d"


def test_unclassified_runs_group_by_section_in_document_order():
    labeled = [LabeledDeviation(_dev(1, t_sec="15(g)(i)", e_sec="15(g)(i)"), NOT_CLASSIFIED,
                                True, ""),
               LabeledDeviation(_dev(2, t_sec="1", e_sec="1"), NOT_CLASSIFIED, True, "")]
    data = _data(labeled)
    assert not data.classified
    assert [g.name for g in data.groups] == ["Section 1 Definitions", "Section 15 Covenants"]


def test_html_has_every_part_and_the_prototype_warning():
    html = render_html(_data(_labeled()))
    assert html.count("Prototype — for professional review only") >= 2  # cover and footer
    for part in ("summary", "definitions", "sections", "non-substantive", "needs-review",
                 "method"):
        assert f'id="{part}"' in html
    assert 'id="d0002"' in html and "<ins>ten (10)</ins>" in html
    assert "Why review: evidence is not copied word for word" in html
    assert "not been classified" not in html
    unclassified = render_html(_data([LabeledDeviation(_dev(1), NOT_CLASSIFIED, True, "")]))
    assert "not been classified" in unclassified


# --- PDF -------------------------------------------------------------------------------

def test_no_renderer_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # import fails
    monkeypatch.setattr(builder, "find_browser", lambda: None)
    html = tmp_path / "r.html"
    html.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no PDF renderer"):
        render_pdf(html, tmp_path / "r.pdf")


def test_browser_can_be_set_by_environment(monkeypatch):
    monkeypatch.setenv("TIFIA_BROWSER", r"C:\browsers\chromium.exe")
    assert find_browser() == r"C:\browsers\chromium.exe"


@pytest.mark.skipif(not has_renderer(), reason="no PDF renderer on this machine")
def test_pdf_has_page_numbers_and_the_warning_in_the_footer(tmp_path):
    import pymupdf
    html = tmp_path / "report.html"
    html.write_text(render_html(_data(_labeled())), encoding="utf-8")
    renderer = render_pdf(html, tmp_path / "report.pdf")
    pdf = pymupdf.open(tmp_path / "report.pdf")
    second = pdf[1].get_text()
    assert f"Page 2 of {len(pdf)}" in second, renderer
    assert "Prototype — for professional review only" in second
    assert "Page 1 of" not in pdf[0].get_text()  # no footer on the cover
