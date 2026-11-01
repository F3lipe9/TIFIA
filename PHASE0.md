# Phase 0 Weekly Guide — In Depth

**Companion to:** `phase0-technical-plan.md` (task lists and interfaces) and `build-plan.md` (phase overview)
**Capacity:** ~5 hrs/week for Felipe · **Owners:** F = Felipe, C = cofounder, A = advisor

Each week covers: **goal**, **why it matters**, **how to do it**, **done when**, **pitfalls**, and **hand-off** to the next week.

> **Status note:** The `DocumentLoader` (week 2) is already drafted, with the revised `Page` model (`pdf_index`, `printed_label`, `needs_review`). That puts you about a week ahead. Spend the extra time on segmentation (week 3), the riskiest step, not on new features.

---

## Pipeline at a glance

```
PDFs ─> Load (W2) ─> Segment (W3) ─> Align (W4) ─> Diff (W5) ─> Classify (W6) ─> Report (W7)
                                                                                     │
                                       Evaluate against advisor answer key (W8) <────┘
                                       Fix + demo (W9) ─> Go/no-go (W10)
```

Each stage only works if the one before it is correct. A segmentation error in week 3 becomes a false deviation in week 5 and a wrong flag in week 7. That's why the early weeks matter most.

---

## Week 1 — Setup and documents

**Goal:** A working repo skeleton, the data model, test fixtures with known answers, and the first real documents in hand.

**Why it matters:** Every later week depends on two things from this week: a stable data model, so stages can pass data to each other, and real documents, so you're not building against guesses.

### How

**1. Repo and dependencies (F, ~1h)**
- Create the folder structure from the technical plan.
- `pyproject.toml` dependencies: `pymupdf`, `sentence-transformers`, `jinja2`, `weasyprint`, `pyyaml`, `python-dotenv`, plus `pytest` for development. Add your LLM provider's SDK in week 6, not now.
- `.env.example` with `LLM_API_KEY=`, and add `.env` and `data/**/*.pdf` to `.gitignore`.
- `config.yaml` for tunable values (`min_chars`, alignment threshold, model names), so you change settings without editing code.

**2. Data model (F, ~1.5h)**
- Write `models.py` with the dataclasses, using the **revised `Page`** (`pdf_index`, `printed_label`, `text`, `needs_review`).
- Update `Section` to store `pdf_start` and `pdf_end` (PDF indexes) plus `label_start` and `label_end` (printed labels) for citations.

**3. Synthetic test fixtures (F, ~1.5h)**
This is your first answer key. You know exactly what changed, so any test failure points straight at a bug.
- Write a short fake template (5–8 sections, a definitions section, nested `(a)`, `(i)` subsections) as HTML, and render it to PDF with WeasyPrint. Using a real PDF means the fixtures test the loader too.
- Copy it as the "executed" version and plant **exactly** these changes:
  1. One added clause
  2. One removed clause
  3. One changed number (e.g. "180 days" → "120 days")
  4. One renumbered section (Section 5 becomes Section 6)
  5. One changed defined term
  6. One template blank filled in (e.g. `[Borrower]` → `City of Example`), which should **not** count as a negotiated deviation
- Save `tests/fixtures/expected_deviations.json` listing those six changes.

**4. Documents (F + C)**
- F: download the borrower TLA template, note its version and date, add its manifest row.
- C: find **2+ executed agreements from public agency borrowers**, add manifest rows immediately.
- C: ask the advisor whether older template versions exist and how to get them.

### Done when
- `pytest` runs (even with no real tests yet)
- `models.py` imports cleanly
- Fixture PDFs and `expected_deviations.json` exist
- Template + at least 2 executed agreements are in `data/` with manifest rows

### Pitfalls
- **Over-designing the data model.** It will change; keep it minimal.
- **Fixtures that are too easy.** Include nesting and a definitions section, or they won't catch real bugs.
- **No agreements by week's end.** This is the biggest schedule risk. If C hasn't found any, escalate immediately, because weeks 2–5 will run on the template alone.

### Hand-off → Week 2
Real PDFs to load; fixtures to test against.

---

## Week 2 — Loading

**Goal:** Clean, page-level text from every document, with problems flagged.

**Why it matters:** Extraction errors (garbled text, wrong reading order, missing pages) silently corrupt every later stage. This is the week to see what the documents actually look like.

### How

**1. Finish and test `DocumentLoader` (F, ~1h — mostly done)**
- Add the four loader tests: clean PDF → `is_scanned == False`; mostly blank → `True`; "civil" on line 1 → no label; "12" on the last line → label "12".

**2. Inspect every document by hand (F, ~2h)**
Write a small script that dumps each document's text to `outputs/inspect/<doc_id>.txt`, then skim them. Check for:
- **Reading order:** do two-column pages or tables come out scrambled?
- **Ligatures:** "ﬁ" and "ﬂ" characters instead of "fi" and "fl" (these break text matching)
- **Hyphenation:** words split across lines ("obli-\ngation")
- **Headers and footers:** what repeats on every page ("TIFIA Loan Agreement," "Execution Version," dates)?
- **Page labels:** are printed page numbers detected correctly?
- **Scanned pages:** which pages were flagged?

Write findings in `data/manifest.csv` notes and a short `docs/extraction-notes.md`. **The header/footer patterns you list here are what the week-5 normalizer removes.**

**3. `store.py` (F, ~1h)**
- Save each stage's output as JSON in `outputs/<run_id>/<stage>.json`.
- Use `dataclasses.asdict()`, and convert enums to their `.value`.
- Write a matching `load` function so later stages can start from a saved file without rerunning everything.

**4. More documents (C)**
- Collect agreements 3–5.

### Done when
- Loader tests pass
- Every document's text has been inspected, and problems are written down
- Stage outputs save to and load from JSON

### Pitfalls
- **Skipping the hand inspection.** It feels slow, but it's the cheapest way to find problems that would cost hours to debug in week 5.
- **Fixing extraction problems in the loader.** Keep the loader raw; record problems now and handle them in the week-5 normalizer, so you always have the original text to check against.

### Hand-off → Week 3
Raw page text, plus a list of extraction quirks.

---

## Week 3 — Segmentation (highest-risk week)

**Goal:** Turn each document into a tree of sections with correct IDs, headings, text, and page ranges, plus a parsed definitions list.

**Why it matters:** Sections are the unit of comparison for everything after this. If the segmenter splits a section wrongly, alignment fails, and the diff reports changes that aren't real.

### Key concept: the character-to-page map
Sections cross page boundaries, so join all pages into **one continuous text** first, and keep a list recording which character position each page starts at. When you find a section at character 48,210, the map tells you it's on PDF page 31. Without this, citations break.

```python
class PageMap:
    def __init__(self, pages: list[Page]):
        self.offsets, self.pages, parts, pos = [], pages, [], 0
        for p in pages:
            self.offsets.append(pos)
            parts.append(p.text)
            pos += len(p.text) + 1  # +1 for the joining newline
        self.full_text = "\n".join(parts)

    def page_at(self, char_pos: int) -> Page:
        idx = bisect.bisect_right(self.offsets, char_pos) - 1
        return self.pages[idx]
```

### How

**1. Find headings (F, ~2h)**
Legal agreements typically nest like: `ARTICLE VII` → `Section 7.02` → `(a)` → `(i)` → `(A)`. Use regex patterns that match **only at the start of a line**:
- Articles: `^ARTICLE\s+[IVXLC\d]+`
- Sections: `^Section\s+\d+\.\d+`
- Subsections: `^\(([a-z]{1,2})\)`, `^\(([ivxlc]+)\)`, `^\(([A-Z])\)`

Check these against what the actual TLA template uses. Formats vary, and the template is your ground truth.

**2. Build the tree (F, ~1h)**
- Assign each heading a level, then a normalized ID: `7.02(a)(i)`.
- Section text = everything from its heading to the next heading at the same or higher level.

**3. Parse definitions (F, ~1h)**
- Find the definitions section (often Article I or Section 1.01).
- Definitions usually look like `"Term" means ...` or `"Term" has the meaning...`, with curly or straight quotes. Capture term → definition text.

**4. Test (F, ~1h)**
- Fixtures: section count and IDs match exactly.
- Real template: compare your section list to its **table of contents**.

### Done when
- Fixture segmentation exactly matches the expected structure
- The template's sections match its table of contents
- Definitions are parsed for the template and one agreement

### Pitfalls
- **The table of contents itself.** It lists every heading, so the segmenter will find each heading twice. Detect and skip TOC pages (lines ending in dot leaders and a page number, like `Section 7.02 Covenants ........ 34`).
- **Cross-references.** "as set forth in Section 7.02" is not a heading. Requiring a line start handles most cases.
- **Roman numeral or letter?** `(i)` can be the 9th letter after `(h)`, or the first roman numeral under `(a)`. Use context: if the previous sibling at that level was `(h)`, it's a letter; otherwise treat it as roman.
- **Headings split across lines** by the PDF extraction. Check the inspection dumps from week 2.

**If this isn't working by week's end:** take time from week 7 report polish. Don't move on with a broken segmenter.

### Hand-off → Week 4
Two section trees (template and agreement) plus their definitions.

---

## Week 4 — Alignment

**Goal:** Pair each section in the agreement with its matching section in the template, or mark it as added or removed.

**Why it matters:** The diff compares paired sections. A wrong pair makes two unrelated sections look completely rewritten.

### How — three passes, most reliable first

**Pass 1: exact section ID (F, ~0.5h).** Template `7.02(a)` ↔ agreement `7.02(a)`. This handles most sections.

**Pass 2: normalized heading (F, ~1h).** For leftovers, compare headings after lowercasing and removing punctuation and numbers. This catches renumbered sections ("Section 5 Reporting" became "Section 6 Reporting").

**Pass 3: embedding similarity (F, ~1.5h).** For anything still unmatched:
- Embed section texts locally with a small sentence-transformers model (e.g. `all-MiniLM-L6-v2`).
- Compute cosine similarity between every unmatched template/agreement pair.
- Match greedily from the highest score down, each section used **only once**, and stop below the threshold (start at 0.80; adjust after hand-checking).

**Leftovers (F, ~0.5h):** unmatched template sections → **REMOVED**; unmatched agreement sections → **ADDED**. Record the `method` and `confidence` on every alignment.

**Hand-check (F, ~1.5h):** export alignments for one real agreement to a spreadsheet and mark each correct or wrong. Target ≥ 90% correct.

### Done when
- Fixtures: the renumbered section is matched, and the added and removed clauses are reported as such
- Real agreement: ≥ 90% of alignments correct by hand-check

### Pitfalls
- **Merged or split sections.** One template section might become two in the agreement. Phase 0 doesn't handle this; it'll show up as one match plus one ADDED. Note it as a known limitation.
- **A threshold set too low** forces bad matches. It's better to report a section as unmatched than to guess.
- **Tuning the threshold on the same agreement you'll evaluate on later.** Tune on one, then test on another.

### Hand-off → Week 5
A list of section pairs, with ADDED and REMOVED sections marked.

---

## Week 5 — Diffing

**Goal:** For each section pair, find the exact sentences that were added, removed, or changed.

**Why it matters:** This is where deviations are actually detected, by code rather than AI. Its accuracy sets the ceiling for recall.

### How

**1. Normalize text first (F, ~1h)**
Apply to both documents before comparing:
- Collapse whitespace; join hyphenated line breaks ("obli-\ngation" → "obligation")
- Straight quotes ↔ curly quotes, en/em dashes → hyphen
- Replace ligatures ("ﬁ" → "fi")
- Remove the header/footer lines found in week 2

Without this, formatting differences appear as hundreds of false deviations.

**2. Handle template blanks (F, part of ~1h)**
Templates contain placeholders like `[Borrower]`, `[___]`, `[●]`, `$[__]`. Filling them in is **not a negotiated change**. Detect placeholders in the template text and, when the only difference in a sentence is a filled placeholder, label it `fill_in` instead of a deviation. Your week-1 fixture tests this.

**3. Split into sentences (F, part of ~2h)**
A simple regex split on `. ` followed by a capital letter works, with exceptions for legal abbreviations: "Sec.", "U.S.C.", "U.S.", "No.", "Inc.", "i.e.", "e.g."

**4. Diff (F, ~2h)**
- Use `difflib.SequenceMatcher` on the two sentence lists. Its opcodes tell you which sentences were inserted, deleted, or replaced.
- Merge neighboring changes into one `Deviation`.
- Record page numbers using the week-3 character map.

**5. Defined terms (F, ~1h)**
- Compare template and agreement definitions directly. Flag new, removed, and changed definitions; set `affects_defined_term`.
- These go first in the report, because one changed definition alters meaning everywhere the term is used.

**6. Sanity check (F)**
- Dump all deviations for one agreement to text and skim. Mostly real changes? Or mostly noise?

**Meanwhile (C):** send the advisor the draft taxonomy and schedule the week-6 meeting.

### Done when
- All six fixture changes detected; the filled blank is labeled `fill_in`, not a deviation
- On one real agreement, the deviation list is mostly real changes, not formatting noise

### Pitfalls
- **Noise flood.** If you see hundreds of tiny "changes," it's almost always normalization. Fix normalization; don't filter deviations after the fact.
- **Sentence splitter errors** on abbreviations cut sentences in half and create false changes.

### Hand-off → Week 6
A clean list of deviations with exact text and page citations.

---

## Week 6 — Classification

**Goal:** Each deviation gets a category, a substantive/non-substantive flag, and a one-sentence explanation, with verified evidence.

**Why it matters:** A raw diff list is hard for an advisor to use. Categories and plain-English explanations turn it into a review tool.

### How

**1. Taxonomy meeting (F + C + A, ~1h)**
- Walk the advisor through the draft categories, with 5–10 real deviations from week 5 as examples.
- Ask: what's missing, what should merge, and what makes a change "substantive"?
- Leave with a final category list and written definitions for each.

**2. LLM client (F, ~1h)**
- `LLMClient` abstract class with `complete_json(system, user) -> dict`; one provider subclass. You can switch providers later without touching the classifier.
- Add the SDK dependency and API key now.

**3. Prompt and validation (F, ~2h)**
- Use the system prompt from the technical plan, with the final taxonomy inserted.
- Send 10–20 deviations per call, each with an ID.
- After each response, check: valid JSON, category in the taxonomy, `evidence` found word-for-word in the executed text (after normalization).
- Any failure: retry once, then set `needs_review = True`.

**4. Caching and cost tracking (F, ~0.5h)**
- Cache each result keyed by a hash of (template text + executed text + prompt version). Reruns cost nothing, and changing the prompt automatically invalidates old results.
- Log tokens used per run, so you can track spending against the $300 budget.

**5. Review one agreement (F, ~0.5h)**
- Read every label. Are categories sensible? Are explanations accurate?

**Meanwhile (C + A): set the accuracy threshold.** It must be set **before** week 8's evaluation. Starting proposal: recall ≥ 90% and precision ≥ 70% on substantive deviations.

### Done when
- Every deviation on one agreement is labeled, or marked `needs_review`
- All evidence quotes are verified
- Accuracy threshold agreed with the advisor, in writing

### Pitfalls
- **Letting the LLM judge whether something changed.** It only labels what the diff found.
- **Skipping evidence verification.** It's your main protection against made-up explanations.
- **An undefined "substantive."** Without the advisor's definition, labels won't be consistent.

### Hand-off → Week 7
Labeled, verified deviations.

---

## Week 7 — Report

**Goal:** A professional PDF report per agreement, generated by one command.

**Why it matters:** The report is the product in Phase 0. It's what the advisor scores and interviewees react to.

### How

**1. HTML template (F, ~2h)**
- Follow the layout in the technical plan: cover, summary, defined-term changes, substantive deviations by category, added and removed sections, non-substantive table, needs review, method note.
- For each deviation, show template text and executed text **side by side**, with changed words highlighted. Use `difflib` at the word level to wrap changes in `<del>` and `<ins>` tags, styled red and green.
- Every deviation shows section number and printed page for both documents.
- Mark the cover clearly: **"Prototype — for professional review only."**

**2. PDF rendering (F, ~1h)**
- Render with Jinja2, convert with WeasyPrint.
- Print CSS: page breaks between categories, page numbers in the footer, no deviation split across pages where avoidable.

**3. Pipeline and CLI (F, ~1h)**
- `Pipeline.run()` chains every stage and saves each output to `outputs/<run_id>/`.
- `scripts/run.py` reads `manifest.csv` and runs every executed agreement against its matching template.

**4. Generate all reports (F, ~1h)**
- Run on every agreement and skim each PDF.

**Meanwhile (C):** send the advisor the gold-set spreadsheet template (see week 8).

### Done when
- `python scripts/run.py` produces a PDF for every agreement
- Reports are readable and every flag has citations

### Pitfalls
- **Polishing design too long.** Clear and readable is enough. If week 3 or 4 ran over, this is the week to cut.
- **Hiding uncertainty.** Keep the "needs review" section visible; it builds trust.

### Hand-off → Week 8
PDF reports, plus the full pipeline for evaluation runs.

---

## Week 8 — Evaluation

**Goal:** Measure accuracy against the advisor's answer key.

**Why it matters:** This produces the numbers the go/no-go decision and the skeptical jurors need: proof, not opinion.

### How

**1. Advisor labels agreements (A)**
- The advisor reviews 3–5 agreements against the template and records every real deviation in the spreadsheet: section IDs, short excerpt, category, substantive yes/no.
- **The advisor labels before seeing your reports.** If they've seen your output first, they'll tend to agree with it, and your accuracy number will be inflated.
- **Hold out at least one labeled agreement** that you won't look at while fixing errors in week 9.

**2. Evaluator (F, ~2.5h)**
- Convert spreadsheets to `data/gold/<doc_id>.json`.
- Matching rule: same section and at least 50% text overlap.
- Compute recall, precision, and category accuracy, overall and for substantive deviations separately.

**3. Run and record (F, ~1h)**
- Results table per agreement, plus totals, against the threshold set in week 6.
- Ask the advisor how long manual review takes compared with reviewing your report.

**4. Error analysis (F, ~1.5h)**
Sort every miss and false flag by **which stage caused it**:

| Cause | Example |
|---|---|
| Extraction | Garbled text, missing page |
| Segmentation | Section split wrongly |
| Alignment | Wrong section pair |
| Diff/normalization | Formatting flagged as a change |
| Classification | Wrong category or substantive flag |

The biggest bucket is what you fix in week 9.

### Done when
- Accuracy numbers recorded against the pre-set threshold
- Every error assigned a cause
- Time comparison recorded

### Pitfalls
- **Changing the threshold after seeing results.** It was set in advance for a reason.
- **Matching rules that are too loose** inflate scores. Keep the 50% overlap rule.

### Hand-off → Week 9
A ranked list of error causes.

---

## Week 9 — Fix and demo

**Goal:** Fix the biggest error sources, confirm on the held-out agreement, and show the tool to real advisors.

### How

**1. Fix (F, ~3h)**
- Take the top one or two error causes only. For each, add a test reproducing the error before fixing it, so it can't come back.
- Rerun on the agreements you fixed against, then **once** on the held-out agreement. Report the held-out number as your honest accuracy.

**2. Regenerate reports (F, ~0.5h)**

**3. Demos (F + C, ~1.5h)**
Show the report to 2–3 advisors from the interviews. A 15-minute structure works:
1. Two minutes on the problem in their words, from the interview
2. Walk through one report
3. Ask: "Is this accurate? Would this save you time? What's missing?"
4. **Ask about price:** "If it did this reliably, would you pay $X per deal or per seat?"
5. Ask for an LOI or pilot commitment if they're positive

Record answers word-for-word where possible.

### Done when
- Held-out accuracy recorded
- 2–3 demos done, with feedback and price reactions written down

### Pitfalls
- **Fixing everything.** Fix the top causes; list the rest as known limitations.
- **Rerunning the held-out agreement after each fix.** Then it's no longer held out.
- **Leaving a demo without asking about price.** Positive feedback without a price signal doesn't meet the gate.

### Hand-off → Week 10
Accuracy results, demo feedback, and any LOIs.

---

## Week 10 — Go/no-go decision

**Goal:** Decide honestly whether to continue to Phase 1.

### How (F + C, ~3h)

Write a short decision memo with this structure:

| Gate criterion | Target | Result | Pass? |
|---|---|---|---|
| Advisors say review is a major time cost | ≥ 50% of interviews | | |
| Written LOI or email naming a price | ≥ 1 | | |
| Firms agreeing to pilot | ≥ 2 | | |
| Held-out accuracy (substantive) | Threshold set in week 6 | | |
| Advisor formally committed | Yes | | |

Then:
1. **Evidence:** key interview quotes, accuracy numbers, time-saving comparison
2. **Decision:** go, change target customer, or stop
3. **If go:** Phase 1 resourcing. 5 hours a week won't support live customer use, so decide on more time, another engineer, or SBIR funding.
4. **If not:** what the evidence suggests instead

### Pitfalls
- **Moving the goalposts.** If a criterion failed, say so. Don't redefine it.
- **"Just a few more weeks."** Don't extend Phase 0 without new evidence that justifies it.

---

## If you fall behind

Cut in this order: embedding fallback (week 4) → category accuracy metric (week 8) → report polish (week 7).

**Never cut:** hand inspection (week 2), evidence verification (week 6), the advisor labeling before seeing reports (week 8), or the held-out agreement (week 9). Those are what make the results believable.