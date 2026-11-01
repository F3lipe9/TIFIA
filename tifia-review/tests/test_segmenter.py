import re

import pytest

from tests.helpers import AGREEMENT, FIXTURES, NON_PROJECT, EXPECTED, TEMPLATE, segment_pdf
from tifia_review.models import Document, Page
from tifia_review.segmenter import (
    PageMap, Segmenter, caption_of, footnote_numbers, label_readings, split_footnotes,
    strip_footnote_refs, toc_mismatches,
)

needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")
needs_agreement = pytest.mark.skipif(not AGREEMENT.exists(), reason="agreement not present")


def by_id(seg):
    return {s.section_id: s for s in seg.sections}


def norm(text: str) -> str:
    """Whitespace and quote style, so planted excerpts compare with extracted text."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split())


# --- unit tests on small inputs ---------------------------------------------------

def test_page_map_offsets():
    pages = [Page(1, None, "abc"), Page(2, "1", ""), Page(3, "2", "de")]
    pmap = PageMap(pages)
    assert pmap.full_text == "abc\n\nde"
    assert [pmap.page_at(i).pdf_index for i in range(len(pmap.full_text))] == [1, 1, 1, 1, 2, 3, 3]


def test_footnote_area_after_blank_line():
    text = "body line\n \n4 \nDefined terms should be changed.\n5  If using each.\n"
    body, notes, expected = split_footnotes(text, expected=4)
    assert body == "body line\n \n"
    assert notes.startswith("4 \nDefined terms")
    assert expected == 6


def test_footnote_continued_from_previous_page():
    text = ("(e) \nlast body line\n________________________ \n(cont’d from previous page) \n"
            "Project Costs Documentation was not delivered.\n")
    body, notes, expected = split_footnotes(text, expected=71)
    assert body == "(e) \nlast body line\n"
    assert expected == 71


@pytest.mark.parametrize("line", [
    "401 S. Jackson Street",  # notice address: not the next footnote number
    "2 CFR Part 170",         # citation that happens to equal the next number
])
def test_numbered_body_lines_are_not_footnotes(line):
    text = f"If to Borrower:\n \n{line}\nSeattle, Washington\n"
    assert split_footnotes(text, expected=2) == (text, "", 2)


def test_footnote_reference_marks_removed_but_numbers_kept():
    text = ("Definitions.4  Unless ... from [at least one (1)]5 Rating Agency, ‘A3’ or "
            "‘Baa1’, a Material Adverse Effect125; or ratios of 1.50 to $520,981,378.]6970\n")
    numbers = [1, 3, 4, 5, 50, 69, 70, 125, 981]
    assert strip_footnote_refs(text, numbers) == (
        "Definitions.  Unless ... from [at least one (1)] Rating Agency, ‘A3’ or "
        "‘Baa1’, a Material Adverse Effect; or ratios of 1.50 to $520,981,378.]\n")
    assert footnote_numbers("3 \nNote three.\n4  Note four.\n(c) \n12 text", 3, 6) == [3, 4]


def test_page_breaks_locate_text_in_a_section_that_turns_the_page():
    pages = [Page(1, "1", "Section 1. \nTerm.  The first sentence. \n"),
             Page(2, "2", "The second sentence, on page two. \nSection 2. \nRate.  Fixed. \n")]
    seg = Segmenter().segment(Document("x", "template", pages))
    first = seg.sections[0]
    assert (first.section_id, first.pdf_start, first.pdf_end) == ("1", 1, 2)
    assert len(first.page_breaks) == 1
    assert first.text[first.page_breaks[0]:].startswith("The second sentence")
    assert seg.sections[1].page_breaks == []


def test_footnote_without_blank_line_needs_exact_next_number():
    text = "body line.\n11\nTo be inserted when applicable.\n"
    assert split_footnotes(text, expected=11)[0] == "body line.\n"
    assert split_footnotes(text, expected=9)[1] == ""


@pytest.mark.parametrize("text,expected", [
    ("Definitions.4  Unless the context otherwise requires", "Definitions"),
    ("Outstanding TIFIA Loan Balance; Revisions to Exhibit G and Loan \nAmortization "
     "Schedule. (a)", "Outstanding TIFIA Loan Balance; Revisions to Exhibit G and Loan "
                      "Amortization Schedule"),
    ("Transactions with other Governmental Authorities.  Except for the",
     "Transactions with other Governmental Authorities"),
    ("Project Changes:  any (A) change to the Eligible Project Costs", "Project Changes"),
    ("[Recovery Plan.  If the quarterly construction progress report", "Recovery Plan"),
    ("[Reserved] ", "Reserved"),
    ("TIFIA Loan proceeds shall be disbursed solely in respect of Eligible Project Costs.", ""),
    ("The Borrower shall have duly executed and delivered to the TIFIA Lender.", ""),
])
def test_caption(text, expected):
    assert caption_of(text) == expected


@pytest.mark.parametrize("label,expected", [
    ("c", [("alpha", 3)]),
    ("i", [("alpha", 9), ("roman", 1)]),
    ("x", [("alpha", 24), ("roman", 10)]),
    ("iv", [("roman", 4)]),
    ("ii", [("alpha", 35), ("roman", 2)]),
    ("aa", [("alpha", 27)]),
    ("xxvii", [("roman", 27)]),
    ("B", [("upper", 2)]),
    ("12", [("arabic", 12)]),
    ("see", []),
])
def test_label_readings(label, expected):
    assert label_readings(label) == expected


# A small agreement in the template's layout: TOC page, definitions, nested clauses,
# an inline "(iii)" that wraps to a line start, a footnote holding a "(c)" line, the
# "(h)/(i)" letter-or-roman case, signatures and an exhibit with its own "Section 1.".
MINI_PAGES = [
    "TABLE OF CONTENTS \nPage \nSection 1. \nDefinitions..........1 \nSection 2. \n"
    "Disbursement Conditions..........2 \nSECTION 3. Covenants..........2 \n \n"
    "EXHIBIT A – Form of TIFIA Bond \n",
    "TIFIA LOAN AGREEMENT \nTHIS TIFIA LOAN AGREEMENT (this “Agreement”) is made. \n"
    "Section 1. \nDefinitions.1  Unless the context otherwise requires: \n"
    "“Act” means the Act as defined in the recitals hereto. \n"
    "[“Bond”2 has the meaning set forth in the Indenture.] \n"
    "“Event” means, with respect to any Person, \n(a) \nan involuntary proceeding; or \n"
    "(b) \nsuch Person shall fail to pay. \n \n1 \nDefined terms may be changed.\n"
    "2  Bracketed text to be used if applicable.\n",
    "Section 2. \nDisbursement Conditions. \n(a) \nProceeds shall be used, (i) first, \n"
    "(ii) second, and \n(iii) third. \n(b) \nRequisitions.  The Borrower shall: \n"
    "(i) \nsubmit forms; \n(A) \nmonthly; and \n(B) \nquarterly; \n(ii) \nkeep records. \n"
    "(c) \nas set forth in \nSection 1.  The Borrower agrees. \n \n3 \nAlternative:\n(c) \n"
    "The Borrower anticipates.\n",
    "SECTION 3. Covenants.  The Borrower agrees as follows: \n(a) \nFirst. \n(b) \nSecond. \n"
    "(c) \nThird. \n(d) \nFourth. \n(e) \nFifth. \n(f) \nSixth. \n(g) \nSeventh. \n(h) \n"
    "Eighth, as follows: \n(i) \none; and \n(ii) \ntwo. \n(i) \nNinth. \n(j) \nTenth. \n"
    "IN WITNESS WHEREOF, the parties have executed this Agreement. \n",
    "EXHIBIT A \nFORM OF TIFIA BOND \nSection 1. \nPayment.  The Borrower promises to pay. \n"
    "(a) \nprincipal. \n",
]


@pytest.fixture(scope="module")
def mini():
    labels = ["i", None, "1", "2", "A-1"]
    pages = [Page(n, label, text) for n, (label, text) in
             enumerate(zip(labels, MINI_PAGES), start=1)]
    return Segmenter().segment(Document("mini", "template", pages))


def test_mini_structure(mini):
    assert [(s.section_id, s.level, s.parent_id) for s in mini.sections] == [
        ("preamble", 1, None),
        ("1", 1, None),
        ("2", 1, None),
        ("2(a)", 2, "2"),
        ("2(b)", 2, "2"),
        ("2(b)(i)", 3, "2(b)"),
        ("2(b)(i)(A)", 4, "2(b)(i)"),
        ("2(b)(i)(B)", 4, "2(b)(i)"),
        ("2(b)(ii)", 3, "2(b)"),
        ("2(c)", 2, "2"),
        ("3", 1, None),
        *[(f"3({c})", 2, "3") for c in "abcdefgh"],
        ("3(h)(i)", 3, "3(h)"),  # "(i)" after "(h)" is roman when "(ii)" follows
        ("3(h)(ii)", 3, "3(h)"),
        ("3(i)", 2, "3"),        # ... and the letter after that
        ("3(j)", 2, "3"),
        ("signatures", 1, None),
        ("Exhibit A", 1, None),
    ]


def test_mini_headings_text_and_pages(mini):
    s = by_id(mini)
    assert s["1"].heading == "Definitions"
    assert s["2(b)"].heading == "Requisitions"
    assert s["3"].heading == "Covenants"  # caption on the "SECTION 3." line
    assert s["Exhibit A"].heading == "Form of TIFIA Bond"  # from the TOC
    assert s["2(a)"].heading == ""
    # parent text runs through its children; a child stops at its next sibling
    assert s["2(b)"].text.startswith("(b) \nRequisitions.")
    assert s["2(b)"].text.endswith("keep records.")
    assert s["2(b)(i)"].text.endswith("quarterly;")
    assert "(iii) third." in s["2(a)"].text  # inline enumeration stays in the text
    assert "Alternative" not in s["2(c)"].text  # footnote cut
    assert "Defined terms may be changed" not in s["1"].text
    assert (s["2"].pdf_start, s["2"].pdf_end, s["2"].label_start) == (3, 3, "1")
    assert (s["preamble"].pdf_start, s["preamble"].label_start) == (2, None)
    assert s["Exhibit A"].text.startswith("EXHIBIT A")
    assert s["Exhibit A"].text.endswith("principal.")  # exhibit "Section 1." stays inside


def test_roman_i_after_h_with_nested_clauses_before_ii():
    # the non-project template's 16(h): "(i)" opens a roman list although "(A)".."(C)"
    # come before "(ii)"; the later "(i) Hedging" is the letter
    text = ("Section 16. \nNegative Covenants. \n"
            + "".join(f"({c}) \nCovenant {c}. \n" for c in "abcdefg")
            + "(h) \nOFAC Compliance. The Borrower shall not: \n(i) \nuse proceeds: \n"
              "(A) \nfirst; \n(B) \nsecond; \n(ii) \nviolate Sanctions. \n"
              "(i) \nHedging.  Other than as permitted. \n")
    seg = Segmenter().segment(Document("x", "template", [Page(1, "1", text)]))
    assert [s.section_id for s in seg.sections] == [
        "16", *[f"16({c})" for c in "abcdefgh"],
        "16(h)(i)", "16(h)(i)(A)", "16(h)(i)(B)", "16(h)(ii)", "16(i)"]
    assert seg.sections[-1].heading == "Hedging"


def test_mini_definitions(mini):
    terms = mini.defined_terms
    assert list(terms) == ["Act", "Bond", "Event"]
    assert terms["Act"].definition == "means the Act as defined in the recitals hereto."
    assert terms["Bond"].definition.startswith("has the meaning set forth")
    assert terms["Event"].definition.endswith("such Person shall fail to pay.")
    assert {t.section_id for t in terms.values()} == {"1"}


# --- the real template against its table of contents --------------------------------

@needs_template
def test_template_matches_its_table_of_contents():
    _, seg, toc = segment_pdf(TEMPLATE)
    assert len(toc) == 39 + 4 + 18  # sections, schedules, exhibits
    assert toc_mismatches(seg.sections, toc) == []
    top = [s.section_id for s in seg.sections if s.level == 1]
    assert top == ["preamble", *[e.section_id for e in toc[:39]], "signatures",
                   *[e.section_id for e in toc[39:]]]
    # The template's TOC page numbers are stale: 18 entries are one page early.
    sections = by_id(seg)
    offsets = {int(sections[e.section_id].label_start) - int(e.page_label)
               for e in toc if e.page_label}
    assert offsets == {0, 1}


@needs_template
def test_template_clause_tree():
    _, seg, _ = segment_pdf(TEMPLATE)
    ids = [s.section_id for s in seg.sections]
    assert len(ids) == len(set(ids)) == 364
    sections = by_id(seg)
    # footnote 70's alternative "(c)" and "(d)" clauses do not become children of Section 4
    assert [i for i in ids if i.startswith("4(")] == ["4(a)", "4(b)", "4(c)", "4(d)", "4(e)"]
    assert [i for i in ids if re.fullmatch(r"12\(a\)\([ivx]+\)", i)][-1] == "12(a)(xxvii)"
    assert [i for i in ids if i.startswith("15(g)(i)(")][-1] == "15(g)(i)(N)"
    assert sections["15(g)(i)(N)"].heading == "Other Adverse Events"
    assert sections["16(h)"].heading == "Transactions with other Governmental Authorities"
    assert sections["13(ee)"].heading == "Patriot Act"
    assert sections["19(a)(ix)"].parent_id == "19(a)"
    assert not [i for i in ids if i.startswith("1(")]  # definitions are not split into clauses
    assert (sections["16(b)"].pdf_start, sections["16(b)"].label_start) == (73, "70")


@needs_template
def test_template_definitions():
    _, seg, _ = segment_pdf(TEMPLATE)
    terms = seg.defined_terms
    assert len(terms) == 194
    acr = terms["Acceptable Credit Rating"].definition
    assert acr.startswith("means, with respect to any Person, the rating of its")
    assert "If using “each”" not in acr  # its footnote was cut
    assert terms["Accreted Value"].definition.endswith("Capital Appreciation Bonds.]")  # no "7"
    assert terms["Federal Fiscal Year"].definition.startswith("means the fiscal year")  # “FFY”
    assert terms["Act"].definition == "means the Act as defined in the recitals hereto."


@pytest.mark.skipif(not NON_PROJECT.exists(), reason="template not present")
def test_non_project_template_toc_check_reports_its_drafting_errors():
    _, seg, toc = segment_pdf(NON_PROJECT)
    assert toc_mismatches(seg.sections, toc) == [
        # TOC lists the alternative "[III][IV]"; the body uses Schedule IV
        "in TOC but not found: ['Schedule [III][IV]']",
        "found but not in TOC: ['Schedule IV']",
        # the PDF prints "Section 14." on the Patriot Act paragraph (13(cc) lost its number)
        "14: heading 'Patriot Act' vs TOC 'Representations and Warranties of TIFIA Lender'",
    ]


# --- an executed agreement -----------------------------------------------------------

@needs_agreement
def test_agreement_matches_its_table_of_contents():
    _, seg, toc = segment_pdf(AGREEMENT)
    # the agreement's own TOC still has the old caption of Section 35
    assert toc_mismatches(seg.sections, toc) == [
        "35: heading 'Counterparts; Electronic Signatures' vs TOC 'Counterparts'"]
    sections = by_id(seg)
    assert all(sections[e.section_id].label_start == e.page_label for e in toc if e.page_label)
    assert sections["Exhibit F"].heading == "TIFIA Debt Service"  # lettered unlike the template
    assert sections["Schedule 13(r)"].pdf_start == 102
    assert sections["39"].pdf_end == 93  # blank signature pages and "Sch. I" not included


@needs_agreement
def test_agreement_definitions():
    _, seg, _ = segment_pdf(AGREEMENT)
    terms = seg.defined_terms
    assert len(terms) == 249
    assert terms["NEPA Determination"].definition.startswith("means the Record of Decision")
    assert terms["Revenue Sharing Trigger Event"].definition.startswith("shall be deemed")
    assert "Nationally Recognized Statistical Rating Organization" in terms  # “NRSRO” alias


# --- synthetic fixtures: the template's structure plus exactly the planted change -------

def _expected_ids(template_ids: list[str], fixture: str) -> list[str]:
    ids = list(template_ids)
    if fixture == "fx_01_added":
        ids.insert(ids.index("4(e)") + 1, "4(f)")
    elif fixture == "fx_02_removed":
        ids.remove("4(e)")
    elif fixture == "fx_04_renumbered":  # "Section 6. Term." now comes first
        i, j = ids.index("5"), ids.index("6")
        ids[i], ids[j] = ids[j], ids[i]
    return ids


def _contains(section_text: str, excerpt: str) -> bool:
    """Planted excerpts elide text with "..." or "[...]"; each piece must appear."""
    pieces = re.split(r"\s*(?:\[\.\.\.\]|\.\.\.)\s*", norm(excerpt))
    return all(piece in norm(section_text) for piece in pieces if piece)


@needs_template
@pytest.mark.parametrize("fixture", sorted(EXPECTED))
def test_fixture_structure_and_planted_change(fixture):
    _, template, _ = segment_pdf(TEMPLATE)
    _, seg, _ = segment_pdf(FIXTURES / f"{fixture}.pdf")
    template_ids = [s.section_id for s in template.sections]
    assert [s.section_id for s in seg.sections] == _expected_ids(template_ids, fixture)
    assert seg.defined_terms.keys() == template.defined_terms.keys()

    executed, original = by_id(seg), by_id(template)
    for change in EXPECTED[fixture]["planted"]:
        if change["executed_section_id"]:
            section = executed[change["executed_section_id"]]
            assert section.pdf_start <= change["executed_page"] <= section.pdf_end
            assert _contains(section.text, change["executed_text"])
            if change.get("heading"):
                assert section.heading == change["heading"]
        if change["template_section_id"]:
            section = original[change["template_section_id"]]
            assert section.pdf_start <= change["template_page"] <= section.pdf_end
            assert _contains(section.text, change["template_text"])
        if change.get("affects_defined_term"):
            term = change["affects_defined_term"]
            assert _contains(seg.defined_terms[term].definition, change["executed_text"])
            assert _contains(template.defined_terms[term].definition, change["template_text"])
