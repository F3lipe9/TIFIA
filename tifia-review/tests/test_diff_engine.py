import re

import numpy as np
import pytest

from tests.helpers import (
    AGREEMENT, FIXTURES, EXPECTED, TEMPLATE, make_doc, no_embeddings, segment_pdf,
)
from tifia_review.aligner import Aligner, load_embedder
from tifia_review.diff_engine import DiffEngine, is_fill_in, normalize, split_sentences
from tifia_review.models import AlignMethod, Alignment, ChangeType, Section, SegmentedDocument

ADDED, REMOVED, MODIFIED = ChangeType.ADDED, ChangeType.REMOVED, ChangeType.MODIFIED
FILL_IN, RENUMBERED = ChangeType.FILL_IN, ChangeType.RENUMBERED


def no_match(texts: list[str]) -> np.ndarray:
    """Embedder under which nothing looks alike."""
    return np.zeros((len(texts), 8))


def diff(template_specs, executed_specs):
    template, executed = make_doc("t", template_specs), make_doc("e", executed_specs)
    return DiffEngine().diff(template, executed, Aligner(embed=no_match).align(template, executed))


def summary(deviations):
    return [(d.change_type, d.template_section_id, d.executed_section_id, d.affects_defined_term)
            for d in deviations]


def norm(text: str) -> str:
    return " ".join(normalize(text).split())


# --- normalization and sentences ------------------------------------------------------

def test_normalize():
    nbsp, soft_hyphen, private_use = chr(0xA0), chr(0xAD), chr(0xF097)
    assert normalize("\u201cSemi-\n Annual\u201d \u2018A+\u2019 Borrower\u2019s") == \
        '"Semi-Annual" "A+" Borrower"s'
    assert normalize(f"\ufb01nancial \u2013 test{nbsp}x \u2014 y{soft_hyphen}z {private_use}") \
        == "financial - test x - yz"


@pytest.mark.parametrize("text,expected", [
    ("It complies with 23 U.S.C. Section 603. The Lender may (e.g. in writing) object. "
     "See Pub. L. No. 91-508. Done.",
     ["It complies with 23 U.S.C. Section 603.", "The Lender may (e.g. in writing) object.",
      "See Pub. L. No. 91-508.", "Done."]),
    ("X means, with respect to any Person, (a) a petition shall be filed; (b) such Person "
     "shall (i) apply, (ii) consent; or (c) the Trustee shall act.",
     ["X means, with respect to any Person, (a) a petition shall be filed;",
      "(b) such Person shall (i) apply, (ii) consent; or", "(c) the Trustee shall act."]),
    ("WHEREAS, the Borrower applied; and WHEREAS, the Lender agrees. NOW, THEREFORE, the "
     "parties agree.",
     ["WHEREAS, the Borrower applied; and", "WHEREAS, the Lender agrees.",
      "NOW, THEREFORE, the parties agree."]),
])
def test_split_sentences(text, expected):
    assert [text[a:b] for a, b in split_sentences(text)] == expected


# --- template placeholders ------------------------------------------------------------

@pytest.mark.parametrize("template,executed,expected", [
    # blanks and instructions take any text
    ("between [BORROWER], a [_________] created", "between Metro Transit, a [_________] created",
     True),
    ("shall be [___] percent ([__]%) per annum.", "shall be 4.50 percent (4.50%) per annum.",
     True),
    ("dated [__________] (the \"Application\")", "dated May 27, 2021 (the \"Application\")", True),
    ("nominated by [insert name of planning organization].", "nominated by the PSRC.", True),
    # optional text is kept or dropped as a whole; alternatives are chosen
    ("the Lender [and the Servicer] shall", "the Lender shall", True),
    ("the Lender [and the Servicer] shall", "the Lender and the Servicer shall", True),
    ("the Lender [and the Servicer] shall", "the Lender and the Trustee shall", False),
    ("the Lender [and each other Related Party] shall", "the Lender and each Related Party shall",
     False),
    ("from [each][at least one (1)] Rating Agency", "from at least one (1) Rating Agency", True),
    ("a fee of [ten percent (10%)] of", "a fee of fifteen percent (15%) of", False),
    # a blank inside optional text: fill the blank, or drop the whole sentence
    ("[The Lender has designated [_____] as Servicer.]", "The Lender has designated BAB as "
     "Servicer.", True),
    ("[The Lender has designated [_____] as Servicer.]", "The Lender may designate a Servicer.",
     False),
    # changes outside placeholders, or a negotiated addition parked in a blank
    ("[BORROWER] shall pay the fee.", "Metro Transit shall not pay the fee.", False),
    ("pursuant to [insert cross-reference].", "pursuant to Section 5.02 of the Resolution, "
     "provided that the Borrower may terminate this Agreement at any time without notice to "
     "the Lender and without any further obligation to anyone.", False),
])
def test_fill_in(template, executed, expected):
    assert is_fill_in(norm(template), norm(executed)) is expected


# --- section pairs on small documents ---------------------------------------------------

TEMPLATE_SPECS = [
    ("preamble", "Preamble", "THIS AGREEMENT is between [BORROWER] and the Lender. WHEREAS, "
     "the Borrower applied on [__________]; and WHEREAS, the Lender agrees."),
    ("1", "Definitions", "Section 1. Definitions. Capitalized terms have these meanings.\n"
     "“Act” means the Act.\n“Bond” means the TIFIA Bond.\n"
     "[“Hedge” means any hedge.]\n“Term Sheet” means the term sheet."),
    ("2", "Term", "Section 2. Term. The Loan ends on the Final Maturity Date. Interest accrues "
     "at [___] percent. The Borrower shall pay within thirty (30) days. The Borrower may "
     "prepay."),
    ("3", "Covenants", "Section 3. Covenants. The Borrower agrees as follows:"),
    ("3(a)", "Notices", "(a) Notices. The Borrower shall give notice."),
    ("3(b)", "Insurance", "(b) Insurance. The Borrower shall insure the Project:"),
    ("3(b)(i)", "", "(i) against property loss; and"),
    ("3(b)(ii)", "", "(ii) against liability."),
    ("Schedule I", "Project Budget", "SCHEDULE I PROJECT BUDGET [To be provided by Borrower]"),
    ("signatures", "Signatures", "IN WITNESS WHEREOF, the parties have signed. By: ______"),
]
EXECUTED_SPECS = [
    ("preamble", "Preamble", "THIS AGREEMENT is between Metro Transit and the Lender. WHEREAS, "
     "the Borrower applied on May 1, 2024; and WHEREAS, the Lender agrees to lend."),
    ("1", "Definitions", "SECTION 1. Definitions. Capitalized terms have these meanings.\n"
     "“Act” means the Act.\n“Bond” means the TIFIA Bond and any parity bond.\n"
     "“Pledged Taxes” means the sales tax.\n“Term Sheet” means the term sheet."),
    ("2", "Term", "SECTION 2. Term. The Loan ends on the Final Maturity Date. Interest accrues "
     "at 4.50 percent. The Borrower shall pay within ten (10) days. The Borrower may prepay "
     "without penalty."),
    ("3", "Covenants", "SECTION 3. Covenants. The Borrower agrees as follows:"),
    ("3(a)", "Notices", "(a) Notices. The Borrower shall give notice."),
    ("Schedule I", "Project Budget", "SCHEDULE I PROJECT BUDGET Total Project Costs: "
     "$100,000,000."),
]


def test_deviations_by_section():
    deviations = diff(TEMPLATE_SPECS, EXECUTED_SPECS)
    assert summary(deviations) == [
        (FILL_IN, "preamble", "preamble", None),   # borrower name and application date
        (MODIFIED, "preamble", "preamble", None),  # "agrees" -> "agrees to lend"
        (MODIFIED, "1", "1", "Bond"),
        (FILL_IN, "1", "1", "Hedge"),               # optional definition left out
        (ADDED, "1", "1", "Pledged Taxes"),
        (FILL_IN, "2", "2", None),                  # the interest rate blank
        (MODIFIED, "2", "2", None),                 # two neighboring changes, merged
        (REMOVED, "3(b)", None, None),              # with its clauses (i) and (ii)
        (FILL_IN, "Schedule I", "Schedule I", None),
    ]                                               # no deviation for the signature block
    by_kind = {(d.change_type, d.template_section_id): d for d in deviations}
    merged = by_kind[(MODIFIED, "2")]
    assert merged.template_text == ("The Borrower shall pay within thirty (30) days. "
                                    "The Borrower may prepay.")
    assert merged.executed_text.endswith("may prepay without penalty.")
    assert "(ii) against liability." in by_kind[(REMOVED, "3(b)")].template_text
    assert by_kind[(FILL_IN, "preamble")].executed_text.startswith(
        "THIS AGREEMENT is between Metro Transit")
    assert [d.deviation_id for d in deviations] == [f"d{n:04d}" for n in range(1, 10)]


def test_pages_come_from_page_breaks():
    text_t = "Section 5. Fees. A fee applies. The fee is one percent. It is payable yearly."
    text_e = "SECTION 5. Fees. A fee applies. New sentence. The fee is two percent. " \
             "It is payable yearly."
    # each document turns the page right before "The fee"
    t = Section("5", "Fees", text_t, 10, 11, page_breaks=[text_t.index("The fee")])
    e = Section("5", "Fees", text_e, 20, 21, page_breaks=[text_e.index("The fee")])
    deviations = DiffEngine().diff(SegmentedDocument("t", [t], {}),
                                   SegmentedDocument("e", [e], {}),
                                   [Alignment(t, e, AlignMethod.NUMBER, 1.0)])
    # "New sentence." (page 20) and the changed fee (page 11 in the template), merged
    assert [(d.change_type, d.template_page, d.executed_page) for d in deviations] == [
        (MODIFIED, 11, 20)]
    assert deviations[0].template_text == "The fee is one percent."
    assert deviations[0].executed_text == "New sentence. The fee is two percent."


@pytest.mark.parametrize("executed_specs,expected", [
    # the two sections keep their places but swap numbers (as in fx_04): both reported
    ([("5", "Term", "SECTION 5. Term. Until maturity."),
      ("4", "Interest Rate", "SECTION 4. Interest Rate. Fixed.")],
     [("4", "5", "Section 4. Term.", "Section 5. Term."),
      ("5", "4", "Section 5. Interest Rate.", "Section 4. Interest Rate.")]),
    # Term moves after Interest Rate: only the section that moved is reported
    ([("4", "Interest Rate", "SECTION 4. Interest Rate. Fixed."),
      ("5", "Term", "SECTION 5. Term. Until maturity.")],
     [("4", "5", "Section 4. Term.", "Section 5. Term.")]),
])
def test_renumbered_sections(executed_specs, expected):
    template = [("4", "Term", "Section 4. Term. Until maturity."),
                ("5", "Interest Rate", "Section 5. Interest Rate. Fixed.")]
    deviations = diff(template, executed_specs)
    assert [(d.template_section_id, d.executed_section_id, d.template_text, d.executed_text)
            for d in deviations if d.change_type is RENUMBERED] == expected


def test_relettering_after_a_removal_is_not_renumbering():
    template = [("3", "Covenants", "Section 3. Covenants."),
                ("3(a)", "Notices", "(a) Notices. Give notice."),
                ("3(b)", "Insurance", "(b) Insurance. Insure it."),
                ("3(c)", "Taxes", "(c) Taxes. Pay taxes.")]
    executed = [("3", "Covenants", "SECTION 3. Covenants."),
                ("3(a)", "Notices", "(a) Notices. Give notice."),
                ("3(b)", "Taxes", "(b) Taxes. Pay taxes.")]
    assert summary(diff(template, executed)) == [(REMOVED, "3(b)", None, None)]


def test_only_the_clause_that_moved_within_its_list_is_renumbered():
    template = [("3", "Covenants", "Section 3. Covenants."),
                *[(f"3({c})", name, f"({c}) {name}. Comply with {name.lower()}.")
                  for c, name in zip("abcd", ["Audit", "Budget", "Ethics", "Debarment"])]]
    executed = [("3", "Covenants", "SECTION 3. Covenants."),
                *[(f"3({c})", name, f"({c}) {name}. Comply with {name.lower()}.")
                  for c, name in zip("abcd", ["Audit", "Debarment", "Budget", "Ethics"])]]
    assert summary(diff(template, executed)) == [(RENUMBERED, "3(d)", "3(b)", None)]


# --- synthetic fixtures: exactly the expected deviations, blanks as fill-ins ----------

needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")


def _key(text: str) -> str:
    """Normalized tokens one space apart: the re-typeset fixture reads "Authority , a"."""
    return " ".join(re.findall(r"_+|[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*|\S", normalize(text)))


def _contains(text: str, excerpt: str) -> bool:
    """Expected excerpts elide text with "..." or "[...]" and may start with the label."""
    excerpt = re.sub(r"^\(\w+\)\s*", "", excerpt)
    pieces = re.split(r"\s*(?:\[\.\.\.\]|\.\.\.)\s*", excerpt)
    return all(_key(piece) in _key(text) for piece in pieces if piece.strip())


@needs_template
@pytest.mark.parametrize("fixture", sorted(EXPECTED))
def test_fixture_deviations_are_exactly_the_expected_ones(fixture):
    """The planted change is a deviation and each filled blank is a fill_in; nothing else."""
    _, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(FIXTURES / f"{fixture}.pdf")
    alignments = Aligner(embed=no_embeddings).align(template, executed)
    deviations = DiffEngine().diff(template, executed, alignments)
    expected = EXPECTED[fixture]["planted"] + EXPECTED[fixture]["fill_ins"]
    assert len(deviations) == len(expected)
    for change in expected:
        match = [d for d in deviations
                 if d.change_type.value == change["change_type"]
                 and d.template_section_id == change["template_section_id"]
                 and d.executed_section_id == change["executed_section_id"]
                 and d.template_page == change["template_page"]
                 and d.executed_page == change["executed_page"]
                 and d.affects_defined_term == change.get("affects_defined_term")
                 and _contains(d.template_text, change["template_text"])
                 and _contains(d.executed_text, change["executed_text"])]
        assert len(match) == 1, change


# --- a real agreement (needs sentence-transformers and the cached model) -------------

@pytest.fixture(scope="module")
def drle_deviations():
    pytest.importorskip("sentence_transformers")
    if not (TEMPLATE.exists() and AGREEMENT.exists()):
        pytest.skip("template or agreement not present")
    try:
        embed = load_embedder(download=False)
    except OSError:
        pytest.skip("embedding model not downloaded")
    _, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(AGREEMENT)
    return DiffEngine().diff(template, executed, Aligner(embed=embed).align(template, executed))


def test_real_agreement_deviations_are_not_formatting_noise(drle_deviations):
    """Week-5 sanity check on DRLE: deviations that differ only in punctuation are rare,
    and every one of them is a real edit (a dropped serial comma, added quotes)."""
    def words(text):
        return re.findall(r"\w+", normalize(text))

    modified = [d for d in drle_deviations if d.change_type is MODIFIED]
    punctuation_only = [d for d in modified
                        if words(d.template_text) == words(d.executed_text)]
    assert len(punctuation_only) <= 0.05 * len(drle_deviations)
    for d in punctuation_only:  # but something did change
        assert normalize(d.template_text) != normalize(d.executed_text)


def test_real_agreement_known_deviations(drle_deviations):
    def find(kind, section=None, term=None, text=""):
        return [d for d in drle_deviations if d.change_type is kind
                and (section is None or section in (d.template_section_id, d.executed_section_id))
                and (term is None or d.affects_defined_term == term)
                and text in d.template_text + " " + d.executed_text]

    assert find(FILL_IN, "preamble", text="July 31, 2021")      # approval date filled in
    assert find(MODIFIED, "preamble", text="secured by the Pledged Taxes")
    assert find(MODIFIED, "1", term="Acceptable Credit Rating", text="NRSRO")
    assert find(ADDED, "1", term="1996 Motor Vehicle Tax")
    assert find(MODIFIED, "12(a)(vi)", text="ninety (90)")       # rating delivery window
    moved = [(d.template_section_id, d.executed_section_id) for d in find(RENUMBERED)]
    assert ("16(k)", "10(c)") in moved                            # "No Defeasance" moved
    assert ("13(l)", "13(k)") not in moved  # relettered after the removed 13(k): a shift
    assert not [d for d in drle_deviations if "signatures" in
                (d.template_section_id, d.executed_section_id)]
    pages = [d.executed_page for d in find(ADDED, "1", term="1996 Motor Vehicle Tax")]
    assert pages == [6]
