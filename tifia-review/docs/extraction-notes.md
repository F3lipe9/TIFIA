# Extraction notes (Phase 0, week 2)

Text dumps: `python scripts/inspect_documents.py` writes every manifest document to
`outputs/inspect/<doc_id>.txt`, one block per page with its PDF index, printed label and
review flag. Findings below are from those dumps and from building weeks 3-5 on them
(last checked 2026-10-31). Where each problem is handled is noted in brackets.

## Checks

**Reading order.** PyMuPDF's default order is right for the running text of all
documents (single column). Exceptions:
- The printed page number at the bottom of each page comes out as the page's first line
  (Word writes the footer first). [loader: page labels are read from the first lines]
- Tables (project budgets, amortization schedules, the disbursement schedule) come out
  cell by cell. They sit in schedules and exhibits, which are compared as whole sections.
- The non-project template prints "Section 14." on the Patriot Act paragraph (13(cc) lost
  its number in Word) and runs the real Section 14 caption into its last line. This is in
  the PDF itself, not an extraction error. [segmenter: reported by the TOC self-check]

**Ligatures.** None found ("ﬁ", "ﬂ"). [normalizer maps them anyway (NFKC)]

**Hyphenation.** No document breaks words across lines. Every hyphen at a line end is part
of a compound word ("well-being", "Semi-Annual", "non-appealable", "Anti-Corruption").
[normalizer drops the line break and keeps the hyphen]

**Headers and footers.**
- 2021 agreements: two running header lines on every page, "AMERICAS/<document number>"
  and "ST <project> Loan Agreement" (e.g. "ST DRLE Loan Agreement"). [loader removes
  header-zone lines that repeat on at least 30% of pages]
- Omnibus amendment: running header "Omnibus Amendment". [loader, same rule]
- Templates: no running header; page numbers only.
- I-81 drafts: "ERRATA COPY/VERSION" printed at the **top** of every page, but PyMuPDF
  returns it as the page's **last** line, so the first-lines rule missed it (it caused 16
  false deviations on I-81 Regular body: 513 -> 497). The one-off cover stamp
  "NRF Draft 10/14/2022" is kept.
- HRTAC draft: no printed page numbers in the body. The only footer is a package stamp,
  "Exhibit J-5" on pp. 7-107 and "Exhibit I - 1" on pp. 4 and 6, which was being read as
  the page label. Citations now fall back to "PDF p. N".
- [loader, since Nov 7: margin text is found **by position**, not extraction order. A
  line whose centre is in the top or bottom 8% of the page is margin text (headers end
  above ~40pt, footers start below ~727pt, body text spans ~70-721pt of 792). Margin lines
  repeating on at least 30% of pages are removed, by shape (digits masked) or, for a
  label-like line, by exact text (a constant "label" is a stamp, not a page number); a
  malformed variant of a stamp ("Exhibit I - 1") is removed too. The page number or exhibit
  label nearest the page edge becomes printed_label. One-off margin text ("EXECUTION
  VERSION", "[Signature page to ...]", the "TIFIA Loan Amortization Schedule" table
  title) stays in the text. Pages with nothing in either margin keep the old first-lines
  rule. Output for the templates, fixtures and Sound Transit agreements is unchanged.]
- Layout notes in the text: "[The remainder of this page intentionally left blank]",
  "[Signature page follows]", and in the template's Exhibit H-2 "[Do not delete - this
  paragraph generates the automatic page number]". [normalizer drops these lines]

**Footnotes (templates).** About 160 drafting notes per template at the bottom of pages,
some continued with "(cont'd from previous page)"; a few contain their own "(c)", "(iii)"
lines (e.g. footnote 70 on alternative clauses 4(c)-(e)). Their numbers are glued to the
body text ("Definitions.4", "[at least one (1)]5", "Material Adverse Effect125").
Agreements have one footnote (DRLE Exhibit D, Appendix Two). [segmenter cuts each page's
footnote area and removes only that page's footnote numbers from the body, so "1.50",
"$520,981,378" and the rating codes "A3", "Baa1" are kept]

**Page labels.** Detected: arabic and roman numbers, "34a" (fixture), "Exhibit A-4"
(templates), "A-1" (agreements). Missed until 2026-10-31, now detected: "Schedule III-1",
"Schedule 13(f)-1" (templates) and "Sch. I", "Sch. 13(o)" (2021 agreements). Pages with
no printed label: the cover and the first body page (templates, fixtures); the signature
pages and the TIFIA amortization schedule pages of Exhibit F (2021 agreements); cover,
table of contents and signature pages of the omnibus amendment.

**Scanned pages.** No document is scanned (`is_scanned` is False for all). Pages under
50 characters, all checked:

| Document | Pages | What they are |
|---|---|---|
| Project-revenue template | 101, 121, 122, 125, 129, 144 | Schedule 13(f), Exhibits F, G, I, Annex One to Exhibit J, Annex One: headings only, contents "to be provided" |
| Non-project template | 90, 91, 111, 112, 118, 133 | Schedule IV, Schedule 13(f), Exhibits F, G, Annex One to Exhibit I, Annex One: same |
| DRLE | 94, 95 / 120 | signature pages with no text layer (images) / Exhibit F "[Attached.]" |
| Federal Way | 95, 96 / 121 / 141 | signature pages with no text layer / Exhibit F "[Attached.]" / Exhibit J signature block |
| Northgate | 95, 96 / 107 / 122 | signature pages with no text layer / "[Signature page follows]" / Exhibit F "[Attached.]" |
| O&M Facility East | 95, 96 / 121 / 139 | signature pages with no text layer / Exhibit F "[Attached.]" / Exhibit I heading page |
| Fixtures | as the template, shifted by one after page 36 in fx_01 | same pages as the template |

**Other characters.** Curly quotes and apostrophes throughout (the template writes ‘A+’
where agreements write “A+”), en and em dashes, a narrow no-break space, "●" in template
blanks, and a private-use bullet glyph (U+F097) in all four 2021 agreements. [normalizer: one quote mark,
"-" for dashes, NFKC, private-use glyphs dropped]

## Known limits

- The signature pages of the 2021 agreements have no text, so the signature blocks cannot
  be compared; the diff skips signature blocks altogether.
- Tables are compared as text in reading order, so a reformatted table can show up as a
  change even when the numbers are the same.
