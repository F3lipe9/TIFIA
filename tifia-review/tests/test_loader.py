from pathlib import Path

import pymupdf
import pytest

from tifia_review.loader import DocumentLoader, find_printed_label, split_header

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fx_01_added.pdf"
AGREEMENT = (ROOT / "data" / "agreements"
             / "federal-way-link-extension-project-TIFIA-loan-agreement-executed-9.10.21.pdf")
BODY = "body line one\nbody line two\nbody line three\nbody line four\n"


def make_pdf(path: Path, pages: list[str]) -> str:
    """A PDF with one page per text ("" leaves the page blank)."""
    with pymupdf.open() as pdf:
        for text in pages:
            page = pdf.new_page()
            if text:
                page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), text, fontsize=11)
        pdf.save(path)
    return str(path)


PROSE = ("The Borrower shall maintain its existence and comply with all applicable laws, "
         "rules and regulations in connection with the Project. ") * 3


def test_clean_pdf_is_not_scanned(tmp_path):
    doc = DocumentLoader().load(make_pdf(tmp_path / "clean.pdf", [PROSE] * 4), "clean", "x")
    assert doc.is_scanned is False
    assert not [p for p in doc.pages if p.needs_review]


def test_mostly_blank_pdf_is_scanned(tmp_path):
    doc = DocumentLoader().load(make_pdf(tmp_path / "blank.pdf", [PROSE, "", "", ""]),
                                "blank", "x")
    assert doc.is_scanned is True
    assert [p.pdf_index for p in doc.pages if p.needs_review] == [2, 3, 4]


@pytest.mark.parametrize("text,expected", [
    ("\n34 \nbody text", "34"),
    ("i \nTABLE OF CONTENTS", "i"),
    ("AMERICAS/2021.6\nii\nbody", "ii"),
    ("Exhibit A-4 \nIN WITNESS", "Exhibit A-4"),
    ("34a\n(f) clause", "34a"),
    ("TIFIA LOAN AGREEMENT\nTHIS TIFIA", None),
    ("civil\nprocedure applies", None),           # roman-numeral letters, not a numeral
    (BODY + "12", "12"),                          # page number as the last line
    (BODY + "1,250\n12", None),                   # last cell of a column of numbers
    ("Schedule III-1 \nSCHEDULE III", "Schedule III-1"),
    ("Schedule 13(f)-1 \nSCHEDULE 13(f)", "Schedule 13(f)-1"),
    ("Sch. 13(o) \nSCHEDULE 13(r)", "Sch. 13(o)"),
])
def test_printed_label(text, expected):
    assert find_printed_label(text) == expected


def test_label_on_last_line_is_removed_from_the_text():
    assert split_header(BODY + "12\n", set()) == ("12", BODY)


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not generated")
def test_load_fixture_pages():
    doc = DocumentLoader().load(str(FIXTURE), "fx_01_added", "executed")
    assert len(doc.pages) == 148
    assert [p.pdf_index for p in doc.pages[:3]] == [1, 2, 3]
    assert doc.pages[1].printed_label == "i"
    assert doc.pages[3].printed_label is None  # no printed number on the first body page
    assert doc.pages[36].printed_label == "33a"  # the inserted page with the planted clause
    assert doc.is_scanned is False  # few low-text pages


@pytest.mark.skipif(not AGREEMENT.exists(), reason="agreement not present")
def test_blank_pages_flagged_but_document_not_scanned():
    doc = DocumentLoader().load(str(AGREEMENT), "tla_2021_federal_way", "executed")
    # 95-96 are signature pages with no text layer; 121 is Exhibit F ("[Attached.]") and
    # 141 the Exhibit J signature block
    assert [p.pdf_index for p in doc.pages if p.needs_review] == [95, 96, 121, 141]
    assert doc.is_scanned is False
    strict = DocumentLoader(max_low_text_fraction=0.0).load(str(AGREEMENT), "x", "executed")
    assert strict.is_scanned is True


@pytest.mark.skipif(not AGREEMENT.exists(), reason="agreement not present")
def test_running_header_removed_but_label_kept():
    doc = DocumentLoader().load(str(AGREEMENT), "tla_2021_federal_way", "executed")
    page = doc.pages[5]
    assert page.printed_label == "2"
    assert "AMERICAS/" not in page.text
    assert "ST Federal Way Loan Agreement" not in page.text
    assert page.text.lstrip().startswith("NOW, THEREFORE")


def test_split_header_only_touches_header_zone():
    running = {"AMERICAS/##########.#", "ST Federal Way Loan Agreement"}
    text = ("AMERICAS/2021320676.6 \n2  \nST Federal Way Loan Agreement \nbody line\n"
            "ST Federal Way Loan Agreement \n")
    label, body = split_header(text, running)
    assert label == "2"
    assert body == "body line\nST Federal Way Loan Agreement \n"
