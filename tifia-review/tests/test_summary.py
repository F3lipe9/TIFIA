"""Executive summary (Summary_Report.md): topic mapping, topic rows and their checks,
the debt service exhibit, the bottom line, and the two-page limit."""

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from tests.helpers import make_doc
from tifia_review.classifier import FILL_IN_CATEGORY
from tifia_review.llm.client import LLMClient
from tifia_review.models import (
    AlignMethod, Alignment, ChangeType, Deviation, LabeledDeviation, ScheduleDigest, TopicRow,
    ValueStatus,
)
from tifia_review.report.builder import find_browser
from tifia_review.store import ResultCache
from tifia_review.summary.bottom_line import BottomLineWriter, check, fallback
from tifia_review.summary.builder import (
    OtherChange, SummaryBuilder, SummaryData, SummaryTooLong, count_pages, parties,
)
from tifia_review.summary.exhibits import ExhibitLocator, parse_pages
from tifia_review.summary.mapping import TopicMapper, is_state_law_term
from tifia_review.summary.schedule import ScheduleParser, frequency, loan_amount, parse_amount
from tifia_review.summary.topics import TopicExtractor

TOPICS = {
    "project": {"label": "The project", "sections": ["recitals", "1:Project"],
                "ask": "What is being built."},
    "borrowing_authority": {"label": "Borrowing authority", "sections": ["13"],
                            "state_law_definitions": True, "ask": "The state law."},
    "loan_amount": {"label": "Loan amount", "sections": ["recitals", "3"], "ask": "Amount."},
    "interest_rate": {"label": "Interest rate", "sections": ["6"], "ask": "The rate."},
    "default_remedies": {"label": "Events of default and remedies", "sections": ["19"],
                         "ask": "Defaults."},
}
PRIORITY = ["Payment terms", "Events of default", "Covenants", "Definitions", "Other"]

TEMPLATE_SPECS = [
    ("preamble", "Preamble", "WHEREAS, the Borrower has requested a loan in a principal "
                             "amount not to exceed $[_________]."),
    ("1", "Definitions", "Section 1. Definitions."),
    ("1(a)", "", "“Project” means [describe project]."),
    ("1(b)", "", "“Toll Revenues” means all tolls."),
    ("3", "TIFIA Loan Amount", "The principal amount of the TIFIA Loan shall not exceed "
                               "$[_________]."),
    ("6", "Interest Rate", "The interest rate shall be [_____] percent per annum."),
    ("13", "Representations", "The Borrower is duly organized."),
    ("15", "Affirmative Covenants", "The Borrower shall maintain insurance."),
    ("19", "Events of Default and Remedies", "Section 19. Events of Default."),
    ("19(a)", "", "Failure to pay principal when due is an Event of Default."),
]
AGREEMENT_SPECS = [
    ("preamble", "Preamble", "THIS AGREEMENT is by and between the HAMPTON ROADS "
                             "TRANSPORTATION ACCOUNTABILITY COMMISSION, a body politic, and "
                             "the UNITED STATES DEPARTMENT OF TRANSPORTATION, acting through "
                             "the Build America Bureau. WHEREAS, the Borrower has requested a "
                             "loan in a principal amount not to exceed $[310,194,800]."),
    ("1", "Definitions", "Section 1. Definitions."),
    ("1(a)", "", "“Project” means the Hampton Roads Express Lanes Network, a system of "
                 "express lanes on Interstate 64."),
    ("1(b)", "", "“HRTAC Act” means Chapter 26 of Title 33.2 of the Code of Virginia of "
                 "1950, as amended."),
    ("3", "TIFIA Loan Amount", "The principal amount of the TIFIA Loan shall not exceed "
                               "$[310,194,800] (excluding capitalized interest)."),
    ("6", "Interest Rate", "The interest rate shall be 4.12 percent per annum, fixed on the "
                           "Effective Date."),
    ("13", "Representations", "The Borrower is duly organized under the HRTAC Act and has "
                              "full power to borrow."),
    ("15", "Affirmative Covenants", "The Borrower shall maintain insurance and a reserve."),
    ("19", "Events of Default and Remedies", "Section 19. Events of Default."),
    ("19(a)", "", "Failure to pay principal within five days is an Event of Default."),
    ("19(z)", "", "A downgrade of the Toll Road System below investment grade."),
]


@pytest.fixture(scope="module")
def docs():
    template = make_doc("t", TEMPLATE_SPECS)
    executed = make_doc("e", AGREEMENT_SPECS)
    t = {s.section_id: s for s in template.sections}
    alignments = [Alignment(t.get(s.section_id), s, AlignMethod.NUMBER if s.section_id in t
                            else None, 1.0) for s in executed.sections]
    return template, executed, alignments


def ld(n, template_section, executed_section, category="Covenants", substantive=True,
       term=None, review=False, template_text="old words", executed_text="new words here"):
    change = (ChangeType.ADDED if template_section is None else ChangeType.REMOVED
              if executed_section is None else ChangeType.MODIFIED)
    d = Deviation(f"d{n:04d}", change, template_section, executed_section, template_text,
                  executed_text, 1, 1, term)
    return LabeledDeviation(d, category, substantive, f"Explanation {n}.", needs_review=review)


def mapper(docs) -> TopicMapper:
    template, executed, alignments = docs
    return TopicMapper(TOPICS, PRIORITY, alignments, template, executed)


# --- mapping -------------------------------------------------------------------------

def test_deviation_in_19_counts_under_events_of_default_not_other(docs):
    changes, outside = mapper(docs).map([ld(1, "19(a)", "19(a)", "Events of default")])
    assert [x.deviation.deviation_id for x in changes["default_remedies"].substantive] == ["d0001"]
    assert outside == []


def test_section_added_in_the_agreement_counts_under_its_parent(docs):
    assert mapper(docs).topic_of(ld(1, None, "19(z)")) == "default_remedies"


def test_definitions_map_only_when_listed_or_citing_state_law(docs):
    m = mapper(docs)
    assert m.topic_of(ld(1, "1(a)", "1(a)", term="Project")) == "project"
    assert m.topic_of(ld(2, None, "1(b)", term="HRTAC Act")) == "borrowing_authority"
    assert m.topic_of(ld(3, "1(b)", None, term="Toll Revenues")) is None
    assert m.topic_of(ld(4, "preamble", "preamble")) == "project"  # recitals: first topic
    assert is_state_law_term("RCW Chapter 81.112", "means Chapter 81.112 RCW") is False
    assert is_state_law_term("Enabling Act", "means Chapter 81.112 of the Revised Code of "
                                             "Washington")


def test_count_sections_narrow_what_a_topic_counts(docs):
    template, executed, alignments = docs
    topics = {"authority": {"sections": ["13", "15"], "count_sections": ["13"], "ask": "x"}}
    m = TopicMapper(topics, PRIORITY, alignments, template, executed)
    assert m.topic_of(ld(1, "13", "13")) == "authority"
    assert m.topic_of(ld(2, "15", "15")) is None


def test_exhibits_missing_from_a_body_only_pdf_are_not_highlights(docs):
    gone = ld(1, "Exhibit N", None, "Payment terms")
    _, outside = mapper(docs).map([gone])
    assert outside == []  # the agreement file has no exhibits at all


def test_other_changes_ranked_by_priority_then_size_and_review_never_listed(docs):
    labeled = [
        ld(1, "15", "15", "Covenants", executed_text="a b c d e f g h i j k l"),
        ld(2, "15", "15", "Covenants", executed_text="a b"),
        ld(3, "15", "15", "Payment terms"),
        ld(4, "15", "15", "Covenants", review=True),
        ld(5, "15", "15", "Covenants", substantive=False),
        ld(6, "6", "6", FILL_IN_CATEGORY, substantive=False),
        ld(7, "6", "6", "Payment terms", review=True),
    ]
    changes, outside = mapper(docs).map(labeled)
    assert [x.deviation.deviation_id for x in outside] == ["d0003", "d0001", "d0002"]
    rate = changes["interest_rate"]
    assert (len(rate.substantive), rate.other, rate.needs_review) == (0, 1, 1)


# --- topic rows ----------------------------------------------------------------------

class TopicClient(LLMClient):
    """Answers each topic with replies[label] (a dict, or a list of dicts for retries)."""
    model_id = "fake"

    def __init__(self, replies):
        super().__init__()
        self.replies, self.requests = replies, []

    def complete_json(self, system, user, schema=None):
        self.usage.add(SimpleNamespace(input_tokens=10, output_tokens=5))
        label = user.split("\n", 1)[0].removeprefix("Topic: ")
        self.requests.append((label, user))
        reply = self.replies.get(label, {"status": "not_found", "summary": "", "quotes": []})
        return reply.pop(0) if isinstance(reply, list) else reply


def rows_for(docs, replies, cache=None, client=True):
    template, executed, alignments = docs
    c = TopicClient(replies) if client else None
    rows = TopicExtractor(c, TOPICS, cache).extract(template, executed, alignments, None, {})
    return {r.topic: r for r in rows}, c


def test_each_row_cites_the_section_its_quote_is_in(docs):
    rows, _ = rows_for(docs, {
        "Interest rate": {"status": "found", "summary": "Fixed at 4.12% a year.",
                          "quotes": ["shall be 4.12 percent per annum"]},
        "The project": {"status": "found", "summary": "Express lanes on Interstate 64.",
                        "quotes": ["a system of express lanes on Interstate 64"]},
        "Borrowing authority": {"status": "found",
                                "summary": "The HRTAC Act, part of the Code of Virginia.",
                                "quotes": ["Chapter 26 of Title 33.2 of the Code of Virginia"]},
    })
    assert rows["interest_rate"].status is ValueStatus.FOUND
    assert rows["interest_rate"].sources == ["§6, PDF p. 1"]
    assert rows["project"].sources == ["§1, PDF p. 1"]  # the definition, in Section 1
    assert rows["borrowing_authority"].sources == ["§1, PDF p. 1"]


def test_bracketed_values_are_draft_blanks_never_invented(docs):
    rows, _ = rows_for(docs, {
        # the model says "found", but the quote shows a bracketed value: code decides
        "Loan amount": {"status": "found", "summary": "Up to $310,194,800.",
                        "quotes": ["shall not exceed $[310,194,800]"]},
    })
    assert rows["loan_amount"].status is ValueStatus.DRAFT_BLANK
    assert rows["loan_amount"].summary == "Up to $310,194,800."


def test_not_inserted_needs_a_quote_saying_so(docs):
    rows, client = rows_for(docs, {"Loan amount": [
        {"status": "not_inserted", "summary": "Not inserted.",
         "quotes": ["shall not exceed $[310,194,800]"]},
        {"status": "not_inserted", "summary": "Not inserted.",
         "quotes": ["shall not exceed $[310,194,800]"]}]})
    row = rows["loan_amount"]
    assert row.status is ValueStatus.NOT_FOUND and "not inserted" in row.problem
    assert sum(1 for label, _ in client.requests if label == "Loan amount") == 2


def test_a_bad_quote_is_retried_then_falls_back_to_see_agreement(docs):
    rows, client = rows_for(docs, {"Interest rate": [
        {"status": "found", "summary": "Fixed.", "quotes": ["interest is fixed at four"]},
        {"status": "found", "summary": "Fixed.", "quotes": ["still not in the text"]}]})
    row = rows["interest_rate"]
    assert row.status is ValueStatus.NOT_FOUND and row.summary is None
    assert row.sections == ["§6"] and "not found word for word" in row.problem
    retry = [user for label, user in client.requests if label == "Interest rate"][1]
    assert "failed a check" in retry


def test_an_invented_number_fails_the_check(docs):
    rows, _ = rows_for(docs, {"Interest rate": [
        {"status": "found", "summary": "Fixed at 5.5%.", "quotes": ["4.12 percent per annum"]},
        {"status": "found", "summary": "Fixed at 4.12%.", "quotes": ["4.12 percent per annum"]}]})
    assert rows["interest_rate"].status is ValueStatus.FOUND


def test_advice_fails_the_check(docs):
    rows, _ = rows_for(docs, {"Interest rate": [
        {"status": "found", "summary": "The borrower should lock this rate.",
         "quotes": ["4.12 percent per annum"]}] * 2})
    assert "do not advise" in rows["interest_rate"].problem


def test_rows_that_pass_are_cached(docs, tmp_path):
    cache = ResultCache(tmp_path / "c.sqlite")
    reply = {"Interest rate": {"status": "found", "summary": "Fixed.",
                               "quotes": ["4.12 percent per annum"]}}
    _, first = rows_for(docs, reply, cache)
    rows, second = rows_for(docs, reply, cache)
    assert not any(label == "Interest rate" for label, _ in second.requests)
    assert rows["interest_rate"].status is ValueStatus.FOUND


def test_without_the_llm_every_row_points_to_the_agreement(docs):
    rows, _ = rows_for(docs, {}, client=False)
    assert all(r.status is ValueStatus.NOT_FOUND for r in rows.values())
    assert rows["default_remedies"].sections == ["§19"]


def test_parties_from_the_preamble(docs):
    _, executed, _ = docs
    borrower, lender = parties(executed.sections[0].text)
    assert borrower == "Hampton Roads Transportation Accountability Commission"
    assert lender == "U.S. Department of Transportation (Build America Bureau)"


# --- Exhibit G -----------------------------------------------------------------------

def _table_page(doc, heading: str, rows: list[list[str]]):
    """A page with a heading and a ruled table, as a debt service exhibit prints."""
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "Exhibit G-1", fontsize=9)
    page.insert_text((200, 90), heading, fontsize=12)
    x = [72, 192, 312, 432, 552]
    top, h = 120, 18
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x[c] + 4, top + r * h + 13), cell, fontsize=9)
    for r in range(len(rows) + 1):
        page.draw_line((x[0], top + r * h), (x[-1], top + r * h))
    for xi in x:
        page.draw_line((xi, top), (xi, top + len(rows) * h))
    return page


SCHEDULE = [["Payment Date", "Principal", "Interest", "Total Debt Service"],
            ["07/01/2031", "$100,000.00", "$5,000.00", "$105,000.00"],
            ["01/01/2032", "$100,000.00", "$4,000.00", "$104,000.00"],
            ["07/01/2032", "$300,000.00", "$3,000.00", "$303,000.00"],
            ["Total", "$500,000.00", "$12,000.00", "$512,000.00"]]


def make_pdf(path: Path, pages) -> Path:
    doc = pymupdf.open()
    toc = doc.new_page()
    toc.insert_text((72, 72), "EXHIBIT A - Form of Bond ........ A-1", fontsize=9)
    toc.insert_text((72, 90), "EXHIBIT G - TIFIA Debt Service ........ G-1", fontsize=9)
    for kind, arg in pages:
        if kind == "table":
            _table_page(doc, "EXHIBIT G   TIFIA DEBT SERVICE", arg)
        else:
            page = doc.new_page()
            page.insert_text((72, 72), arg, fontsize=11)
    doc.save(path)
    return path


def test_exhibit_g_digest_and_principal_cross_check(tmp_path):
    pdf = make_pdf(tmp_path / "g.pdf", [("table", SCHEDULE), ("text", "EXHIBIT H  OPINIONS")])
    exhibit = ExhibitLocator().find(pdf)
    assert exhibit.letter == "G" and exhibit.pages == [2]  # not the contents page
    digest = ScheduleParser().parse(pdf, exhibit, 500_000.0)
    assert digest.status is ValueStatus.FOUND and digest.matches_loan_amount is True
    assert (digest.first_payment, digest.final_payment) == ("2031-07-01", "2032-07-01")
    assert (digest.payment_count, digest.frequency) == (3, "semi-annual")
    assert digest.total_principal == 500_000.0
    assert digest.largest_annual == ("2032", 407_000.0)


def test_exhibit_g_total_mismatch_shows_a_warning_not_a_digest(tmp_path):
    pdf = make_pdf(tmp_path / "g.pdf", [("table", SCHEDULE)])
    digest = ScheduleParser().parse(pdf, ExhibitLocator().find(pdf), 310_194_800.0)
    assert digest.matches_loan_amount is False
    data = _data([], schedule=digest)
    assert data.schedule_lines() == ["Schedule total doesn't match loan amount — see "
                                     "Exhibit G, PDF p. 2."]


def test_exhibit_g_attached_placeholder_is_not_inserted(tmp_path):
    pdf = make_pdf(tmp_path / "g.pdf", [("text", "EXHIBIT F  TIFIA DEBT SERVICE  [Attached.]")])
    exhibit = ExhibitLocator().find(pdf)
    digest = ScheduleParser().parse(pdf, exhibit, 1.0)
    assert exhibit.letter == "F" and digest.status is ValueStatus.NOT_INSERTED
    assert "not yet inserted" in _data([], schedule=digest).schedule_lines()[0]


def test_exhibit_g_missing_points_to_the_exhibit(tmp_path):
    pdf = make_pdf(tmp_path / "g.pdf", [("text", "SECTION 9. Payment of Principal")])
    digest = ScheduleParser().parse(pdf, ExhibitLocator().find(pdf), 1.0)
    assert digest.status is ValueStatus.NOT_FOUND
    assert _data([], schedule=digest).schedule_lines() == [
        "Full schedule: Exhibit G (not in this PDF)."]


def test_a_partly_readable_schedule_is_never_shown(tmp_path):
    bad = [*SCHEDULE[:2], ["01/01/2032", "see note", "$4,000.00", "$104,000.00"]]
    pdf = make_pdf(tmp_path / "g.pdf", [("table", bad)])
    digest = ScheduleParser().parse(pdf, ExhibitLocator().find(pdf), 1.0)
    assert digest.status is ValueStatus.NOT_FOUND and "could not be read" in digest.problem


def test_schedule_helpers():
    assert loan_amount("shall not exceed $[310,194,800] (excluding") == 310_194_800.0
    assert parse_amount("(1,000.50)") == -1000.5 and parse_amount("-") == 0.0
    assert parse_amount("n/a") is None
    assert parse_pages("120-122, 125") == [120, 121, 122, 125]
    from datetime import date
    assert frequency([date(2030, 1, 1), date(2031, 1, 1)]) == "annual"


# --- bottom line ---------------------------------------------------------------------

def _rows() -> list[TopicRow]:
    return [TopicRow("interest_rate", "Interest rate", "Fixed.", ["q"], ["§6"],
                     ValueStatus.FOUND, 2, ["d0001", "d0002"], other_changes=1),
            TopicRow("oversight", "Project oversight", None, [], [], ValueStatus.NOT_FOUND, 0,
                     [], sections=["§22"])]


TOTALS = {"total": 609, "substantive": 421, "needs_review": 2}


def test_bottom_line_checks_topics_and_numbers():
    given = {"total_deviations": 609, "substantive_deviations": 421, "needs_review": 2,
             "substantive_outside_these_topics": 400,
             "topics": [{"topic": "Interest rate", "substantive_changes": 2, "minor_changes": 1}]}
    ok = {"text": "The agreement departs from the template in 421 places. Interest rate has 2.",
          "topics_mentioned": ["Interest rate"]}
    assert check(ok, given) is None
    assert "not one of the topics" in check({**ok, "topics_mentioned": ["Security"]}, given)
    assert "not one of the counts" in check(
        {**ok, "text": "It changed 37 things. Interest rate has 2."}, given)
    assert "two or three sentences" in check({**ok, "text": "One sentence only."}, given)


def test_bottom_line_falls_back_to_the_counts():
    class Bad(LLMClient):
        model_id = "fake"

        def complete_json(self, system, user, schema=None):
            return {"text": "Too short.", "topics_mentioned": []}

    text, by_llm = BottomLineWriter(Bad()).write(_rows(), TOTALS, 400)
    assert not by_llm and text == fallback(_rows(), TOTALS, 400)
    assert "421 substantive" in text and "Interest rate (2)" in text


# --- the PDF -------------------------------------------------------------------------

def _data(rows, schedule=None, others=3, words=12) -> SummaryData:
    meta = {"agreement_id": "x", "agreement_title": "Test agreement", "template_title": "T",
            "borrower": "B", "lender": "L", "date": "Draft — not dated", "draft": True,
            "run_id": "r", "full_report": "x__vs__t.pdf", "generated": "now"}
    other = [OtherChange(f"d{n:04d}", "§15(k)", "Covenants", " ".join(["word"] * words))
             for n in range(others)]
    return SummaryData(meta, "Bottom line. Two sentences.", True, rows, other, others + 5,
                       schedule, TOTALS, True)


def test_changed_column_counts_and_states():
    data = _data(_rows())
    assert data.changed_text(data.rows[0]) == "2 substantive changes — d0001, d0002"
    assert data.changed_text(data.rows[1]) == "Standard"
    assert data.summary_text(data.rows[1]) == "See agreement §22."
    draft = TopicRow("loan_amount", "Loan amount", "Up to $[310,194,800].", ["q"], [],
                     ValueStatus.DRAFT_BLANK, 0, [])
    assert data.summary_text(draft).endswith("(draft, not final)")
    draft.summary = "Up to $[310,194,800], not final."
    assert data.summary_text(draft) == "Up to $[310,194,800], not final."
    draft.sources = ["§1, PDF p. 30", "§1, PDF p. 15", "§9, p. 44", "§1, PDF p. 15"]
    assert data.source_text(draft) == "§1, PDF pp. 15, 30; §9, p. 44"


def test_a_word_the_agreement_uses_is_not_advice():
    from tifia_review.summary.text import advice_problem
    source = "all notices to the TIFIA Lender should be made by email"
    assert advice_problem("Notices to the lender should go by email.", source) is None
    assert advice_problem("The borrower should negotiate.", "no such word") is not None


def test_judging_words_fail_the_check(docs):
    rows, _ = rows_for(docs, {"Interest rate": [
        {"status": "found", "summary": "A favorable fixed rate.",
         "quotes": ["4.12 percent per annum"]}] * 2})
    assert "judge" in rows["interest_rate"].problem


def test_a_summary_that_cannot_fit_is_an_error_not_a_third_page(tmp_path):
    def three_pages(html_path, pdf_path):
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page()
        doc.save(pdf_path)
        return "fake"

    with pytest.raises(SummaryTooLong):
        SummaryBuilder(2).write(_data(_rows()), tmp_path, renderer=three_pages)


@pytest.mark.skipif(find_browser() is None, reason="no browser to print the PDF")
def test_a_long_summary_fits_on_two_pages(tmp_path):
    long = " ".join(["The agreement sets out a long provision in plain words."] * 6)
    rows = [TopicRow(f"t{n}", f"Topic {n}", long, ["q"], [f"§{n}, PDF p. {n}"],
                     ValueStatus.FOUND, 3, ["d0001", "d0002", "d0003"]) for n in range(12)]
    schedule = ScheduleDigest(ValueStatus.FOUND, [120], "G", "2031-07-01", "2060-07-01", 60,
                              "semi-annual", 310_194_800.0, ("2045", 18_200_000.0), True)
    data = _data(rows, schedule, others=3, words=60)
    data.rows[0].topic = "payment_schedule"
    _, pdf, _, shown = SummaryBuilder(2).write(data, tmp_path)
    assert count_pages(pdf) <= 2
    text = "".join(page.get_text() for page in pymupdf.open(pdf))
    assert "Not legal or financial advice" in text and "Bottom line" in text
    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["level"] == shown.level
