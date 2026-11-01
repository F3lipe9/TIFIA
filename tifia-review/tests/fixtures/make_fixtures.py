"""Generate synthetic "executed" TLAs from the public borrower template.

Each fixture fills in the cover-page/preamble/interest-rate blanks (expected,
benign differences) and plants exactly one kind of deviation:

    fx_01  ADDED     new clause 4(f), after 4(e) on an inserted page
    fx_02  REMOVED   clause 4(e) deleted
    fx_03  MODIFIED  numbers changed (60 -> 90 days, 30 -> 10 days)
    fx_04  RENUMBERED  Sections 5 and 6 swap numbers
    fx_05  DEFINITION  "Acceptable Credit Rating" thresholds lowered

Writes tests/fixtures/<fixture>.pdf and tests/fixtures/expected_deviations.json, the
ground truth the stage tests check against: per fixture, the planted change(s) and the
filled-in blanks the diff must label fill_in, not report as deviations. Across the five
fixtures that is the six kinds of change of PHASE0 week 1. Run from the repo root:

    python tests/fixtures/make_fixtures.py
"""

import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "data" / "templates" / "TIFIA Public Borrower TLA Template.pdf"
OUT_DIR = Path(__file__).resolve().parent

FIXTURES = {
    "fx_01_added": dict(
        borrower="Metro Regional Transit Authority",
        project="Northgate Corridor Project",
        amount="$245,000,000",
        date="March 15, 2024",
        rate="4.50",
    ),
    "fx_02_removed": dict(
        borrower="Lakeshore Expressway Authority",
        project="Harbor Bridge Replacement Project",
        amount="$180,500,000",
        date="June 28, 2024",
        rate="4.25",
    ),
    "fx_03_modified": dict(
        borrower="Cascade County Transportation District",
        project="Route 9 Managed Lanes Project",
        amount="$312,000,000",
        date="September 10, 2024",
        rate="4.75",
    ),
    "fx_04_renumbered": dict(
        borrower="Red Rock Regional Rail Commission",
        project="Eastside Light Rail Extension",
        amount="$520,000,000",
        date="November 21, 2024",
        rate="4.60",
    ),
    "fx_05_definition": dict(
        borrower="Gulf Coast Port Authority",
        project="Terminal Access Connector Project",
        amount="$96,750,000",
        date="January 30, 2025",
        rate="4.35",
    ),
}

ADDED_CLAUSE = (
    "(f)  The Borrower shall pay to the TIFIA Lender a fee equal to one-quarter of one "
    "percent (0.25%) of the amount of each disbursement of proceeds of the TIFIA Loan, "
    "payable on the date of such disbursement. Such fee shall be in addition to any other "
    "fees and expenses payable pursuant to Section 28 (Fees and Expenses)."
)


SOFT_HYPHEN = "­"
FONT_DIR = Path("C:/Windows/Fonts")
FONT_FILES = {  # (bold, italic) -> (name, file)
    (False, False): ("tnr", "times.ttf"),
    (True, False): ("tnrb", "timesbd.ttf"),
    (False, True): ("tnri", "timesi.ttf"),
    (True, True): ("tnrbi", "timesbi.ttf"),
}


def _font_for(flags):
    return FONT_FILES[(bool(flags & 16), bool(flags & 2))]


def _register_fonts(page):
    for name, fname in FONT_FILES.values():
        page.insert_font(fontname=name, fontfile=str(FONT_DIR / fname))


BASE14 = {  # (bold, italic) -> built-in Times face
    (False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi",
}
_fonts = {}


def _face(ch, flags):
    """(fontname, file). ASCII uses the built-in Times; the embedded TTF's cmap makes
    ';' and '-' extract as U+037E / U+00AD, so it is only used for non-ASCII glyphs."""
    key = (bool(flags & 16), bool(flags & 2))
    if ord(ch) < 128:
        return BASE14[key], None
    name, fname = FONT_FILES[key]
    return name, str(FONT_DIR / fname)


def _font_obj(ch, flags):
    name, path = _face(ch, flags)
    if name not in _fonts:
        _fonts[name] = pymupdf.Font(name) if path is None else pymupdf.Font(fontfile=path)
    return _fonts[name]


def _put(page, point, ch, flags, size):
    """Insert one character; return its advance width."""
    ch = ch.replace(SOFT_HYPHEN, "-").replace(" ", " ")
    name, _ = _face(ch, flags)
    page.insert_text(point, ch, fontname=name, fontsize=size)
    return _font_obj(ch, flags).text_length(ch, fontsize=size)


def rewrite_page(page, edits=(), exact=(), drop_from=None, drop_to=None, center=False):
    """Re-typeset a page span by span so edited text stays inline in reading order.

    edits:     (old, new) substring replacements; each must hit at least once
    exact:     {old: new} for spans whose whole stripped text equals old (safe for swaps)
    drop_from/drop_to: drop every line from the one equal to drop_from through the
               one containing drop_to
    center:    keep edited spans centered on their original position (cover page)
    """
    spans, dropping, hits = [], False, {old: 0 for old, _ in edits}
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                s["text"] = "".join(c["c"] for c in s["chars"])
            text = "".join(s["text"] for s in line["spans"])
            if drop_from and text.strip().startswith(drop_from):
                dropping = True
            if dropping:
                if drop_to in text:
                    dropping = False
                continue
            for s in line["spans"]:
                if not s["text"].strip():
                    continue
                new = s["text"]
                if new.strip() in exact:
                    new = new.replace(new.strip(), exact[new.strip()])
                else:
                    for old, rep in edits:
                        if old in new:
                            hits[old] += 1
                            new = new.replace(old, rep)
                spans.append((s, new))
    missing = [o for o, n in hits.items() if n == 0]
    if missing or (drop_from and dropping):
        raise ValueError(f"page {page.number + 1}: edit not applied: {missing or drop_from}")

    page.add_redact_annot(page.rect)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    _register_fonts(page)
    for s, new in spans:
        if new == s["text"]:
            # justified text has per-word spacing, so place every character where it was
            for c in s["chars"]:
                _put(page, c["origin"], c["c"], s["flags"], s["size"])
            continue
        x, y = s["origin"]
        if center:
            width = sum(_font_obj(ch, s["flags"]).text_length(ch, fontsize=s["size"])
                        for ch in new.strip())
            x = (s["bbox"][0] + s["bbox"][2]) / 2 - width / 2
        for ch in new.replace(SOFT_HYPHEN, "-"):
            x += _put(page, (x, y), ch, s["flags"], s["size"])


def queue(plan, idx, edits=(), exact=None, **kw):
    """Collect edits per page so each page is re-typeset exactly once."""
    entry = plan.setdefault(idx, dict(edits=[], exact={}))
    entry["edits"].extend(edits)
    entry["exact"].update(exact or {})
    entry.update(kw)


def fill_blanks(plan, f):
    queue(plan, 0, center=True, edits=[
        ("[Maximum Principal Amount of Loan]", f["amount"]),
        ("[BORROWER]", f["borrower"]),
        ("[__________PROJECT]", f["project"]),
        ("[_________________]", f["date"]),
    ])
    # the preamble splits "[BORROWER]" across three spans
    queue(plan, 3, exact={"BORROWER": f["borrower"]},
          edits=[("between [", "between "), ("], a [", ", a [")])
    queue(plan, 36, edits=[("[___] percent ([__]%)", f"{f['rate']} percent ({f['rate']}%)")])


def copy_lines(src, dst, first, last):
    """Re-typeset src's lines from the one starting with `first` through the one containing
    `last` onto dst, at the same positions. Returns the bottom y of the last line."""
    copying = False
    for block in src.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(c["c"] for s in line["spans"] for c in s["chars"])
            copying = copying or text.strip().startswith(first)
            if not copying:
                continue
            for s in line["spans"]:
                if "".join(c["c"] for c in s["chars"]).strip():
                    for c in s["chars"]:
                        _put(dst, c["origin"], c["c"], s["flags"], s["size"])
            if last in text:
                return line["bbox"][3]
    raise ValueError(f"page {src.number + 1}: lines {first!r}..{last!r} not found")


def add_clause_page(doc):
    """Insert page "33a" holding clause 4(e), moved off page 34 by plant_added, and the new
    clause 4(f) right after it, so 4(f) sits between 4(e) and Section 5 in reading order.
    (A page inserted after page 34 would land in the middle of Section 7(a).)"""
    with pymupdf.open(TEMPLATE) as template:
        src = template[36]
        page = doc.new_page(pno=36, width=src.rect.width, height=src.rect.height)
        page.insert_text((290, 50), "33a", fontname="tiro", fontsize=12)
        _register_fonts(page)
        bottom = copy_lines(src, page, first="(e)", last="appropriations by Congress")
    page.insert_textbox(
        pymupdf.Rect(72, bottom + 12, page.rect.width - 72, bottom + 240),
        ADDED_CLAUSE,
        fontname="tiro",
        fontsize=12,
    )


def plant_added(plan):
    queue(plan, 36, drop_from="(e)", drop_to="appropriations by Congress")  # moved to 33a
    return dict(change_type="added", template_section_id=None, executed_section_id="4(f)",
                template_page=None, executed_page=37, template_text="",
                executed_text=ADDED_CLAUSE, substantive=True)


def plant_removed(plan):
    queue(plan, 36, drop_from="(e)", drop_to="appropriations by Congress")
    return dict(change_type="removed", template_section_id="4(e)", executed_section_id=None,
                template_page=37, executed_page=None,
                template_text="Notwithstanding anything to the contrary set forth in this "
                "Agreement (including this Section 4, Section 12 (Conditions Precedent) or "
                "Exhibit D (Requisition Procedures)), in no event shall the TIFIA Lender have "
                "any obligation to make any disbursement of proceeds of the TIFIA Loan to the "
                "Borrower if the TIFIA Lender's ability to make such disbursement is impaired "
                "as a result of a partial or total shutdown of the operations of any federal "
                "department or agency [...] due to a lapse in appropriations by Congress.",
                executed_text="", substantive=True)


def plant_modified(plan):
    queue(plan, 7, edits=[("sixty (60)", "ninety (90)")])
    queue(plan, 72, edits=[("thirty (30)", "ten (10)")])
    return [
        dict(change_type="modified", template_section_id="1", executed_section_id="1",
             affects_defined_term="Bankruptcy Related Event",
             template_page=8, executed_page=8,
             template_text="shall continue undismissed for sixty (60) days",
             executed_text="shall continue undismissed for ninety (90) days",
             detail="definition of Bankruptcy Related Event, clause (a)", substantive=True),
        dict(change_type="modified", template_section_id="16(b)", executed_section_id="16(b)",
             template_page=73, executed_page=73,
             template_text="at least thirty (30) days prior to the effective date thereof",
             executed_text="at least ten (10) days prior to the effective date thereof",
             substantive=True),
    ]


def plant_renumbered(plan):
    queue(plan, 36, exact={"Section 5.": "Section 6.", "Section 6.": "Section 5."})
    return [
        dict(change_type="renumbered", template_section_id="5", executed_section_id="6",
             heading="Term", template_page=37, executed_page=37,
             template_text="Section 5. Term.", executed_text="Section 6. Term.",
             substantive=False),
        dict(change_type="renumbered", template_section_id="6", executed_section_id="5",
             heading="Interest Rate", template_page=37, executed_page=37,
             template_text="Section 6. Interest Rate.",
             executed_text="Section 5. Interest Rate.", substantive=False),
    ]


def plant_definition(plan):
    queue(plan, 4, edits=[
        ("‘A+’, ‘A1’", "‘A-’, ‘A3’"),
        ("‘A’, ‘A2’", "‘BBB+’, ‘Baa1’"),
    ])
    return dict(change_type="modified", template_section_id="1", executed_section_id="1",
                affects_defined_term="Acceptable Credit Rating",
                template_page=5, executed_page=5,
                template_text="‘A+’, ‘A1’ ... ‘A’, ‘A2’",
                executed_text="‘A-’, ‘A3’ ... ‘BBB+’, ‘Baa1’",
                substantive=True)


PLANTERS = {
    "fx_01_added": plant_added,
    "fx_02_removed": plant_removed,
    "fx_03_modified": plant_modified,
    "fx_04_renumbered": plant_renumbered,
    "fx_05_definition": plant_definition,
}


def expected_fill_ins(name, f):
    """The two blanks filled inside agreement sections (the cover page belongs to no
    section): the borrower in the preamble and the rate in Section 6 (Interest Rate),
    which fx_04 numbers 5 and fx_01's inserted page pushes to page 38."""
    return [
        dict(change_type="fill_in", template_section_id="preamble",
             executed_section_id="preamble", template_page=4, executed_page=4,
             template_text="by and between [BORROWER], a [_________] created",
             executed_text=f"by and between {f['borrower']}, a [_________] created",
             substantive=False),
        dict(change_type="fill_in", template_section_id="6",
             executed_section_id="5" if name == "fx_04_renumbered" else "6",
             template_page=37, executed_page=38 if name == "fx_01_added" else 37,
             template_text="shall be [___] percent ([__]%) per annum",
             executed_text=f"shall be {f['rate']} percent ({f['rate']}%) per annum",
             substantive=False),
    ]


def main():
    truth = {}
    for name, fields in FIXTURES.items():
        doc = pymupdf.open(TEMPLATE)
        plan = {}
        fill_blanks(plan, fields)
        planted = PLANTERS[name](plan)
        for idx, kw in plan.items():
            rewrite_page(doc[idx], **kw)
        if name == "fx_01_added":  # after rewrites: the inserted page shifts indices
            add_clause_page(doc)
        doc.save(OUT_DIR / f"{name}.pdf", garbage=3, deflate=True)
        doc.close()
        truth[name] = dict(
            filled_fields=fields,
            planted=planted if isinstance(planted, list) else [planted],
            fill_ins=expected_fill_ins(name, fields),
        )
        print("wrote", name)
    (OUT_DIR / "expected_deviations.json").write_text(
        json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
