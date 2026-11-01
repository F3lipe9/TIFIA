# Plan: 2-Page Executive Summary PDF (v2)

**Date:** November 1, 2026  **Owner:** Felipe  **Phase:** 0 (ready before week-9 demos)
**Reader:** Agency or client executive: plain English, minimal legal detail
**Output:** `outputs/reports/<doc_id>_summary.pdf`, produced alongside the full deviation report
**Layout:** one row per topic. Each row has a plain-English summary **and** whether that topic was changed from the standard template.

**Changes from v1:** topic list set by Felipe; one-row-per-topic layout instead of separate terms and deviation pages; borrowing authority covers both statute and resolution; Exhibit G (payment schedule) is now in scope.

---

## 1. Decisions and concerns

1. **The reader doesn't match the Phase 0 customer.** Phase 0 sells to advisors. Present the summary as **a deliverable the advisor hands to their client**, and ask advisors in demos whether they'd send it.
2. **Describe, never advise.** Rows say what the agreement says ("Interest is fixed at 4.12%"), never what to do. Footer on both pages: **"Prototype — prepared for professional review. Not legal or financial advice."**
3. **Your topic list leaves out the two most negotiated areas.** Covenants (§15–16) and security/what's pledged (§8) aren't on the list, but in HRTAC they're where most of the extra text is (§15 runs ~18 pages vs. 12 in the template). An executive would want to know what revenue backs the loan. **Plan: add one "Other significant changes" row** listing the top substantive deviations outside your nine topics, so covenant and security changes aren't hidden. Drop it if you disagree.
4. **New extraction has no accuracy check.** The advisor's answer keys don't cover it. Felipe hand-verifies every row on 2 agreements (each fact has a quote and page, so it's fast). The advisor's labeling work doesn't change.
5. **Not in the go/no-go gate.** ~13 hours of work, which pushes Felipe's remaining Phase 0 work to ~28–33 hours. It must not delay the answer keys, taxonomy meeting, or interviews.
6. **Held-out agreement:** generate its summary only after its held-out score is recorded.

---

## 2. Design rules

1. **Every fact cites its source:** section and page, or deviation ID.
2. **Every LLM-written line has a verified quote** that appears word-for-word in the agreement. A failed check shows **"See agreement §X"** instead of a summary.
3. **No guessing.** Missing, blank, or not-yet-inserted items are reported as such.
4. **Code decides which deviations matter and which topic they belong to.** The LLM only writes the plain-English lines.
5. **Hard 2-page limit**, enforced by code.

---

## 3. The summary rows

Section numbers are from the 2023 project-revenue template. The code finds the matching executed sections through the existing alignment, so renumbered agreements still work.

| # | Row | What it says | Where it comes from |
|---|---|---|---|
| — | **Header** | Borrower, lender, project name, agreement date or **"Draft"**, template compared against | Cover, preamble |
| — | **Bottom line** | 2–3 sentences: overall how close to the template, and where the main changes are | LLM, from the topic rows' change counts only |
| 1 | **The project** | What is being built or financed, in one or two lines | Recitals; "Project" definition (§1) |
| 2 | **Borrowing authority** | (a) The state law that lets the borrower borrow and pledge revenue; (b) the resolution authorizing this loan | (a) §1 definitions + §13 "Organization; Power and Authority"; (b) §1 and §12 Conditions Precedent *(verify on HRTAC where the resolution is named)* |
| 3 | **Loan amount** | Maximum principal amount | Recitals; §3 TIFIA Loan Amount |
| 4 | **Interest rate** | Rate and how it's set | §6 Interest Rate |
| 5 | **Payment schedule** | Payment frequency, repayment start, final maturity (§9 and §1); plus a schedule digest from Exhibit G (section 4 below) | §9, §1, **Exhibit G** |
| 6 | **Events of default and remedies** | Main default triggers (2–3 bullets) and what the lender can do | §19 |
| 7 | **Accounting and audit** | What records, audits, and inspection rights are required | §20 |
| 8 | **Project oversight and monitoring** | Who oversees the project and what's reported | §22 |
| 9 | **Notices and termination** | How formal notices are given; when the agreement ends | §36, §38 |
| 10 | **Other significant changes** *(recommended addition)* | Top 3 substantive deviations outside rows 1–9 | Deviation list |
| — | **Footer** | Totals (deviations, substantive, needs review), pointer to the full report, disclaimer | Deviation list |

### Each row has three parts

| Part | Example | Produced by |
|---|---|---|
| **Summary** | "Fixed rate equal to the Treasury rate on the signing date; draft shows [__]%." | LLM, with verified quote |
| **Changed from template** | "Standard" · or · "2 substantive changes — D-031, D-044" | **Code**, from deviations whose section maps to this topic |
| **Source** | "§6, p. 36" | Code |

### Value states

| State | Shown as |
|---|---|
| Found and verified | Summary + citation |
| Blank in a draft (`$[310,194,800]`, `[____]`) | Value as written + **"(draft, not final)"** |
| Not yet inserted (`[to be inserted]`) | **"Not yet inserted (draft)"** |
| Not found or quote failed | **"See agreement §X"** |

---

## 4. Exhibit G — payment schedule digest

The full debt service table won't fit and isn't what an executive needs. Show a **digest**:

| Item | Example |
|---|---|
| First payment date | 2031-07-01 |
| Final payment date | 2060-07-01 |
| Number of payments, frequency | 60, semi-annual |
| Total principal scheduled | $310,194,800 |
| Largest annual payment | $18.2M (FY 2045) |

**Cross-check (deterministic):** the sum of the principal column should equal the loan amount from row 3. If it doesn't, show **"Schedule total doesn't match loan amount — see Exhibit G"** instead of the digest. This catches table-parsing errors.

### How Exhibit G gets into the pipeline

Phase 0 compares the **body only**, and Exhibit G is outside the body file. So:

1. **Manifest:** add a `full_path` column pointing to the agreement **with** exhibits (e.g. `reference/tla_2025_hrtac_draft_full.pdf`). Body-only diffing doesn't change.
2. **`ExhibitLocator`:** search the full PDF for the heading "EXHIBIT G" + "TIFIA DEBT SERVICE" and return its pages. Optional manifest override `exhibit_g_pages` if auto-detection fails.
3. **`ScheduleParser`:** extract the table with PyMuPDF's `find_tables()`; identify date, principal, interest, and total columns by header text; parse dates and dollar amounts.

**Expected results on current documents:**
- **HRTAC draft:** Exhibit G says "[to be inserted]", so the row shows "Not yet inserted (draft)" plus the §9 facts.
- **Sound Transit agreements:** unknown; check whether their PDFs include Exhibit G and whether it's a real table or a scanned image.

**Risk:** debt service tables vary widely (multi-page, merged headers, footnotes, scanned). If parsing fails, the row falls back to §9 facts plus "Full schedule: Exhibit G, p. X." Never show a partially parsed schedule.

---

## 5. Mapping deviations to topics (code, not LLM)

```yaml
summary:
  topics:
    project:            {sections: ["recitals", "1:Project"]}
    borrowing_authority:{sections: ["1:HRTAC Act", "12", "13"]}   # definition names vary by borrower
    loan_amount:        {sections: ["recitals", "3"]}
    interest_rate:      {sections: ["6"]}
    payment_schedule:   {sections: ["5", "7", "9", "10"], exhibit: "G"}
    default_remedies:   {sections: ["19"]}
    accounting_audit:   {sections: ["20"]}
    oversight:          {sections: ["22"]}
    notices_termination:{sections: ["36", "38"]}
  other_significant_changes: {top_n: 3}
  max_pages: 2
```

- A deviation belongs to a topic if its template section (or its parent) is listed for that topic.
- Definition changes map to a topic only when listed (e.g. a change to the borrowing-authority definition).
- **"Other significant changes"** takes the top substantive deviations not mapped to any topic, ranked by: category priority (set at the taxonomy meeting), then size of change.
- `needs_review` deviations are counted but never listed as highlights.

**Fix for the HRTAC-specific config:** definition names like "HRTAC Act" differ by borrower. The extractor should look for any definition whose text cites a state code or statute, not a fixed name.

---

## 6. Architecture

```
Aligned sections ─┬─> TopicExtractor (LLM, per topic) ──┐
                  │                                       │
Full PDF ─> ExhibitLocator ─> ScheduleParser ────────────┼─> SummaryBuilder ─> <doc_id>_summary.pdf
                                                          │
Labeled deviations ─> TopicMapper ───────────────────────┤
                                                          │
                       BottomLineWriter (LLM) <───────────┘
```

| Class | File | Responsibility |
|---|---|---|
| `TopicExtractor` | `summary/topics.py` | For each topic, send **only that topic's sections** to the LLM; get a 1–2 line summary + supporting quote; verify the quote; detect draft blanks and "[to be inserted]" |
| `ExhibitLocator` | `summary/exhibits.py` | Find Exhibit G pages in the full PDF |
| `ScheduleParser` | `summary/schedule.py` | Parse the debt service table; compute the digest; run the principal-sum cross-check |
| `TopicMapper` | `summary/mapping.py` | Assign deviations to topics; pick "other significant changes" |
| `BottomLineWriter` | `summary/bottom _line.py` | 2–3 sentences from topic change counts only; must cite topic names that exist; fallback template sentence if checks fail |
| `SummaryBuilder` | `summary/builder.py` | HTML → PDF with the existing renderer; count pages; if over 2, shorten summaries and bullets, then re-render |

### Data model additions

```python
class ValueStatus(Enum):
    FOUND = "found"
    DRAFT_BLANK = "draft_blank"
    NOT_INSERTED = "not_inserted"
    NOT_FOUND = "not_found"

@dataclass
class TopicRow:
    topic: str
    label: str
    summary: str | None
    quote: str | None
    sources: list[str]              # e.g. ["§6, p. 36"]
    status: ValueStatus
    substantive_changes: int
    change_ids: list[str]           # deviation IDs, max 3 shown

@dataclass
class ScheduleDigest:
    first_payment: str | None
    final_payment: str | None
    payment_count: int | None
    frequency: str | None
    total_principal: float | None
    largest_annual: tuple[str, float] | None
    matches_loan_amount: bool | None
    status: ValueStatus
    pages: list[int]
```

---

## 7. Cost

~11 topic calls on short section excerpts + 1 bottom-line call per agreement. Rough estimate **under $0.50 per agreement**; check `llm_usage.csv` after the first run. Exhibit G parsing uses no LLM. Cached like the classifier.

---

## 8. Tests

| Test | Checks |
|---|---|
| Fixture with known terms in every topic | Each row's summary cites the right section |
| Draft blank / "[to be inserted]" | `DRAFT_BLANK` / `NOT_INSERTED`, never invented values |
| Bad LLM quote | Row falls back to "See agreement §X" |
| Deviation in §19 | Counted under Events of default, not "Other" |
| Exhibit G fixture table | Digest correct; principal sum matches loan amount |
| Exhibit G total mismatch | Shows mismatch warning, no digest |
| Exhibit G missing | Row uses §9 facts + page pointer |
| Long agreement | PDF ≤ 2 pages |

Build the Exhibit G fixture as a small HTML table rendered to PDF, like the other fixtures.

---

## 9. Schedule (~13 hours, about 3 weeks at 5 hrs/week)

Starts after the classifier has run on DRLE, because "changed from template" needs labeled deviations.

| Step | Task | Hrs |
|---|---|---|
| 1 | Data model, config, manifest `full_path` column | 0.5 |
| 2 | `TopicMapper` + tests | 1.5 |
| 3 | `SummaryBuilder`: HTML template, PDF, 2-page enforcement (with change counts only, before any LLM text) | 2.5 |
| 4 | `TopicExtractor`: section lookup, prompt, quote check, draft states + tests | 3 |
| 5 | `ExhibitLocator` + `ScheduleParser` + cross-check + tests | 3 |
| 6 | `BottomLineWriter` + fallback | 1 |
| 7 | Hand-verify every row on DRLE and one more non-held-out agreement | 1 |
| 8 | Generate all non-held-out summaries; read each as an executive would | 0.5 |

**If time runs short, cut in this order:** Exhibit G parsing (show §9 facts + page pointer) → bottom line (use the template sentence) → "Other significant changes" row. Never cut quote verification or the 2-page check.

---

## 10. Demo questions (week 9)

1. "Would you send this to your client as is? What would you change?"
2. "Is anything here wrong or missing?"
3. "Which rows would your client actually read?"
4. "Does this change what you'd pay?"

---

## 11. Done when

- One command produces both PDFs for every non-held-out agreement
- Every summary is ≤ 2 pages
- Every row is verified, or marked as draft, not inserted, or "See agreement"
- Every "changed from template" count matches the full report
- Exhibit G digest either passes the principal cross-check or falls back cleanly
- All rows hand-checked on 2 agreements, with errors recorded