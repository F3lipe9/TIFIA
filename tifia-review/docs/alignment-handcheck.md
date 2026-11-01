# Alignment hand-check (Phase 0, week 4)

**Document:** Federal Way 2021 agreement (`tla_2021_federal_way`) against the 2023
project-revenue public borrower template. The aligner's thresholds were calibrated on the
DRLE agreement, so Federal Way had not been used for tuning before this check.

**Marked file:** `data/handcheck/tla_2021_federal_way__vs__tla_template_2023_public_project_revenue.csv`
(the `correct` and `notes` columns of the week-4 export, 431 rows). Rerunning
`scripts/align_documents.py` overwrites only `outputs/alignment/`, not this copy.

**Who marked it:** Claude, 2026-11-01, by reading both sections' text for every row that
was not a same-number, same-caption pair. These marks are a first pass for F to audit;
the calls most worth a second look are listed under "Judgment calls".

## Marks

- **y**: a pair is the same provision (reworded, renumbered or moved is fine); a removed or
  added section has no counterpart on the other side.
- **n**: a pairing error a reviewer would fix: the wrong partner, or a counterpart that
  exists as its own section and was missed.
- **limitation**: the counterpart exists only as part of a section (clauses merged or
  split, or a list written inline in one paragraph), so no one-to-one pairing of sections
  can be right. The aligner docstring already says Phase 0 does not handle this.

## Result

| | Rows | y | n | limitation |
|---|---|---|---|---|
| number | 148 | 146 | 2 | 0 |
| heading | 64 | 64 | 0 | 0 |
| embedding | 39 | 37 | 2 | 0 |
| removed | 113 | 58 | 16 | 39 |
| added | 67 | 45 | 16 | 6 |
| **total** | **431** | **350** | **36** | **45** |

| Accuracy | |
|---|---|
| strict (limitations count as wrong) | 350 / 431 = **81.2%** |
| limitation rows left out | 350 / 386 = **90.7%** |
| limitations count as correct | 395 / 431 = **91.6%** |
| pairs the aligner made that are right | 247 / 251 = **98.4%** |

Against the PHASE0 target of 90%, the result passes only if limitation rows are excluded
or accepted. Under the strict reading it does not.

The aligner rarely pairs wrongly. Its errors are misses: 32 of the 36 wrong rows are
REMOVED or ADDED rows whose counterpart exists.

## Wrong pairs (4 rows)

- T19(b) / E19(b): E19(b) (automatic termination on a bankruptcy event) is T19(c). The
  same error happens on DRLE.
- T12(a)(i) / E12(a)(xxix): E12(a)(xxix) terminates the 2019 loan; T12(a)(i) is
  E12(a)(i)(A).
- T21(c)(i) / E21(c)(i): T21(c)(i) only groups (A) and (B); E21(c)(i) is T21(c)(i)(A).
- T22(b) / E22(b): E22(b) is a new "Reporting" heading; T22(b), the quarterly progress
  report, is E22(b)(i), the monthly progress report.

## Missed counterparts (32 rows)

| Template | Agreement | What it is |
|---|---|---|
| 8(d)(v) | 8(d)(i) | first payment in the flow of funds: interest on senior debt |
| 12(a)(i) | 12(a)(i) | delivery of the Agreement and the TIFIA Bond (see the wrong pairs above) |
| 12(a)(xii) | 12(a)(x) | Base Case Financial Model (also missed on DRLE) |
| 12(b)(ii) | 12(b)(i) | Financial Plan for disbursements 60+ days after the Effective Date |
| 12(b)(vi) | 12(b)(iv) | insurance policies in full force |
| 15(g)(i)(A) | 15(p) | Substantial Completion notice, now its own covenant |
| 15(k), 15(k)(i)-(iii) | 15(k), 15(k)(i), (iii), (iv) | reserve and project accounts, recaptioned "Funds and Accounts" (also missed on DRLE) |
| 15(l) | 15(l) | rate covenant: "Rate Coverage" vs the tax-rate "Sufficiency Test" |
| 15(o)(i) | 15(n)(i) | Qualified Hedge required for variable-rate debt |
| 16(a)(iii) | 16(b)(i) | certificate before incurring more debt |
| 19(c) | 19(b) | automatic termination on a bankruptcy event |
| 21(a)(iii) | 21(a)(iv) | Financial Plan cash flow reporting |
| 21(b) | 21(f) | periodic coverage certificate |
| 21(c)(i)(A) | 21(c)(i) | quarterly financial statements |
| 22(b) | 22(b)(i) | construction progress report |

Most are rewritten provisions: either their cosine is under 0.80 or their wording is too
different for the embedding passes to confirm the match. In four cases one side was
already taken by a wrong pair.

## Limitations (45 rows)

- **Lists written inline in one paragraph** (31 rows): the agreement writes the template's
  separate clauses as "(A) ...; (B) ..." inside one paragraph, and the segmenter only splits
  at the start of a line. This covers T21(a)(iii)(A)-(F), T21(a)(iv)(A)-(E), T22(b)(i)-(vii),
  and T10(a)(i) and (iv) inside E10(a). The flow of funds is the largest case: the agreement's
  "First" to "Thirteenth" steps run inline through E8(d)(ii) and (iii), and nine template
  steps live there.
- **Merged** (9 rows): T12(a)(ii) into E12(a)(i); T15(b)(i), (iii) into E15(b); T15(f)(i),
  (ii), (vi) into E15(f); T15(g)(ii) into E15(g)(xvii); T16(a)(i) is E16(a)'s own text;
  T15(g)(i), whose notice list the agreement puts directly under 15(g).
- **Split** (4 rows): E12(a)(xii) (NEPA, split from T12(a)(xv)); E9(h) (the non-Business-Day
  rule, split out of T9(a)); E15(l)(i) and E15(s) (the template's one-paragraph rate covenant).
- **No text** (1 row): the agreement's signature pages are images.

## What would fix what

1. A last pass that pairs leftover sections under a paired parent when each is the other's
   best match by embedding, with a lower threshold. It would target 12(a)(xii), 12(b)(ii),
   12(b)(vi), 15(k), 15(l) and 21(a)(iii). An earlier measurement on DRLE found that
   lowering the global threshold to 0.75 was a wash, so this needs measuring there first.
2. Splitting inline enumerations after "shall:" or "following order:" in the segmenter.
   That would make most limitation rows pairable, but it changes the sections every later
   stage sees.
3. For T19(b)/T19(c): the check that a sibling reads more alike compares wording only.
   Comparing embeddings as well might catch it (untested).

**Tune on DRLE, then check on another agreement.** Federal Way has now been examined, so
tuning against it would no longer give an unbiased number. Northgate or O&M Facility
East is a good next check.

## Judgment calls

These rows depend on whether a rewritten provision counts as "the same provision". They
are marked n (missed). Marking them y instead adds 14 correct rows (strict 84.5%):
8(d)(v)/8(d)(i), 15(g)(i)(A)/15(p), 15(l)/15(l), 15(o)(i)/15(n)(i), 16(a)(iii)/16(b)(i),
21(b)/21(f), 22(b)/22(b)(i).

T19(a)(vii)/E19(a)(vi)(A) is marked y: the agreement's cross default under Related
Documents takes the place of the template's cross default under Principal Project
Contracts.
