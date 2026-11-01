# Evaluation (Phase 0, week 8)

## Steps

1. **Answer keys (advisor).** The advisor fills in one CSV per agreement for 3-5 agreements,
   following `data/gold/INSTRUCTIONS.md`, before seeing any report from the tool. Save them
   as `data/gold/<doc_id>.csv`.
2. **Before any scoring:**
   - Record the threshold as agreed: `evaluation.threshold_agreed` in `config.yaml` (who,
     when, where it is written down). The targets must not change after results are seen.
   - Choose the held-out agreement and list it in `evaluation.holdout`. It is not scored
     or looked at while fixing errors in week 9.
3. **Run the pipeline with classification:** `python scripts/run.py <doc_id> ...`.
4. **Score:** `python scripts/evaluate.py`. This converts each answer-key CSV to
   `data/gold/<doc_id>.json`, scores the newest run of each agreement, and writes
   `outputs/evaluation/<time>/`:
   - `results.md`: the table per agreement and in total, and the verdict against the
     threshold
   - `results.json`: the same numbers, plus every match
   - `errors.csv`: one row per miss, false flag and wrong label
5. **Error analysis.** In `errors.csv`, confirm or correct each row's cause in the `cause`
   column. Each row starts with a `suggested_cause` and a `detail` explaining it. Causes:
   `extraction`, `segmentation`, `alignment`, `diff/normalization`, `classification`, and
   `answer key` (a real deviation the key left out, or a key error). Then rank them with
   `python scripts/evaluate.py --causes <errors.csv>`. The top one or two causes are what
   week 9 fixes.
6. **Time comparison.** Ask the advisor how long a manual review takes compared with
   reviewing the report, and record it below.
7. **Week 9:** after the fixes, rerun and score the agreements you fixed against. Then run
   `python scripts/evaluate.py --holdout` once, and report that number as the honest
   accuracy.

## How deviations are matched and scored

- **Match** (PHASE0's rule): a reported deviation matches an answer-key row when:
  - they are in the same section: the agreement sections are equal, or the template
    sections are, once written the same way ("Section 12 (a)(x)" is "12(a)(x)");
  - and the deviation's text contains at least 50% of the row's excerpt words, in order
    (`evaluation.match_text_overlap`). Quote styles don't matter, and it is checked
    against the agreement text or the template text, whichever covers more.

  Fill-ins and renumberings don't count on either side. One deviation can match several
  rows, for example a removed section that the advisor lists as three changes.
- **Recall:** rows matched by a deviation. **Precision:** deviations that match a row.
- **Substantive recall and precision** are what the threshold applies to:
  - Substantive recall: the substantive rows matched by a deviation that the report shows
    as substantive or needs review. A substantive change found but filed in the
    non-substantive table does not count.
  - Substantive precision: the deviations shown as substantive or needs review that match
    a substantive row.
- **Category and substantive-flag accuracy:** over the matched rows, compared with the
  deviation that covers each row best. They are not available for a run made with
  `--no-llm`.

## Things to keep in mind

- **Precision depends on the answer key's detail.** The tool reports sentence-level
  changes, including wording changes, so a key that lists only important changes makes
  overall precision look low. The `answer key` cause is for those rows. Substantive
  precision is less affected.
- **Keep the matching rule.** A looser rule (parent sections, lower overlap) inflates the
  scores; PHASE0 warns against it.
- **Expect some alignment errors.** The week-4 hand-check (`docs/alignment-handcheck.md`)
  found clauses merged, split or written inline. These show up as misses and false flags
  with the suggested cause `alignment`. Check each one: some are really `segmentation`.

## Results (fill in)

| Date | Run | Agreements | Substantive recall | Substantive precision | Category accuracy | Meets threshold? |
|---|---|---|---|---|---|---|
| | | | | | | |

Held-out result (week 9, scored once):

| Date | Run | Agreement | Substantive recall | Substantive precision | Category accuracy | Meets threshold? |
|---|---|---|---|---|---|---|
| | | | | | | |

## Time comparison (ask the advisor)

| Agreement | Manual review | Reviewing the report | Notes |
|---|---|---|---|
| | | | |
