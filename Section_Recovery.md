# Plan: TOC-Guided Section Recovery

**Date:** November 1, 2026  **Owner:** Felipe  **Estimate:** ~6 hours (about 1.5 weeks at 5 hrs/week)
**Problem:** Some PDFs lose their section numbers in extraction. In the I-81 Regular draft, 19 of 39 headings have no "Section N." and are glued to the end of the previous paragraph:

```
...signed by a duly authorized representative of such party.TIFIA Loan Amount. The principal amount...
```

The segmenter merges those sections into their neighbors, which causes false deviations and wrong citations downstream.

**Fix:** When headings are missing, read the section titles from the document's table of contents (or from the template), then search forward through the body for each missing title and split there.

---

## 1. Where it fits

```
Load ─> Segment ─> StructureCheck ──(complete)──────────────────────────> Align ─> Diff ...
                        │
                        └──(missing sections)─> TocParser ─> HeadingRecovery ─> StructureCheck again
                                                                                     │
                                                         (still missing) ─> mark DEGRADED, continue
```

Recovery runs **only when the first segmentation is incomplete**. Documents that segment cleanly (template, HRTAC, I-81 Rural) are untouched.

---

## 2. Rules

1. **Repair, don't replace.** Keep every heading the segmenter found. Only add missing ones.
2. **Search forward only, in TOC order.** Each title is searched for after the previous section's start, and before the next section the segmenter already found.
3. **Match the heading form.** Title + period, not inside parentheses, not preceded by "Section N (".
4. **Never search inside the TOC.** Start after the TOC pages.
5. **Never lose text.** Every character of the body belongs to exactly one section.
6. **Never fail silently.** Anything not recovered is reported as degraded.

---

## 3. Components

| Class | File | Responsibility |
|---|---|---|
| `TocParser` | `segment/toc.py` | Find the TOC pages and parse entries into `(section_id, title)`, including titles that wrap onto a second line |
| `StructureCheck` | `segment/structure.py` | Compare found sections with the expected list; check text coverage; return complete / incomplete / degraded |
| `HeadingRecovery` | `segment/recovery.py` | Search forward for missing titles, split sections, re-run subsection parsing inside new sections |
| `TitleNormalizer` | `segment/titles.py` | Shared normalization so TOC titles and body text compare equally |

### Data model additions (`models.py`)

```python
@dataclass
class TocEntry:
    section_id: str          # "3"
    title: str               # "TIFIA Loan Amount"
    toc_page: int            # pdf_index where the entry appears

class BoundarySource(Enum):
    NUMBERED = "numbered"            # found by the normal segmenter
    TOC_TITLE = "toc_title"          # recovered using the document's TOC
    TEMPLATE_TITLE = "template_title"  # recovered using the template's titles

@dataclass
class StructureReport:
    expected: int
    found: int
    recovered: list[str]             # section IDs recovered
    missing: list[str]               # still not found
    text_coverage: float             # section chars / body chars
    status: str                      # "complete" | "recovered" | "degraded"
```

Add `boundary_source: BoundarySource` to `Section`, so reports and the evaluator can tell recovered sections apart.

---

## 4. `TocParser`

**Find the TOC:** the first pages (in the first ~10% of the document) where most lines end in dot leaders and a page number:

```
Section 3.    TIFIA Loan Amount ..............................................................34
```

**Parse each entry:** `^Section\s+(\d+)\.\s+(.+?)\s*\.{3,}\s*\d+$`

**Wrapped titles:** some entries span two lines, for example:

```
Section 7.    Outstanding TIFIA Loan Balance; Revisions to Exhibit G and Loan
              Amortization Schedule.................................................34
```

If a `Section N.` line has no dot leaders, join it with the following line(s) until one ends in leaders and a page number.

**Output:** ordered `list[TocEntry]` plus the page index where the TOC ends (`toc_end`).

**Fallback:** if no TOC is found, or it has fewer than 80% of the template's section count, use the **template's section list** (`TEMPLATE_TITLE`). Template titles may differ slightly from the agreement's, so this fallback catches less; see section 9.

---

## 5. `TitleNormalizer`

Applied to both TOC titles and body text before matching:

- Collapse all whitespace, including line breaks, to single spaces. Body headings wrap too: `"Accounting and Audit Procedures; Inspections; Reports and\nRecords."`
- Curly quotes and apostrophes → straight (`Borrower’s` → `Borrower's`)
- En/em dashes → hyphen
- Remove a trailing period from TOC titles (the period is added back in the match pattern)

**Important:** normalize a copy for searching, but keep a **character offset map** back to the original text, so the split happens at the right position in the original and page citations stay correct through the existing `PageMap`.

---

## 6. `HeadingRecovery`

### Algorithm

```
expected = TOC entries in order
found    = sections from the segmenter, keyed by section_id
cursor   = position where the TOC ends

for each entry in expected:
    if entry already found:
        cursor = found[entry].start
        continue
    limit = start of the next section that was already found (or end of body)
    pos   = find_heading(normalized_text, entry.title, start=cursor, end=limit)
    if pos found:
        record boundary (entry.section_id, pos, source=TOC_TITLE)
        cursor = pos
    else:
        add entry to missing

rebuild sections from all boundaries, sorted by position
re-run subsection parsing ((a), (i), (A)) inside each recovered section
```

### Matching a heading

```python
def find_heading(text: str, title: str, start: int, end: int) -> int | None:
    pattern = re.compile(
        rf"(?<![\(\w])"                 # not inside "(" and not mid-word
        rf"(?:Section\s+\d+\.\s+)?"     # number may or may not be present
        rf"{re.escape(title)}\."        # title followed by a period
        rf"(?=\s|[A-Z\"“(])"           # then a space, or the glued next sentence
    )
    m = pattern.search(text, start, end)
    return m.start() if m else None
```

**Why the `limit`:** bounding each search by the next already-found heading means a recovered section can never jump past a section that's known to come after it. That's the main protection against false matches.

**Ambiguity:** if a title occurs more than once inside the search window, take the **first**, and log a warning with both positions so the hand-check can confirm it.

### Splitting glued text

When the title is glued to the end of the previous paragraph (`party.TIFIA Loan Amount.`), the split happens at the start of the title:
- The previous section's text ends at `party.`
- The new section's heading is `TIFIA Loan Amount`, and its text starts after `Amount.`

---

## 7. `StructureCheck`

| Check | Rule |
|---|---|
| Section coverage | `found / expected` (expected = TOC count, or template count) |
| Order | Recovered section numbers must be increasing in document order |
| Text coverage | Sum of section text ÷ body text after the TOC, target ≥ 98% |
| Duplicates | No section ID appears twice |

**Status:**
- `complete`: everything found by the normal segmenter
- `recovered`: everything found, some by recovery
- `degraded`: coverage below 95%, or any order/duplicate violation

**When degraded:**
- The report cover shows **"Document structure partly unrecovered: N sections could not be located. Citations for nearby changes may be imprecise."**
- The evaluator excludes the document from scoring by default (add a `--include-degraded` flag).
- The missing sections' text stays in the neighboring section, so nothing is lost; the aligner's embedding pass may still pair it.

Write the `StructureReport` to `outputs/<run_id>/structure.json` and print a one-line summary in the CLI:

```
tla_2022_ctb_i81_regular_draft: 39/39 sections (20 numbered, 19 recovered from TOC), coverage 99.6%
```

*(Expected output; to be confirmed when it runs.)*

---

## 8. Config (`config.yaml`)

```yaml
segmentation:
  recovery:
    enabled: true
    min_section_coverage: 0.95
    min_text_coverage: 0.98
    toc_search_fraction: 0.10     # look for the TOC in the first 10% of pages
    toc_min_entries_ratio: 0.80   # below this, fall back to template titles
```

---

## 9. Edge cases

| Case | Handling |
|---|---|
| Title appears in a cross-reference: `Section 36 (Notices; Payment Instructions)` | Excluded by the parenthesis rule and the bounded search window |
| Title appears as an ordinary phrase: `the Interest Rate shall` | Excluded by requiring a period right after the title |
| Agreement renamed a section vs. its TOC | TOC and body come from the same document, so they usually match. If not, the section ends up in `missing` → degraded |
| No TOC | Template titles. Agreements often rename sections (HRTAC §21 "Financial Plan, Semi-Annual Coverage Certificates; Statements…" vs. template "Financial Plan; Semi-Annual…"), so expect lower recovery. A fuzzy-title step can be added later if needed |
| Sections added by the agreement (not in the template) | Only an issue with the template fallback; they stay inside the previous section until a later fix |
| Subsection labels missing, e.g. `Prepayment.Mandatory Prepayments.` with no "(a)" | Out of scope. The sentence diff still finds changes; citations show §10 instead of §10(a). Listed as a known limitation |
| Heading split across a page break | The joined text plus whitespace normalization handles it |

---

## 10. Tests

| # | Test | Expected |
|---|---|---|
| 1 | Clean fixture | Recovery not triggered; output identical to today |
| 2 | Fixture with two "Section N." labels removed and headings glued to the prior line | Both recovered; `boundary_source = TOC_TITLE` |
| 3 | Cross-reference trap: body contains `Section 36 (Notices; Payment Instructions)` early | No split there |
| 4 | Plain-phrase trap: `the TIFIA Loan Amount shall` | No split there |
| 5 | Wrapped TOC entry | Parsed as one title |
| 6 | Wrapped body heading | Found after whitespace normalization |
| 7 | Title genuinely absent | Listed in `missing`; status `degraded`; no text lost |
| 8 | Text coverage | Section text within 2% of body text after the TOC |
| 9 | Citations | A recovered section's page matches where its title appears in the PDF |
| 10 | **Regression: I-81 Regular body** | 39 sections, matching its TOC; status `recovered` |
| 11 | **No-change check: template, HRTAC, I-81 Rural** | Status `complete`; section lists unchanged from before this feature |

Build the fixtures for tests 2–7 from your existing fixture PDFs, so they go through the real loader.

---

## 11. Schedule (~6 hours)

| Step | Task | Hrs |
|---|---|---|
| 1 | Data model + config | 0.5 |
| 2 | `TitleNormalizer` with offset map + tests | 1 |
| 3 | `TocParser`, including wrapped entries + tests | 1.5 |
| 4 | `HeadingRecovery` + trap tests | 1.5 |
| 5 | `StructureCheck`, CLI line, report warning, evaluator exclusion | 1 |
| 6 | Run on all documents; hand-check the 19 recovered I-81 Regular boundaries | 0.5 |

---

## 12. Scoring rule

Because this feature is tuned on the I-81 Regular draft, **don't use that document in your accuracy score**. Use it as a robustness test only. The held-out agreement stays untouched.

---

## 13. Done when

- I-81 Regular segments into 39 sections matching its TOC, with every recovered boundary hand-checked
- Template, HRTAC, and I-81 Rural segment exactly as before
- Every run writes `structure.json` and prints the one-line summary
- Degraded documents are flagged on the report and excluded from scoring
- All 11 tests pass