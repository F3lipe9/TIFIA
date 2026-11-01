import logging
import re
import zlib

import numpy as np
import pytest

from tests.helpers import (
    AGREEMENT, FIXTURES, EXPECTED, TEMPLATE, make_doc, no_embeddings, segment_pdf,
)
from tifia_review import aligner as aligner_module
from tifia_review.aligner import Aligner, heading_similarity, heading_words, load_embedder
from tifia_review.models import AlignMethod

NUMBER, HEADING, EMBEDDING = AlignMethod.NUMBER, AlignMethod.HEADING, AlignMethod.EMBEDDING


def bag_of_words(texts: list[str]) -> np.ndarray:
    """Stand-in embedder: hashed word counts, unit length."""
    vectors = np.zeros((len(texts), 512))
    for row, text in enumerate(texts):
        for word in re.findall(r"[a-z]+", text.lower()):
            vectors[row, zlib.crc32(word.encode()) % 512] += 1
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)


def same_topic(texts: list[str]) -> np.ndarray:
    """Stand-in embedder that finds every pair of sections alike (cosine 1.0)."""
    return np.ones((len(texts), 4)) / 2


def pairs(alignments) -> dict[str, tuple[str | None, AlignMethod | None]]:
    """template id (or "+executed id" for ADDED) -> (executed id, method)."""
    out = {}
    for a in alignments:
        if a.template_section is None:
            out["+" + a.executed_section.section_id] = (None, None)
        else:
            out[a.template_section.section_id] = (
                a.executed_section.section_id if a.executed_section else None, a.method)
    return out


def assert_each_section_once(alignments, template, executed):
    assert sorted(a.template_section.section_id for a in alignments if a.template_section) \
        == sorted(s.section_id for s in template.sections)
    assert sorted(a.executed_section.section_id for a in alignments if a.executed_section) \
        == sorted(s.section_id for s in executed.sections)


# --- captions ------------------------------------------------------------------------

def test_heading_words():
    assert heading_words("Opinions Required from Counsel to Borrower "
                         "[and each other Borrower Related Party]") == {
        "opinion", "required", "counsel", "borrower"}
    assert heading_words("Notices") == heading_words("NOTICE") == {"notice"}
    assert heading_words("[Reserved]") == {"reserved"}
    assert heading_words("Business") == {"business"}  # "ss" is not a plural


@pytest.mark.parametrize("a,b,expected", [
    ("Credit Ratings", "Credit Rating", 1.0),
    ("Hedging", "Hedging and Variable Rate Bonds", 0.625),
    ("Litigation", "List of Litigation Matters", 0.667),
    ("Optional Prepayments", "Mandatory Prepayments", 0.417),
    ("Insurance", "Title", 0.0),
    ("", "Title", None),
])
def test_heading_similarity(a, b, expected):
    score = heading_similarity(a, b)
    assert score == expected if expected is None else round(score, 3) == expected


# --- small documents -----------------------------------------------------------------

TEMPLATE_SPECS = [
    ("1", "Definitions", "Section 1. Definitions. Capitalized terms have these meanings."),
    ("2", "Term", "Section 2. Term. The term of the TIFIA Loan ends on the Final Maturity Date."),
    ("2(a)", "", "(a) The TIFIA Loan matures on the Final Maturity Date."),
    ("2(b)", "", "(b) The Borrower may extend the term with consent of the TIFIA Lender."),
    ("3", "Interest Rate", "Section 3. Interest Rate. The interest rate is fixed."),
    ("4", "Covenants", "Section 4. Covenants. The Borrower agrees as follows:"),
    ("4(a)", "Notices", "(a) Notices. The Borrower shall give notice of any litigation."),
    ("4(b)", "Insurance", "(b) Insurance. The Borrower shall maintain property insurance."),
    ("4(c)", "Taxes", "(c) Taxes. The Borrower shall pay all taxes when due."),
    ("Exhibit A", "Form of TIFIA Bond",
     "EXHIBIT A FORM OF TIFIA BOND The Borrower promises to pay."),
    ("Exhibit B", "TIFIA Debt Service", "EXHIBIT B TIFIA DEBT SERVICE Amortization schedule."),
]
EXECUTED_SPECS = [  # Sections 2 and 3 swapped, 4(b) dropped, 4(c) added, exhibits relettered
    ("1", "Definitions", "SECTION 1. Definitions. Capitalized terms have these meanings."),
    ("2", "Interest Rate", "SECTION 2. Interest Rate. The interest rate is 4.50%."),
    ("3", "Term", "SECTION 3. Term. The term of the TIFIA Loan ends on the Final Maturity Date."),
    ("3(a)", "", "(a) The TIFIA Loan matures on the Final Maturity Date."),
    ("3(b)", "", "(b) The Borrower may extend the term with the prior written consent of "
                 "the TIFIA Lender."),
    ("4", "Covenants", "SECTION 4. Covenants. The Borrower agrees as follows:"),
    ("4(a)", "Notice", "(a) Notice. The Borrower shall give notice of any litigation or default."),
    ("4(b)", "Taxes", "(b) Taxes. The Borrower shall pay all taxes when due."),
    ("4(c)", "Audit", "(c) Audit. Each year an independent accountant audits the books."),
    ("Exhibit A", "TIFIA Debt Service", "EXHIBIT A TIFIA DEBT SERVICE Amortization schedule."),
    ("Exhibit B", "Form of TIFIA Bond",
     "EXHIBIT B FORM OF TIFIA BOND The Borrower promises to pay."),
]


def test_numbers_and_captions():
    template, executed = make_doc("t", TEMPLATE_SPECS), make_doc("e", EXECUTED_SPECS)
    alignments = Aligner(embed=bag_of_words).align(template, executed)
    assert pairs(alignments) == {
        "1": ("1", NUMBER),
        "2": ("3", HEADING),     # renumbered section, found by its caption ...
        "2(a)": ("3(a)", NUMBER),  # ... and its clauses come with it
        "2(b)": ("3(b)", NUMBER),
        "3": ("2", HEADING),
        "4": ("4", NUMBER),
        "4(a)": ("4(a)", NUMBER),  # "Notices" = "Notice"
        "4(b)": (None, None),      # REMOVED
        "4(c)": ("4(b)", HEADING),  # relettered after the removal
        "+4(c)": (None, None),     # ADDED
        "Exhibit A": ("Exhibit B", HEADING),
        "Exhibit B": ("Exhibit A", HEADING),
    }
    assert_each_section_once(alignments, template, executed)
    # template order, with the ADDED clause after the pair of the clause before it
    order = [(a.template_section.section_id if a.template_section else None,
              a.executed_section.section_id if a.executed_section else None)
             for a in alignments]
    assert order[order.index(("4(c)", "4(b)")) + 1] == (None, "4(c)")


RELETTERED_TEMPLATE = [
    ("5", "Conditions Precedent", "Section 5. Conditions Precedent. Before closing:"),
    ("5(a)", "", "(a) The Borrower shall have delivered the executed agreement and the bond."),
    ("5(b)", "", "(b) Counsel shall have rendered legal opinions satisfactory to the lender."),
    ("5(c)", "", "(c) The Borrower shall have paid all invoices for fees and expenses."),
]
RELETTERED_EXECUTED = [  # 5(b) dropped, so 5(c) became 5(b)
    ("5", "Conditions Precedent", "SECTION 5. Conditions Precedent. Before closing:"),
    ("5(a)", "", "(a) The Borrower shall have delivered the executed agreement and the bond."),
    ("5(b)", "", "(b) The Borrower shall have paid all invoices for fees and expenses of its "
                 "advisors."),
]


def test_relettered_clauses_without_captions_pair_by_embedding():
    template, executed = make_doc("t", RELETTERED_TEMPLATE), make_doc("e", RELETTERED_EXECUTED)
    result = pairs(Aligner(embed=bag_of_words).align(template, executed))
    assert result["5(a)"] == ("5(a)", NUMBER)
    assert result["5(b)"] == (None, None)  # different wording: not paired by number
    assert result["5(c)"][0] == "5(b)" and result["5(c)"][1] is EMBEDDING


def test_embedding_match_needs_same_number_or_similar_wording():
    template = make_doc("t", [("6", "Covenants", "Section 6. Covenants."),
                              ("6(a)", "", "(a) Keep the project insured."),
                              ("6(b)", "", "(b) Maintain all permits.")])
    executed = make_doc("e", [("6", "Covenants", "SECTION 6. Covenants."),
                              ("6(a)", "", "(a) Payments fall due monthly."),
                              ("6(c)", "", "(c) Records are kept for five years.")])
    result = pairs(Aligner(embed=same_topic).align(template, executed))
    assert result["6(a)"] == ("6(a)", EMBEDDING)  # same number backs up the topic match
    assert result["6(b)"] == (None, None)         # topic alone is not enough
    assert result["+6(c)"] == (None, None)


def test_threshold_leaves_dissimilar_sections_unmatched():
    template, executed = make_doc("t", RELETTERED_TEMPLATE), make_doc("e", RELETTERED_EXECUTED)
    result = pairs(Aligner(threshold=0.999, embed=bag_of_words).align(template, executed))
    assert result["5(c)"] == (None, None) and "+5(b)" in result


def test_no_model_loaded_when_rules_match_everything():
    template = make_doc("t", TEMPLATE_SPECS)
    alignments = Aligner(embed=no_embeddings).align(template, make_doc("e", TEMPLATE_SPECS))
    assert all(a.method is NUMBER and a.confidence == 1.0 for a in alignments)


def test_missing_sentence_transformers_skips_embedding_pass(monkeypatch, caplog):
    def missing(*args, **kwargs):
        raise ImportError("no sentence_transformers")

    monkeypatch.setattr(aligner_module, "load_embedder", missing)
    template, executed = make_doc("t", RELETTERED_TEMPLATE), make_doc("e", RELETTERED_EXECUTED)
    with caplog.at_level(logging.WARNING):
        result = pairs(Aligner().align(template, executed))
    assert "skipping the embedding pass" in caplog.text
    assert result["5(c)"] == (None, None) and "+5(b)" in result


# --- synthetic fixtures: the planted changes ------------------------------------------

needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")


@needs_template
@pytest.mark.parametrize("fixture", sorted(EXPECTED))
def test_fixture_alignment_reports_exactly_the_planted_structure_changes(fixture):
    _, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(FIXTURES / f"{fixture}.pdf")
    alignments = Aligner(embed=no_embeddings).align(template, executed)
    assert_each_section_once(alignments, template, executed)

    expected = {"added": set(), "removed": set(), "renumbered": set()}
    for change in EXPECTED[fixture]["planted"]:
        if change["change_type"] == "added":
            expected["added"].add(change["executed_section_id"])
        elif change["change_type"] == "removed":
            expected["removed"].add(change["template_section_id"])
        elif change["change_type"] == "renumbered":
            expected["renumbered"].add((change["template_section_id"],
                                        change["executed_section_id"]))
    found = {
        "added": {a.executed_section.section_id for a in alignments if not a.template_section},
        "removed": {a.template_section.section_id for a in alignments if not a.executed_section},
        "renumbered": {(a.template_section.section_id, a.executed_section.section_id)
                       for a in alignments if a.template_section and a.executed_section
                       and a.template_section.section_id != a.executed_section.section_id},
    }
    assert found == expected
    for a in alignments:  # renumbered sections are found by caption, the rest by number
        if a.template_section and a.executed_section:
            renumbered = (a.template_section.section_id, a.executed_section.section_id) \
                in expected["renumbered"]
            assert a.method is (HEADING if renumbered else NUMBER)


# --- a real agreement (needs sentence-transformers and the cached model) -------------

@pytest.fixture(scope="module")
def embedder():
    pytest.importorskip("sentence_transformers")
    try:
        return load_embedder(download=False)
    except OSError:
        pytest.skip("embedding model not downloaded")


@needs_template
@pytest.mark.skipif(not AGREEMENT.exists(), reason="agreement not present")
def test_real_agreement_alignment(embedder):
    """DRLE (2021) against the 2023 project-revenue template: pairs checked by hand."""
    _, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(AGREEMENT)
    alignments = Aligner(embed=embedder).align(template, executed)
    assert_each_section_once(alignments, template, executed)
    result = pairs(alignments)
    expected = {
        "1": ("1", NUMBER),
        "13(l)": ("13(k)", HEADING),            # reps relettered after a dropped 13(k)
        "16(k)": ("10(c)", HEADING),            # "No Defeasance" moved to Section 10
        "Exhibit G": ("Exhibit F", HEADING),    # exhibits relettered
        "Exhibit H-1": ("Exhibit G", HEADING),  # bracketed option in the caption ignored
        "15(g)(i)(D)": ("15(g)(ii)", HEADING),  # notice list flattened one level
        "22(c)": ("22(b)(iii)", HEADING),       # moved under 22(b) "Reporting"
        "22(b)": ("22(b)", EMBEDDING),          # recaptioned, same number
        "12(a)(iii)": ("12(a)(ii)", EMBEDDING),  # conditions precedent relettered
        "12(a)(xxvi)": ("12(a)(xxv)", EMBEDDING),
        "21(c)(ii)": ("21(d)", EMBEDDING),      # promoted one level
        "15(b)(ii)": ("20(d)", EMBEDDING),      # moved to another section
        "4(e)": ("4(f)", EMBEDDING),
        "Exhibit F": (None, None),              # FHWA Oversight Agreement: not in DRLE
        "9(e)": (None, None),                   # Accrual of Amounts on Interim Payment Dates
        "+13(z)": (None, None),                 # "Other Prohibited Transactions" is new
        "+16(b)": (None, None),                 # "Issuance of Future Bonds" is new
    }
    assert {k: result.get(k) for k in expected} == expected
