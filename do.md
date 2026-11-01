# What's left to finish Phase 0

As of November 1, 2026. Read with `PHASE0.md`, which sets the requirements; this file says
what is already done and what you (F), your cofounder (C) and the advisor (A) still need to
do, in order. Commands run from `tifia-review/`.

## The short version

The code for weeks 1–8 is written and tested (173 tests pass). What remains is mostly
work only people can do, plus a few decisions:

1. Put an API key in `.env`, so the classifier can run (15 minutes).
2. Hold the taxonomy meeting with the advisor, and get the accuracy threshold agreed in
   writing (week 6).
3. Get the advisor labeling agreements now: it is the longest wait, and it must happen
   before the advisor sees any report (week 8).
4. Run, score, fix the top one or two error causes, then score the held-out agreement
   once (weeks 8–9).
5. Demo to 2–3 advisors, asking about price and for an LOI or pilot (week 9).
6. Write the go/no-go memo (week 10).

The critical path is the advisor's answer keys. Everything from week 8 on waits for them,
so start that today, in parallel with everything else.

---

## 1. Where things stand

Each "done when" item from `PHASE0.md`. ✅ done · ⚠️ partly done or open · ⬜ not started.

| Week | Done when (PHASE0.md) | Status |
|---|---|---|
| 1 | `pytest` runs; `models.py` imports | ✅ |
| 1 | Fixture PDFs and `expected_deviations.json` exist | ✅ Five fixtures, built from the real template rather than the short HTML/WeasyPrint template the plan describes, which makes them a harder test. |
| 1 | Template + at least 2 executed agreements in `data/` with manifest rows | ✅ 2 templates and 4 agreements. ⚠️ All 4 are Sound Transit agreements signed the same day, so they are near-duplicates (see step 4). |
| 1 | C asks the advisor whether older template versions exist | ⬜ This matters: the 2021 agreements predate the 2023 template (see decision D2). |
| 2 | Loader tests pass; every document inspected; stages save and load JSON | ✅ `docs/extraction-notes.md` |
| 2 | C collects agreements 3–5 | ⚠️ 4 agreements, one borrower |
| 3 | Fixture segmentation exact; template matches its TOC; definitions parsed | ✅ |
| 4 | Fixtures: renumbered section matched, added/removed reported | ✅ |
| 4 | Real agreement: ≥ 90% of alignments correct by hand-check | ⚠️ Claude marked Federal Way: 81.2% strict, 90.7% if merged/split clauses are left out. **You need to audit the marks and decide** (step 2). |
| 5 | All six fixture changes detected; fill-in labeled as such | ✅ |
| 5 | Deviation list on a real agreement is mostly real changes | ✅ About 1.4% punctuation-only on DRLE, and all of those are real edits. |
| 6 | Every deviation on one agreement labeled or `needs_review` | ⬜ Code done. Not run yet: no API key. |
| 6 | All evidence quotes verified | ⬜ Automatic once it runs. |
| 6 | Accuracy threshold agreed with the advisor, in writing | ⬜ C + A |
| 6 | Taxonomy meeting held, final categories written down | ⬜ Draft ready: `docs/taxonomy-draft.md`. |
| 7 | `python scripts/run.py` produces a PDF for every agreement | ✅ Made with `--no-llm` (no labels yet). Printed by headless Edge because WeasyPrint can't load (optional fix: step 6). |
| 7 | Reports readable, every flag has citations | ⚠️ Citations checked on every deviation. Readability needs a look once the reports are classified. |
| 7 | C sends the advisor the answer-key template | ⬜ Ready: `data/gold/gold_template.csv` and `INSTRUCTIONS.md`. |
| 8 | Accuracy recorded against the threshold; every error has a cause; time comparison recorded | ⬜ Evaluator and commands ready (`docs/evaluation.md`). Needs the answer keys. |
| 9 | Held-out accuracy recorded; 2–3 demos with feedback and price reactions | ⬜ |
| 10 | Decision memo with the gate table | ⬜ |

---

## 2. What blocks what

```
API key ──────────────┐
Taxonomy meeting ─────┼─> classified run ─> read every label (wk 6) ─> classified reports (wk 7) ─> demos (wk 9)
Threshold in writing ─┘                                                        │
                                                                               v
Answer keys from advisor (long wait; must come before they see reports) ─> score (wk 8) ─> error causes
                                                                               │
                                       fix top 1-2 causes ─> held-out score, once (wk 9) ─> memo (wk 10)
Interview notes + LOIs/pilots (C, all along) ────────────────────────────────────────────────┘
```

Two ordering rules from PHASE0 shape this:

- **The advisor labels before seeing any report.** Send the answer-key package before
  anyone shows them a report. Keep the taxonomy meeting to the 10 draft examples, not
  full reports.
- **The threshold is fixed before scoring.** Record it in `config.yaml` before the first
  `scripts/evaluate.py` run.

---

## 3. Step by step

### Step 0 — This week: unblock everything (F + C, about 1 hour)

- [ ] **F: API key.** Create a key in the Anthropic Console, then:
  ```
  copy .env.example .env      # then edit .env: LLM_API_KEY=sk-ant-...
  python scripts/classify_documents.py fx_03_modified --dry-run   # what it will send
  python scripts/classify_documents.py fx_03_modified             # one call, about $0.01
  ```
  Open `outputs/classify/fx_03_modified__vs__...csv`. Two deviations should have categories,
  explanations and evidence quotes, and two should be fill-ins. Every run's cost goes to
  `outputs/llm_usage.csv` with the running total against the $300 budget.
- [ ] **F + C: decide which template each agreement is compared against** (decision D2),
  because the advisor must label against the same template the tool uses. It is the
  manifest's `template` column, currently the project-revenue template for all four.
- [ ] **C: send the advisor the answer-key package** (week 8, step 1). It should contain:
  - `data/gold/gold_template.csv` and `data/gold/INSTRUCTIONS.md`.
  - The template file for each agreement. Add one line to the instructions naming it,
    because they currently just say "the template".
  - Which agreements to label: 3–5, see step 4.
  - The rule: label before seeing any report from the tool.

  Agree on scope too: the whole agreement, exhibits included? If the advisor labels only
  some sections, the evaluator has to be limited to those sections, or everything else
  counts as false flags. That is a small code change; ask before they start. Ask for a
  date, since labeling a 140-page agreement takes hours.
- [ ] **C: schedule the taxonomy meeting** (1 hour, F + C + A), and ask the advisor to
  agree the accuracy threshold in writing.
- [ ] **C: ask the advisor about older template versions** (a week-1 leftover), and look
  for 1–2 agreements from other public borrowers (see step 4).
- [ ] **C: check the interview record.** The week-10 gate counts interviews ("advisors say
  review is a major time cost, ≥ 50% of interviews") and the week-9 demos go to "advisors
  from the interviews". Make sure the notes exist with a tally, and line up the 2–3 demo
  candidates now.

### Step 1 — Week 6: the taxonomy meeting (F + C + A, 1 hour)

Use `docs/taxonomy-draft.md`. It has the draft categories with definitions, the draft
definition of "substantive", 10 real DRLE deviations with suggested labels, and the
questions to ask. The main open questions:

- Representations and warranties currently fall under "Other". Security and flow of funds
  fall under "Payment terms". Should either be its own category?
- Are cross-reference and caption updates substantive? Is dropping a template provision
  that doesn't fit the deal substantive? Are new deal-specific definitions substantive?
- Is one sentence the right length for an explanation?

After the meeting:

- [ ] Write the final names and definitions into `config.yaml` (`taxonomy`, `substantive`).
  Changing them changes the prompt, so earlier cached labels are not reused. That is
  intended.
- [ ] Record the threshold. Set `evaluation.threshold_agreed` in `config.yaml` (who, when,
  where it is written) and fill in the "Accuracy threshold" section of
  `docs/taxonomy-draft.md`. Update `target_recall` and `target_precision` if they changed
  from 90% and 70%. **Don't touch them after scoring starts.**
- [ ] Optional: if the technical plan has its own system prompt, compare it with
  `src/tifia_review/llm/prompts.py`. `PHASE0.md` says to use the plan's prompt, but that
  file wasn't in the folder, so Claude drafted this one.

### Step 2 — Week 4 leftover: the alignment hand-check (F, 1.5–3 hours)

Claude marked all 431 Federal Way alignment rows
(`data/handcheck/tla_2021_federal_way__vs__...csv`, summary in
`docs/alignment-handcheck.md`). The marks are Claude's; the plan says F marks them.

- [ ] **Audit the marks.** Start with the 14 judgment-call rows listed at the end of the
  summary, then spot-check the rest. Change any `correct` value you disagree with.
- [ ] **Decide how to read the result** (decision D3). Strictly it is 81.2%, which misses
  the 90% target. With the 45 merged, split or inline-clause rows left out it is 90.7%,
  which meets it; the plan itself says Phase 0 doesn't handle merged or split sections.
  The aligner's own pairs are 98.4% right; its errors are rewritten clauses it failed to
  pair.
- [ ] **If you choose to improve it,** two fixes are described in the summary: a last
  pass that pairs leftover clauses within a paired section when each is the other's best
  match, and splitting inline "(A) …; (B) …" lists. Tune on DRLE and recheck on
  **Northgate**. Don't use Federal Way (already examined) or O&M Facility East (keep it
  clean for the hold-out, step 4). This is optional for Phase 0; if time is short, list
  it as a known limitation.

### Step 3 — Week 6 finish: label one agreement and read every label (F, 1.5 hours, about $3)

- [ ] Run the classifier on DRLE with the final taxonomy:
  ```
  python scripts/classify_documents.py tla_2021_drle --dry-run   # about 43 calls
  python scripts/classify_documents.py tla_2021_drle
  ```
- [ ] **Read every label** in `outputs/classify/tla_2021_drle__vs__...csv` (week 6, step
  5). Fill in `category_ok`, `substantive_ok`, `explanation_ok` and `notes`. Look for:
  - categories that don't fit the definitions;
  - explanations that claim more than the text says;
  - "needs review" items, and why.
- [ ] If a category keeps going wrong, the fix is usually in its definition in
  `config.yaml`. Changing it re-labels on the next run, costing another ~$3.

Done when: every deviation is labeled or marked needs review, and there are no unverified
evidence quotes (the classifier enforces this).

### Step 4 — Week 8 preparation: choose the agreements and the hold-out (F + C, 30 minutes)

- [ ] **Which agreements the advisor labels.** PHASE0 asks for 3–5. All four Sound Transit
  agreements were drafted together and are nearly identical, so a score on one says
  little about the others. If C can find agreements from other public borrowers, include
  1–2 of them; that is a much more convincing test. Add each one to `data/agreements/`
  and `data/manifest.csv` (with `title` and `template` columns), then run
  `python scripts/run.py <doc_id> --no-llm` to check it reads, segments and aligns
  sensibly. If you plan to hold one of them out, only check that it runs without errors,
  and don't study its report.
- [ ] **Choose the held-out agreement now, before any scoring,** and put it in
  `evaluation.holdout` in `config.yaml`. Recommendation: **O&M Facility East**, the one
  nothing has been tuned or checked on so far. DRLE was used for tuning and the taxonomy
  examples, Federal Way for the hand-check, and Northgate for the alignment recheck if
  you do step 2's fix. A new borrower's agreement, if C finds one, is even better.

### Step 5 — Week 8: score and analyze errors (F, about 4 hours, once the answer keys arrive)

- [ ] Save the advisor's files as `data/gold/<doc_id>.csv`, one per agreement.
- [ ] Run the labeled agreements, except the hold-out:
  ```
  python scripts/run.py tla_2021_drle tla_2021_federal_way tla_2021_northgate
  python scripts/evaluate.py
  ```
  `evaluate.py` converts the CSVs to `data/gold/<doc_id>.json`, stopping with line
  numbers if a row is malformed (send those back to the advisor). It scores the newest run
  of each agreement and writes `outputs/evaluation/<time>/results.md`, `results.json` and
  `errors.csv`. It skips the hold-out automatically.
- [ ] **Error analysis.** Every miss, false flag and wrong label is a row in `errors.csv`,
  with a suggested cause and the reason for it. Confirm or correct each in the `cause`
  column: `extraction`, `segmentation`, `alignment`, `diff/normalization`,
  `classification`, or `answer key` (a real deviation the key left out). Then:
  ```
  python scripts/evaluate.py --causes outputs/evaluation/<time>/errors.csv
  ```
  The top one or two causes are the week-9 fixes.
- [ ] **Time comparison.** Ask the advisor how long a manual review took against how long
  reviewing the report takes. Record it in `docs/evaluation.md`.
- [ ] Copy the results table into the "Results" section of `docs/evaluation.md`.
  `outputs/` is not kept in git.

Things to expect, so they don't surprise you:

- Overall precision will look low if the advisor recorded only the important changes,
  since the tool reports every wording change. The threshold applies to substantive
  precision, which is less affected; the `answer key` cause is for those rows.
- Expect alignment-type errors from rewritten or merged clauses, as the week-4 hand-check
  found.
- Don't loosen the matching rule or move the threshold after seeing numbers (PHASE0 week
  8 pitfalls).

### Step 6 — Week 7 polish (F, 1 hour; anywhere after step 3)

- [ ] Regenerate the reports with labels for every agreement **except the hold-out**:
  `python scripts/run.py fx_01_added fx_02_removed fx_03_modified fx_04_renumbered
  fx_05_definition tla_2021_drle tla_2021_federal_way tla_2021_northgate`.

  A bare `python scripts/run.py` would also run the held-out agreement, and its report is
  for week 9. This costs about $3 per real agreement the first time, then reuses the
  cache. Skim each PDF. Is it readable? Is the needs-review section sensible? Do the
  categories group well?
- [ ] Optional: switch the PDF renderer to WeasyPrint, as the plan specifies:
  1. In MSYS2, run `pacman -S mingw-w64-ucrt-x86_64-pango`. This may update MSYS2
     packages.
  2. Set the environment variable `WEASYPRINT_DLL_DIRECTORIES=C:/msys64/ucrt64/bin`.
  3. Run `pip install weasyprint`.

  The command line reports which renderer it used. Edge's output already meets the
  week-7 requirements, so this is cosmetic.

### Step 7 — Week 9: fix, confirm, demo (F + C, about 5 hours)

- [ ] **Fix the top one or two causes only.** For each, first add a test that reproduces
  the error, then fix it (PHASE0 week 9). List the other causes as known limitations.
- [ ] Rerun and rescore the non-held-out agreements:
  `python scripts/run.py <doc_ids>` then `python scripts/evaluate.py`.
- [ ] **Score the held-out agreement once:**
  ```
  python scripts/run.py tla_2021_om_facility_east
  python scripts/evaluate.py --holdout
  ```
  This is the honest accuracy for the memo. Don't fix anything after looking at it and
  rerun. Record it in `docs/evaluation.md`, under the held-out results.
- [ ] Regenerate the reports for the demos.
- [ ] **Demos (F + C, 15 minutes each, 2–3 advisors from the interviews):**
  1. Two minutes on the problem in their words, from their interview.
  2. Walk through one report: summary, a defined-term change, one category, needs review.
  3. Ask: "Is this accurate? Would this save you time? What's missing?"
  4. **Ask about price:** "If it did this reliably, would you pay $X per deal or per seat?"
     Decide on X beforehand.
  5. If they're positive, ask for an LOI or a pilot commitment.

  Write answers down word for word. A demo without a price question doesn't count toward
  the gate. Point out the "Prototype — for professional review only" marking, and don't
  leave the report with them unless they agree to treat it as a prototype.

### Step 8 — Week 10: the go/no-go memo (F + C, about 3 hours)

Fill in the gate table from `PHASE0.md` honestly, citing where each number comes from:

| Gate criterion | Target | Where the result comes from |
|---|---|---|
| Advisors say review is a major time cost | ≥ 50% of interviews | C's interview notes and tally |
| Written LOI or email naming a price | ≥ 1 | Demo follow-ups (step 7) |
| Firms agreeing to pilot | ≥ 2 | Demo follow-ups |
| Held-out accuracy (substantive) | Threshold from week 6 | `evaluate.py --holdout` result (step 7) |
| Advisor formally committed | Yes | Written commitment from the advisor |

Then write:

1. The evidence: key interview quotes, accuracy numbers, and the time comparison.
2. The decision: go, change the target customer, or stop.
3. If go: Phase 1 resourcing, since 5 hours a week won't support live customers (more
   time, another engineer, or SBIR funding).
4. If not: what the evidence suggests instead.

A failed criterion is reported as failed, not redefined, and Phase 0 is not extended
without new evidence (PHASE0 week 10 pitfalls).

---

## 4. Decisions only you can make

| # | Decision | Recommendation |
|---|---|---|
| D1 | Which model classifies (`config.yaml` `llm.model`, now `claude-sonnet-5-5`) | Keep it. About $3 per agreement keeps the whole phase far under $300. Claude Opus 5.5 would cost about twice as much. |
| D2 | Which template the 2021 agreements are compared against | Decide before the advisor labels. They are tax-backed and predate the 2023 templates, so many deviations are template-version differences, not negotiation. Options: an older template if the advisor can get one (best), the non-project template, or the project template (current). Change the manifest's `template` column. |
| D3 | How to read the week-4 hand-check (81.2% strict, 90.7% without merged/split clauses) | Audit the marks first. Report both numbers in the memo, and treat merged or split clauses as the known limitation the plan already names. |
| D4 | Which agreement is held out | O&M Facility East, or a new borrower's agreement if one turns up. Decide before scoring. |
| D5 | Whether to install WeasyPrint | Optional. Edge's output already meets the week-7 requirements. |

## 5. Time and money

- **Your time (F):** about 15–20 hours from here: step 0 (0.5), step 1 (1.5), step 2
  (1.5–3), step 3 (1.5), step 5 (4), step 6 (1), step 7 (5), step 8 (shared, 3). At 5
  hours a week that is 3–4 weeks of work. The calendar depends on the advisor's labeling.
- **Advisor (A):** the 1-hour taxonomy meeting, labeling 3–5 agreements (the big one: ask
  them for an estimate), and the review-time question.
- **Cofounder (C):** the answer-key package, scheduling, the agreement search, interviews,
  demos and follow-ups.
- **LLM spend:** about $0.01 per fixture and about $3 per real agreement the first time.
  Reruns are free unless the text, prompt, taxonomy or model changes. Expect $20–50 for
  the rest of Phase 0, depending on how often the taxonomy and the week-9 fixes force
  re-labeling. It is tracked in `outputs/llm_usage.csv`.

## 6. If you fall behind

From `PHASE0.md`: cut the embedding fallback first (it is already built, so nothing to
cut), then the category accuracy metric, then report polish.

**Never cut:**

- the hand inspection (done)
- evidence verification (built in)
- the advisor labeling before seeing reports
- the held-out agreement

## 7. Reference

| What | Where |
|---|---|
| Run everything, one PDF per agreement | `python scripts/run.py [doc_id ...] [--no-llm]`, output in `outputs/reports/` |
| Classify only, with a cost estimate | `python scripts/classify_documents.py <doc_id> [--dry-run] [--limit N]` |
| Score against answer keys | `python scripts/evaluate.py [doc_id ...] [--holdout] [--causes errors.csv]` |
| Tests | `python -m pytest -q` |
| Settings: taxonomy, threshold, hold-out, model | `config.yaml` |
| Agreements and their templates | `data/manifest.csv` |
| Taxonomy draft and threshold record | `docs/taxonomy-draft.md` |
| Alignment hand-check | `docs/alignment-handcheck.md`, `data/handcheck/` |
| Extraction findings | `docs/extraction-notes.md` |
| Evaluation guide and results record | `docs/evaluation.md` |
| Advisor's answer-key instructions | `data/gold/INSTRUCTIONS.md`, `data/gold/gold_template.csv` |
| Cost ledger | `outputs/llm_usage.csv` |
