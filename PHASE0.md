# PHASE 0 — Build and Validation Plan (v3)
 
**Updated:** November 7, 2026 (2018 template and manifest, see below)  **Replaces:** v2 (Nov 4), `phase0-technical-plan.md`, `phase0-weekly-guide.md`, and the Nov 1 status doc
**Budget:** $1,000 cap (LLM line $300)  **Felipe's capacity:** ~5 hrs/week
**Owners:** F = Felipe · C = cofounder · A = domain advisor
 
> **Status basis:** code status checked against the repo on Nov 4, 2026. People status from F on Nov 5, 2026. ✅ = done · ⚠️ = partly done or needs attention · ⬜ = not done · ❓ = unknown.
 
### What changed in v3 (Nov 5)
 
- **2018 public-borrower TIFIA template found** (`tifia-public-borrower-template-updated-5232018`). Likely baseline for the Sound Transit agreements (D2) and possibly I-81. Body-only PDF is pp. 1–91.
- **2018 template numbers sections differently from 2023** (40 sections; several key sections shifted by +1). Affects the summary PDF topic map and the alignment results (section 3).
- **Alignment audit on hold** until Federal Way is rerun against the correct template.
- **Interviews:** just starting.
- **Advisor:** committed verbally; **will not sign anything**. The gate criterion was rewritten to be about what A delivers (D9).
- **Advisor meeting Wednesday, Nov 11.** Run it as the taxonomy meeting (Step 1).
- New open questions: HRTAC held-out contamination (D8), UMD IP exposure (section 12a).

### Update Nov 7

- **2018 template added** to `data/templates/tifia-public-borrower-template-2018.pdf` and the manifest (`tla_template_2018_public`). Exported from the .doc with Word (139 pp.; body + signature page pp. 1–92). It loads cleanly: 40/40 sections from the TOC, no recovery needed.
- **Manifest updated:** new columns `status`, `role`, `source_pages`; the four Sound Transit agreements now point at the 2018 template; I-81 Regular *body* row added (section 3).
- **Headers/footers fixed:** I-81's `ERRATA COPY/VERSION` and HRTAC's `Exhibit J-5` / `Exhibit I - 1` stamps are now removed; the loader detects margin text by page position (Step 3).
- **Finding:** the Sound Transit agreements follow the 2018 text but **drop 2018's §11 "Reserved"**, so their section numbers match 2023 numbering (section 3). Word overlap with the 2018 template is only slightly higher than with 2023 (see D2). A's confirmation still matters.
---
 
## 1. Goal and gate
 
**Goal:** prove, with evidence, whether financial and municipal advisors will use and pay for a cited TIFIA deviation report.
 
**Go/no-go gate (all required):**
 
| Criterion | Target | Source of the result |
|---|---|---|
| Advisors say review is a major time cost | ≥ 50% of interviews, **with at least N interviews** (N to be set before the first tally, D10; proposal: 8) | C's interview notes and tally |
| Written LOI or email naming a price | ≥ 1 | Demo follow-ups |
| Firms agreeing to pilot | ≥ 2 | Demo follow-ups |
| Held-out accuracy on substantive deviations | Threshold agreed with A **before** scoring | `evaluate.py --holdout` |
| **Advisor delivered** *(replaces "signed agreement", D9)* | Taxonomy meeting held; threshold confirmed by A in writing (email); answer keys for all 4 agreements delivered by the agreed date | Meeting notes, A's email, `data/gold/` |
 
**Three of five criteria depend on people, not code.** The code is mostly built. **The people work is the critical path:** interviews are only now starting, and demos and LOIs come after them.
 
**Gate date:** ⬜ not set. With interviews just starting, it is almost certainly later than week 10. Choose an option in section 10 and write down a fixed date.
 
---
 
## 2. Where things stand
 
| Area | Status |
|---|---|
| Loading, segmentation, diffing (weeks 1–5) | ✅ Built and tested (224 tests) |
| Alignment hand-check | ⚠️ Federal Way: **81.2% strict**, 90.7% excluding merged/split clauses, measured against the **2023** template. **Must be rerun against the 2018 template** before the audit (Step 7) |
| Classifier | ✅ API key in `.env`; run on HRTAC (609 deviations, $1.13) and I-81 Regular full (545 deviations, $1.08, in `llm_usage.csv`) |
| Reports | ✅ Classified report + summary PDF for I-81 Regular in `outputs/reports/` |
| Evaluator | ✅ Built and verified on fixtures; ⬜ waiting on answer keys |
| Taxonomy, threshold | ⬜ Draft only. To be settled at the Nov 11 meeting |
| 2018 template | ✅ In `data/templates/` and the manifest (`tla_template_2018_public`); Word export of the .doc, 139 pp. Segments cleanly (40/40). Federal Way `--no-llm` run against it: 770 deviations. ⚠️ Not an official PDF, and footnote/option-bracket checks (Step 3) not yet run on it |
| Interviews | ⚠️ Just starting *(C)* |
| Advisor commitment | ⚠️ Committed verbally; won't sign. Gate criterion revised (D9) |
| Section recovery, summary PDF, parallel classification | ✅ All three built (sections 6–8); hand-checks still open; summary topic map assumes 2023 numbering |
 
---
 
## 3. Document set
 
### Templates
 
| File | Use |
|---|---|
| `tla_template_public_project_revenue_2023_body.pdf` | Baseline for **HRTAC** (2025). Template pp. 1–97 (body through signature page) |
| `tifia-public-borrower-template-2018.pdf` (manifest: `tla_template_2018_public`) | **Added Nov 7.** 2018 public-borrower template (project revenue version, "5/23/2018" on the cover), 139 pp.: body Sections 1–40 and signature page pp. 1–92, schedules pp. 93–97, exhibits A–O pp. 98–139. Source: `tifia-public-borrower-template-updated-5232018.doc`, exported with Word for Microsoft 365. **Baseline for Sound Transit (2021)** in the manifest; confirm with A. If an official PDF exists, use it instead (the exported file's page breaks may not match the original) |
| `TIFIA_Public_Borrower_TLA_Template.pdf` (147 pp.) | Reference only. Its exhibits are lettered differently from executed agreements |
| `TIFIA Non-Project Revenue TLA Template.pdf` (2023) | Baseline for **I-81**. Kept: I-81 Regular matches it much better than the 2018 template (83% of its body words vs 74%) and uses 39 sections like 2023 |
 
### Section numbering: 2018 vs 2023
 
The 2018 template has **40 sections**. Several topics used by the summary PDF are numbered differently:
 
| Topic | 2023 | 2018 |
|---|---|---|
| Events of default and remedies | §19 | §20 |
| Accounting and audit | §20 | §21 |
| Project oversight and monitoring | §22 | §23 |
| Notices | §36 | §37 |
| Termination | §38 | §39 |
 
What this means:
- The **summary PDF topic map** must match on **section title** (or keep one map per template version), not a fixed section number.
- **Alignment by section ID** against the wrong template misaligns everything after the shift. Rerun Federal Way, DRLE and Northgate against the 2018 template before trusting any alignment number.
- Record the template version per agreement in the manifest (`template` column) and in each report's cover.
- **Sound Transit is a hybrid (found Nov 7).** The four 2021 agreements have **39** sections: they follow the 2018 text (e.g. §21 "Financial Plan, Statements, and Reports") but drop the 2018 template's §11 "Reserved", so 2018 §12–40 become §11–39. Their numbers therefore match **2023** numbering (Events of Default §19, Notices §36, Termination §38). The aligner already pairs these by heading (29 body sections), so alignment is not broken by the shift. For the summary topic map, numbers taken from the **template** side are 2018 numbers and numbers from the **agreement** side are 2023-style, which is one more reason to match on title.
### Agreements and their roles
 
| doc_id | Borrower / year | Status | Template | Role |
|---|---|---|---|---|
| `tla_2021_drle` | Sound Transit, 2021 | Executed | `tla_template_2018_public` *(confirm with A)* | Tuning; classifier label review |
| `tla_2021_federal_way` | Sound Transit, 2021 | Executed | `tla_template_2018_public` *(confirm with A)* | **Labeled** (scored); alignment hand-check |
| `tla_2021_northgate` | Sound Transit, 2021 | Executed | `tla_template_2018_public` *(confirm with A)* | Alignment recheck (tuning) |
| `tla_2021_om_facility_east` | Sound Transit, 2021 | Executed | `tla_template_2018_public` *(confirm with A)* | **Labeled** (scored) |
| `tla_2025_hrtac_draft_body` | HRTAC, 2025 | **Draft** (Sep 2025 committee package, pp. 45–152) | `tla_template_2023_public_project_revenue` | **Held-out** (see D8: possibly compromised) |
| `tla_2022_ctb_i81_rural_draft_body` | Virginia CTB, 2022 | **Draft** (Oct 2022 CTB package, pp. 182–255) | 2023 non-project likely, as for I-81 Regular *(confirm with A)* | **Labeled** (scored). ⬜ No PDF, no manifest row yet |
| `tla_2022_ctb_i81_regular_draft_body` | Virginia CTB, 2022 | **Draft** (pp. 38–110) | `tla_template_2023_public_non_project_revenue` | **Robustness test only, never scored** (section recovery is tuned on it). Exhibit G read from the `_full` PDF |
| `tla_2022_ctb_i81_regular_draft_full` | Same draft, body + exhibits (pp. 38–155) | Draft | Same | **Reference only**: manifest `type` = `reference`, so `run.py` skips it unless named; it is the body row's `full_path` |
 
Keep the `_full.pdf` versions (with exhibits) in `reference/`, outside `data/agreements/`. The summary PDF reads Exhibit G from them through the manifest's `full_path` column. *(Nov 7: `reference/` not created yet; the I-81 full PDF is still in `data/agreements/`, marked `reference` in the manifest. If you move it, update the `notes` `File:` path and the body row's `full_path`.)*
 
### Held-out rules
 
HRTAC was chosen over O&M Facility East as a stronger held-out test: different borrower, drafted under the 2023 template, not tuned on. Rules:
- Only check that it runs without errors; look at totals, never the report.
- Generate its full report and summary only after its held-out score is recorded.
> ⚠️ **Rule already bent:** per `DONE.md`, HRTAC was fully classified and its summary was generated and all 9 rows hand-verified. See D8.
 
### Data caveats to state in the memo
 
- The four Sound Transit agreements are near-duplicates (same borrower, same day). The two I-81 drafts likely are too. **Count each set as one borrower** when describing test-set variety.
- HRTAC and the I-81 agreements are **drafts**, not executed agreements. Final versions may differ. Keep looking for executed versions (later meeting packages, bond disclosures).
- The 2018 template was converted from .doc; page citations against it may differ from an official PDF.
### Manifest
 
Updated Nov 7 (`tifia-review/data/manifest.csv`). Column order: `doc_id, title, type, status, role, borrower_type, signing_date, template_version, template, source, source_pages, notes, full_path, exhibit_g_pages`. The pipeline still reads only `type`, `template`, `notes` (`File: ...`), `full_path` and `exhibit_g_pages`; the new columns are records for the memo and the answer-key package.

| Column | Purpose | Done |
|---|---|---|
| `status` | `executed` / `draft` (plus `synthetic` for fixtures, `n/a` for templates) | ✅ HRTAC and both I-81 rows are `draft`; `type` stays `executed` so the pipeline compares them |
| `full_path` | Agreement with exhibits, for Exhibit G | ✅ (column exists, plus `exhibit_g_pages`); set on I-81 Regular body (full PDF, p. 98) |
| `source_pages` | e.g. "HRTAC Finance Committee package 9/18/2025, pp. 45–152 (body only)" | ✅ HRTAC, I-81 body (pp. 38–110) and full (pp. 38–155); "standalone PDF" for the rest. ⚠️ I-81 full range is derived from its page count, and the HRTAC full package range (pp. 45–244) is not recorded since only the body is in the repo |
| `role` | `tuning` / `labeled` / `holdout` / `robustness` (plus `template`, `fixture`, `reference`, `excluded`) | ✅ DRLE, Northgate `tuning` · Federal Way, O&M Facility East `labeled` · HRTAC `holdout` · I-81 body `robustness` · I-81 full `reference` · omnibus amendment `excluded`. ⚠️ `config.yaml` `evaluation.holdout` is still empty; the evaluator reads that, not this column (D4) |
| `template` | Template per agreement | ✅ Sound Transit ×4 → `tla_template_2018_public` (`template_version` "2018 (likely; confirm with A)") · HRTAC → 2023 project revenue · I-81 → 2023 non-project. Revisit after A confirms on Nov 11 |

Manifest rows: ✅ HRTAC body · ✅ I-81 Regular *full* (now `type` = `reference`) · ✅ I-81 Regular *body* · ✅ 2018 template · ⬜ I-81 Rural (no PDF, no row) · ⬜ `reference/` folder doesn't exist yet

Every PDF in `data/agreements/` and `data/templates/` now has a manifest row.
 
---
 
## 4. Decisions
 
| # | Decision | Status / recommendation | Deadline |
|---|---|---|---|
| D1 | Classification model | Keep `claude-sonnet-5-5`. **Lock before the label review** and don't change it after | Before label review |
| D2 | Template for the 2021 Sound Transit agreements | **Nearly settled: 2018 public-borrower template** (set in the manifest Nov 7). Evidence is weaker than hoped: on Federal Way's body, 61.7% of the 2018 template's words match vs 59.0% for 2023, but the diff finds *more* deviations against 2018 (770 vs 735, `--no-llm`). Only about half of Federal Way's words match either template, so many deviations will be negotiated text whichever is used. A confirms which template applies to each agreement (including I-81) at the Nov 11 meeting; ask whether an intermediate 2019–2021 version exists (it would explain the dropped §11) | Nov 11 |
| D3 | How to report alignment | Rerun against the 2018 template, audit marks, report **strict first**, merged/split-excluded as context | Memo |
| D4 | Held-out agreement | HRTAC draft body, **subject to D8**. `config.yaml` `evaluation.holdout` still empty | Before any scoring |
| D5 | WeasyPrint | Optional; Edge output already meets requirements | Any time |
| D6 | Answer-key scope | Main body only (matches pipeline) | Before answer-key package |
| D7 | Summary PDF scope | Nine topic rows + "Other significant changes"; **topic map by section title**; cut Exhibit G parsing first if short on time | Before demos |
| **D8** | **Is HRTAC still a clean held-out?** | Open question for F: did any detection, normalizer, or classifier code change after HRTAC output was viewed? **If no:** keep HRTAC for deviation-detection accuracy and disclose the summary exposure in the memo. **If yes:** find a new, untouched agreement from a different borrower. Either way, A must not see HRTAC output before labeling | Before answer-key package |
| **D9** | **Advisor criterion** | A won't sign anything. Gate criterion changed from "signed agreement" to "advisor delivered" (section 1). Changed **before** any results, for the stated reason | ✅ Nov 5 |
| **D10** | **Minimum interview count** | Set N before the first tally (proposal: 8). Without it, "≥ 50%" can pass on 2 interviews | Before the first tally |
 
---
 
## 5. Order of work (avoids rework)
 
### Step 0 — This week (F, C in parallel)
 
- [x] **F:** add API key; run the classifier — done on HRTAC and I-81 Regular
- [x] **F + C:** D2 template source found (2018 template); per-agreement confirmation from A on Nov 11
- [ ] **F:** answer D8; then record D4 in `config.yaml` (`evaluation.holdout`)
- [x] **F:** add the 2018 template to `data/templates/` and the manifest (Nov 7, Word export). Still swap in an official PDF if one turns up
- [x] **F:** add the I-81 Regular *body* manifest row (Nov 7)
- [ ] **F:** get the I-81 Rural PDF and add its manifest row; create `reference/`
- [x] **F:** add manifest columns `status`, `source_pages`, `role`, `template` (Nov 7; `template` set per section 3)
- [x] **F:** confirm `ERRATA COPY/VERSION` and HRTAC's `Exhibit I - 1` lines are actually removed. They were **not**; fixed Nov 7 (Step 3)
- [ ] **C:** keep booking interviews; agree N (D10)
- [ ] **F + C:** prepare the Nov 11 meeting (Step 1)
### Step 1 — Taxonomy meeting = advisor meeting, Wednesday Nov 11 (F + C + A, 1 hr)
 
**Agenda:**
1. A labels **10 example deviations from DRLE, with no suggested labels**. DRLE isn't an agreement A will score.
2. Decide: categories and definitions; what "substantive" means; **category priority order** (used by the summary PDF); explanation length.
3. **Accuracy threshold** (starting proposal: recall ≥ 90%, precision ≥ 70% on substantive).
4. **Which template applies to each agreement** (Sound Transit, I-81 Rural/Regular). Confirms D2.
5. A's **time estimate and delivery date** for labeling the 4 agreements.
6. Ask whether A knows other borrowers' agreements or executed versions of the drafts.
**Rules for the meeting:** show **no tool output** for Federal Way, O&M Facility East, I-81 Rural, or HRTAC. If anything is demoed, use DRLE only.
 
**Afterward (same day):** send A a short email summarizing the taxonomy, threshold, template choices, and delivery date, and ask him to confirm by reply. That reply is the written evidence for the gate (D9). Then write taxonomy, priority, and threshold into `config.yaml`. Don't change them after scoring starts.
 
### Step 2 — Send the answer-key package (C, right after Step 1)
 
Contents:
- `gold_template.csv`, `INSTRUCTIONS.md`
- **The exact template file for each agreement** (from D2)
- Agreements to label: Federal Way, O&M Facility East, I-81 Rural, HRTAC (or the replacement held-out, per D8)
- Scope: main body only (D6)
- **Rule: label before seeing any tool output**
- Delivery date agreed on Nov 11
Blocked on: D2 confirmation, D8, I-81 Rural PDF.
 
### Step 3 — Normalizer checks (F, ~2 hrs)
 
- [x] Template drafting footnotes stripped — `segmenter.split_footnotes`, with tests
- [x] Option brackets like `[each][at least one (1)]` treated as `fill_in` — `diff_engine.py`
- [ ] **2018 template:** rerun the footnote and option-bracket handling on it (~536 brackets, inline footnote markers such as `[BORROWER]1`); confirm they don't show up as deviations
- [x] Header/footer lines: `ERRATA COPY/VERSION`, `Exhibit I - 1`. **Fixed Nov 7.** Neither was being removed: ERRATA is printed at the top but extracted as each page's last line, outside the loader's first-3-lines zone; HRTAC's constant footer stamp (`Exhibit J-5`, pp. 7–107) was being read as the page number. The loader now finds margin text **by position** (top/bottom 8% of the page), removes margin lines that repeat on ≥ 30% of pages, treats a constant label-like line as a stamp, and keeps only the page number or exhibit label (`printed_label`). One-off margin text (`EXECUTION VERSION`, `NRF Draft 10/14/2022`, signature-page captions) is kept. Result: templates, fixtures, Sound Transit, omnibus unchanged; I-81 Regular body 513 → 497 deviations (16 were ERRATA); HRTAC body pages have no printed number, so its citations read "PDF p. N". Details in `docs/extraction-notes.md`; tests in `tests/test_loader.py` (228 pass). ⚠️ I-81's cached labels no longer match its text, so its next classified run re-labels (~$1)
- [ ] Run `--no-llm` on I-81 Rural (counts and alignment) once the PDF is in
### Step 4 — Section recovery ✅
 
See section 6. ✅ Rechecked Nov 7: the 2018 template comes through clean (40/40 sections from the TOC, none recovered, 100% text coverage).
 
### Step 5 — Parallel classification ✅
 
See section 7. One open item: per-call timing log.
 
### Step 6 — Label review on DRLE (F, ~1.5 hrs, ~$3)
 
Blocked on Step 1 (final taxonomy) and D1 (model lock). Run against the **2018 template**.
- Classify DRLE with the final taxonomy; read every label (`category_ok`, `substantive_ok`, `explanation_ok`, notes)
- Fix category definitions, not code, when a category keeps going wrong
- Test prompt changes with `--limit 20` before full reruns
### Step 7 — Alignment rerun and audit (F, 2–3.5 hrs)
 
1. **Rerun DRLE, Federal Way, Northgate against the 2018 template.** Record the new strict and merged/split-excluded rates.
2. **Then** audit the Federal Way marks, starting with the judgment calls. Don't audit the old 431 marks; they were made against the 2023 template.
3. Optional fix: pair leftover clauses within paired sections; tune on DRLE, recheck on Northgate.
### Step 8 — Summary PDF: template-aware topic map and hand-check (F, ~3 hrs)
 
- [ ] Change the topic map to match on **section title** (or per-template maps); add a test with a 2018-numbered fixture
- [ ] Hand-check every row on **2 non-held-out** agreements (e.g. DRLE and I-81 Regular)
- [ ] Test the Exhibit G digest on a real filled-in schedule (I-81 has tables with a blank rate)
### Step 9 — Score (F, ~4 hrs, when answer keys arrive)
 
- Save keys as `data/gold/<doc_id>.csv`; run labeled agreements (excluding the held-out); `python scripts/evaluate.py`
- Assign a cause to every error: `extraction` · `segmentation` · `alignment` · `diff/normalization` · `classification` · `answer key`
- Ask A: manual review time vs. time to review the report
- Never loosen the matching rule or move the threshold after seeing numbers
### Step 10 — Fix and held-out score (F, ~3.5 hrs)
 
- Fix the top 1–2 causes only, each with a test reproducing the error first
- Score the held-out **once**: `python scripts/run.py <holdout_id>` then `python scripts/evaluate.py --holdout`
- Then generate its report and summary for demos
- Record D8's outcome in the memo
### Step 11 — Demos (F + C, 15 min each, 2–3 advisors from the interviews)
 
1. Problem in their words (2 min)
2. **Summary PDF first**, then one full report
3. "Is this accurate? Would it save time? What's missing? Would you send the summary to your client?"
4. **Price question**, with X decided beforehand: "$X per deal or per seat?"
5. Ask for an LOI or pilot
6. If asked "why not just use Claude?", use the result from the benchmark in section 9
Record answers word for word. A demo without a price question doesn't count toward the gate.
 
### Step 12 — Go/no-go memo (F + C, ~3 hrs)
 
Gate table with sources; evidence (quotes, accuracy, time comparison, benchmark); decision; Phase 1 resourcing if go. A failed criterion is reported as failed. State the D9 criterion change and its reason.
 
---
 
## 6. Feature: TOC-guided section recovery ✅
 
**Status:** built in `segment/` (`toc.py`, `titles.py`, `recovery.py`, `structure.py`), wired into the pipeline, report cover and `evaluate.py --include-degraded`. I-81 Regular → 39/39 sections; clean documents byte-identical. Tests in `tests/test_recovery.py`.
 
**Template-title fallback:** when a document has no TOC, recovery uses the template's titles. Make sure it uses the titles from **the agreement's own template version** (2018 vs 2023), since the section lists differ.
 
**Problem:** some PDFs drop auto-generated section numbers. In I-81 Regular, 19 headings are missing and glued to the previous paragraph (`party.TIFIA Loan Amount. The principal…`), so sections merge.
 
**Approach:** when the segmenter finds fewer sections than expected, read titles from the document's TOC (or the template's titles if there's no TOC), search **forward** through the body for each missing title, and split there.
 
| Class | Responsibility |
|---|---|
| `TocParser` | Find TOC pages; parse entries, including wrapped titles |
| `TitleNormalizer` | Normalize whitespace, quotes, dashes, with an offset map back to the original text |
| `HeadingRecovery` | Bounded forward search; split glued text; re-run subsection parsing |
| `StructureCheck` | Section coverage, order, duplicates, text coverage → `complete` / `recovered` / `degraded` |
 
**Rules:** repair only (keep found headings); search only between the previous and next found section; title + period, not in parentheses; start after the TOC; never lose text; degraded documents flagged on the report and excluded from scoring.
 
**Known limit:** missing subsection labels like "(a)" aren't recovered; citations land at section level.
 
**Not now:** fuzzy title matching and order-constrained embedding fallback (Phase 1, only if a document needs them).
 
Full detail: `section-recovery-plan.md`.
 
---
 
## 7. Feature: parallel classification ✅
 
- [x] 3 workers (`llm.max_workers`); raise only if no rate-limit errors
- [x] Exponential backoff on 429s (SDK retries, `llm.api_retries: 6`)
- [x] Single-threaded writes to the cache and `llm_usage.csv`
- [x] Deterministic batch order
- [x] Test: several workers give output identical to one worker — `test_parallel_run_matches_a_sequential_one`
- [ ] **Log time per call**; check for retries and long outputs
- [x] Prompt caching on the system prompt
- **Not now:** Batches API. Revisit in Phase 1.
---
 
## 8. Feature: 2-page executive summary PDF ✅ built
 
**Status:** built in `summary/` with all nine topic rows, "Other significant changes", Exhibit G digest, bottom line, disclaimer footer, and fallbacks. Generated for HRTAC and I-81 Regular. ⚠️ **Topic map assumes 2023 numbering** (see section 3). ⬜ Hand-check on 2 non-held-out agreements not done. ⬜ Exhibit G digest untested on a real filled-in schedule.
 
**Reader:** agency or client executive. **Positioning:** a deliverable the advisor hands to their client.
 
**One row per topic:** plain-English summary (LLM, verified quote) · changed from template (code, deviation IDs) · source (section, page).
 
| # | Row | 2023 sections | 2018 sections |
|---|---|---|---|
| — | Header + 2–3 sentence bottom line | Cover; topic change counts | same |
| 1 | The project | Recitals, §1 | Recitals, §1 |
| 2 | Borrowing authority: state law **and** authorizing resolution | §1, §12, §13 | *verify by title* |
| 3 | Loan amount | Recitals, §3 | Recitals, §3 |
| 4 | Interest rate | §6 | §6 |
| 5 | Payment schedule, with **Exhibit G digest** | §5, §7, §9, §10, Exhibit G | §5, §7, §9, §10, Exhibit G |
| 6 | Events of default and remedies | §19 | §20 |
| 7 | Accounting and audit | §20 | §21 |
| 8 | Project oversight and monitoring | §22 | §23 |
| 9 | Notices and termination | §36, §38 | §37, §39 |
| 10 | Other significant changes | Top 3 unmapped substantive deviations | same |
 
The borrowing-authority rule currently counts §13(a), §13(b), §12(a)(xix) (2023 numbering). Check the matching subsections in the 2018 template (§12 Compliance with Laws, §13 Conditions Precedent, §14 Representations) by title before running on 2018-based agreements.
 
**Rules:** describe, never advise; "Prototype — not legal or financial advice" footer; draft blanks shown as "(draft, not final)"; `[to be inserted]` shown as "Not yet inserted"; failed quote → "See agreement §X"; hard 2-page limit; code ranks and maps deviations, the LLM only writes.
 
**If short on time, cut in order:** Exhibit G parsing → bottom line (template sentence) → "Other significant changes" row.
 
Full detail: `summary-pdf-plan.md`.
 
---
 
## 9. Competitor benchmark (~1 hr)
 
Run Anthropic's open-source Legal plugin (`/review-contract`) in the Claude app on a non-held-out agreement (e.g. I-81 Rural body), with a short TIFIA playbook written from the template. Compare with the pipeline: deviations caught and missed, citation accuracy. Record in the memo.
 
- If it comes close, differentiation is weaker than assumed; say so.
- If it misses or miscites, that's the answer to "why not just use Claude?"
- Public documents only.
---
 
## 10. Time and money
 
| Who | Remaining |
|---|---|
| **F** | **~21–25 hrs** (Nov 5 estimate, replacing the stale 37–42): manifest, docs, 2018 template setup ~2 · normalizer checks ~1.5 · per-call timing ~0.5 · label review ~1.5 · alignment rerun + audit ~2–3.5 · summary topic map + hand-check ~3 · benchmark ~1 · scoring ~4 · fix + held-out ~3.5 · demos + memo ~3 (+ demo time). **At 5 hrs/week that's about 4–5 weeks.** |
| **A** | Nov 11 meeting (1 hr); labeling 4 agreements (estimate requested Nov 11); review-time question |
| **C** | Interviews (critical path), answer-key package, agreement search, demos, follow-ups |
 
**The code work will likely finish before the people work.** Interviews are just starting, demos come from interviewees, and LOIs follow demos. Choose one of these and **write down a fixed gate date**:
1. Cut the summary PDF to page 2 only
2. Defer Exhibit G parsing
3. Raise F's hours temporarily
4. Accept a later gate date, fixed now
Don't drift past the gate without choosing one of these.
 
**LLM spend:** ~$3 per real agreement first run (actual: ~$1.10 per agreement so far), ~$0.01 per fixture, under $0.50 per summary. Rest of Phase 0: about $25–50. Reruns against the 2018 template add ~$3–5.
 
---
 
## 11. Cut order if behind
 
1. Summary PDF Exhibit G parsing
2. Summary PDF page 1 / topic summaries (keep change counts)
3. Category accuracy metric
4. Report polish
5. Alignment improvement (keep as known limitation)
**Never cut:** hand inspection · quote verification · structure check · A labeling before seeing output · the held-out rule · the price question in demos.
 
---
 
## 12. Rules that protect the result
 
- Code finds deviations; the LLM only labels and explains.
- Every flag cites section and page, with verified quotes.
- Taxonomy and threshold are set before labeling and scoring.
- A labels before seeing any tool output.
- The held-out is scored once, after fixes are done.
- I-81 Regular is never scored.
- One model, locked before label review.
- Report strict numbers first.
- Each agreement is compared against its own template version.
- Gate criteria change only before results, with the reason written down.
### 12a. Open question: UMD IP exposure (not legal advice; confirm with a lawyer)
 
The belief is that "if A signs, UMD takes equity." University IP policies (UMD IV-3.20(A)) generally decide ownership by **who created the work and whether significant university resources were used**, not by whether a contract was signed. Not signing may not protect the company, and C working under A may matter more.
 
To clarify:
- Is A UMD faculty or staff?
- Is C paid by UMD in the role under A? Is any work done on UMD time, equipment, or accounts?
- Ask UM Ventures (College Park) or a startup lawyer before Phase 1, before equity or investors are involved.
---
 
## 13. Reference
 
| What | Where |
|---|---|
| Run pipeline | `python scripts/run.py [doc_id ...] [--no-llm] [--summary-only]` |
| Classify | `python scripts/classify_documents.py <doc_id> [--dry-run] [--limit N]` |
| Score | `python scripts/evaluate.py [doc_id ...] [--holdout] [--causes errors.csv] [--include-degraded]` |
| Settings | `config.yaml` (taxonomy, priority, threshold, holdout, model, workers, recovery, summary) |
| Documents | `data/manifest.csv`, `data/templates/`, `data/agreements/`, `reference/` |
| Structure results | `outputs/<run_id>/structure.json` |
| Cost ledger | `outputs/llm_usage.csv` |
| Feature plans | `section-recovery-plan.md`, `summary-pdf-plan.md` |
| Full roadmap | `BUILD.md` |