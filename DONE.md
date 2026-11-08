# What has been done in Phase 0

As of November 1, 2026. This file records the work that is finished, step by step, and what
each step does. `do.md` lists only what is still to do; `PHASE0.md` sets the requirements.
Code lives in `tifia-review/`. **224 tests pass** (`python -m pytest -q`), and everything
below is committed on `main`.

## Pipeline at a glance

```
PDF ─> Load ─> Segment (+ recovery) ─> Align ─> Diff ─> Classify (LLM) ─> Report + Summary
                                                                              │
                                              answer keys from the advisor ─> Evaluate
```

Each stage saves its output as JSON under `outputs/<run_id>/<doc_id>/`, so any stage can be
rerun or inspected on its own.

---

## Week 1 — Setup and documents

- **Project skeleton.** `tifia-review/` with `pyproject.toml`, `config.yaml`, `.env.example`,
  `.gitignore`, `src/tifia_review/` and `tests/`. `models.py` holds the shared data types
  (documents, sections, alignments, deviations, labels) used by every stage.
- **Documents collected.** Two 2023 templates (Project Revenue and Non-Project Revenue) and
  the agreements in `data/agreements/`: four Sound Transit 2021 loans (DRLE, Federal Way,
  Northgate, O&M Facility East), the 2023 Sound Transit omnibus amendment (kept for
  reference, not comparable), the HRTAC 2025 draft (body only) and the CTB I-81 2022 draft
  (full, with exhibits).
- **Manifest.** `data/manifest.csv` describes every document: type, borrower, dates, which
  template it is compared to (`template`), a display `title`, page notes (signature pages
  without text, "[Attached.]" exhibits) and, for the summary, `full_path` and
  `exhibit_g_pages`.
- **Test fixtures.** `tests/fixtures/make_fixtures.py` builds five synthetic agreements from
  the real template, each with one planted change (added clause, removed clause, changed
  numbers, swapped section numbers, changed definition), plus `expected_deviations.json`.
  Building them from the real 147-page template makes them a harder test than the short
  template the plan described.

## Week 2 — Loading

- **Loader** (`loader.py`). Reads each PDF with PyMuPDF, keeps the printed page label next
  to the PDF page index, removes running headers and footers (lines that repeat on at least
  30% of pages), normalizes text (NFKC, line-end hyphens kept for compound words) and flags
  low-text pages.
- **Every document inspected by hand.** `scripts/inspect_documents.py` dumps the text of
  each document to `outputs/inspect/`. Findings are in `docs/extraction-notes.md`: reading
  order, headers, signature pages with no text layer, tables extracted cell by cell, and the
  non-project template's own numbering error (13(cc) printed as "Section 14.").
- **Stage storage** (`store.py`). Each stage saves and loads its JSON, so stages chain
  without recomputing.

## Week 3 — Segmentation

- **Segmenter** (`segmenter.py`). Splits a document into sections, clauses and
  sub-clauses ("Section 12", "(a)", "(xix)"), keeping a character-to-page map so every piece
  of text can be cited by page.
- **Definitions parsed.** Section 1's defined terms become their own units, so a changed
  definition is reported as a definition change rather than a big edit to Section 1.
- **TOC self-check.** The template's sections are checked against its table of contents.
- Fixture segmentation is exact; `scripts/segment_documents.py` runs it on the manifest.

## Week 4 — Alignment

- **Aligner** (`aligner.py`). Pairs each executed-agreement section with its template
  section in three passes, most reliable first: matching numbers and titles, then matching
  titles under a new number (renumbering), then text similarity with MiniLM embeddings
  (threshold 0.80). Anything left unpaired is reported as added or removed.
- **Hand-check on Federal Way.** Claude marked all 431 alignment rows
  (`data/handcheck/`, summary in `docs/alignment-handcheck.md`): 81.2% strict, 90.7%
  without the 45 merged/split/inline-clause rows, and 98.4% of the pairs it made are right.
  Its errors are rewritten clauses it failed to pair. A 0.75 threshold was measured and was
  no better, so 0.80 stays.

## Week 5 — Diffing

- **Diff engine** (`diff_engine.py`). Compares each aligned pair word by word after
  normalization, and sorts the result into deviation types: modified, added, removed,
  renumbered, and template fill-ins (blanks like "[Borrower]" filled with deal terms, which
  are not negotiated changes).
- All six planted fixture changes are found, and fill-ins are labeled as such. On DRLE
  about 1.4% of deviations are punctuation-only, and those are real edits.
- `scripts/diff_documents.py` runs it on the manifest.

## Week 6 — Classification

- **LLM client** (`llm/client.py`). `AnthropicClient` with structured JSON output, an
  effort setting, retries on rate limits (`llm.api_retries`) and a usage/cost record for
  every call. The model is `claude-sonnet-5-5` from `config.yaml`. (A brief switch to
  Gemini was reverted and its code removed; Anthropic is the only provider.)
- **Prompt** (`llm/prompts.py`). Drafted by Claude, because the technical plan's own system
  prompt was not in the folder.
- **Classifier** (`classifier.py`). Sends deviations in batches and gets back a category,
  whether it is substantive, a one-sentence explanation and evidence quotes. Every quote is
  checked against the actual text; a bad answer gets one retry with the problem stated, then
  becomes `needs_review`, as does "unclear". Fill-ins and renumberings skip the LLM.
  Passing labels are cached in `outputs/cache.sqlite`, keyed by prompt version, model and
  item, so reruns are free.
- **Parallel calls.** Three requests at a time (`llm.max_workers`); checks, caching and
  stats stay on the main thread in batch order.
- **Taxonomy draft.** `docs/taxonomy-draft.md`: draft categories and definition of
  "substantive", 10 real DRLE deviations with suggested labels, and the questions for the
  advisor meeting. The same drafts are in `config.yaml`.
- **Script.** `scripts/classify_documents.py` (`--dry-run` for a call and cost estimate,
  `--limit`) writes a review CSV to `outputs/classify/` and appends costs to
  `outputs/llm_usage.csv`.
- **First real run: HRTAC.** 609 deviations in 37 calls, about 2 minutes and $1.13; 524
  labeled by Claude, 2 needs review, all evidence quotes verified.

## Week 7 — Report

- **Report builder** (`report/builder.py`, `report/template.html`). One PDF per agreement:
  cover with a "Prototype — for professional review only" warning, summary counts, defined
  term changes, changes by category, added and removed sections, a table of
  non-substantive changes, the needs-review list and a method note. Changes are shown side
  by side with word-level deletions and insertions, and cited as "p. X (PDF p. Y)".
- **Pipeline** (`pipeline.py`). `Pipeline.run` runs every stage, saves each to
  `outputs/<run_id>/<doc_id>/` and copies the PDF to `outputs/reports/`.
- **Command line** (`cli.py`, `scripts/run.py`). Manifest-driven; `--no-llm` skips
  classification. The default template comes from `pipeline.default_template`.
- **PDF rendering.** WeasyPrint if it loads, otherwise headless Edge/Chrome (Edge is on this
  machine and handles page margins and page breaks). WeasyPrint is not installed because it
  needs Pango/GTK on Windows.
- **Answer-key package ready.** `data/gold/gold_template.csv` and `data/gold/INSTRUCTIONS.md`
  for the advisor.

## Week 8 — Evaluation (code)

- **Evaluator** (`evaluator.py`). Converts the advisor's answer-key CSV to
  `data/gold/<doc_id>.json`, stopping with line numbers on malformed rows. A tool deviation
  matches an answer-key row when it is in the same section (normalized ids) and at least
  half the excerpt's words appear in order. It reports recall, precision, substantive
  recall and precision (flagged = substantive or needs review), and category and flag
  accuracy.
- **Error worksheet.** Every miss, false flag and wrong label goes into `errors.csv` with a
  suggested cause (extraction, segmentation, alignment, diff, classification, answer key);
  `--causes` ranks the causes once they are confirmed.
- **Script** (`scripts/evaluate.py`): `--run`, `--holdout`, `--gold-dir`, `--causes`. It
  scores the newest run of each agreement and skips the hold-out unless asked.
  `config.yaml` has `evaluation.threshold_agreed` and `evaluation.holdout`; the guide is
  `docs/evaluation.md`.
- Verified on the fixtures (planted changes as answer keys: 100%). No real scores yet.

## Executive summary (`Summary_Report.md`)

- **Two-page summary per agreement** (`summary/`: mapping, topics, exhibits, schedule,
  bottom_line, builder, summarize, `summary.html`). One row per topic (nine topics plus
  "Other significant changes"), each line checked against quotes from the agreement, and a
  bottom line. Every run writes `outputs/reports/<doc_id>_summary.pdf`; it is skipped for
  the held-out agreement. Settings are in `config.yaml` `summary`.
- **Departures from the plan.** The debt-service exhibit is found by its heading "TIFIA DEBT
  SERVICE" (it is Exhibit F in the Sound Transit PDFs, which only say "[Attached.]");
  borrowing authority counts only §13(a), §13(b) and §12(a)(xix), since all of §12–13 gave a
  misleading 50 changes; exhibits missing from a body-only PDF are not listed as highlights.
- **Run on HRTAC.** About $0.13, 2 pages, all 9 rows verified. HRTAC's printed page labels
  are unreliable, so its citations use PDF pages.

## Section recovery for degraded PDFs (`Section_Recovery.md`)

- **Problem.** In the I-81 draft, 19 of 39 section headings lost their "Section N." in
  extraction and were glued to the previous paragraph, so sections merged and produced false
  deviations and wrong citations.
- **Fix** (`segment/`). `StructureCheck` notices missing sections; `TocParser` reads the
  section titles from the document's table of contents (or the template); `TitleNormalizer`
  makes titles comparable; `HeadingRecovery` searches forward, in TOC order and outside the
  TOC, for each missing title in heading form and splits there, without losing any text.
  Anything still missing marks the document **degraded**.
- **Wired in.** `config.yaml` `segmentation.recovery`; a `structure.json` per run; a
  one-line summary on the command line; a warning on the report cover when degraded; and
  `evaluate.py` skips degraded documents unless `--include-degraded`.
- **Result.** I-81: 39 of 39 sections (13 numbered, 26 recovered), every boundary checked by
  Claude in the extracted text. Documents that already segmented cleanly come out
  byte-identical. Departures from the plan: any missing section counts as degraded (the
  95% coverage rule contradicted the plan's own test 7), an extra section-number boundary
  source, and a stricter sentence-start rule.

## Documentation

- `README.md`: project overview. `tifia-review/README.md`: setup and pipeline guide.
- `docs/`: extraction notes, alignment hand-check, taxonomy draft, evaluation guide.
- Plans: `PHASE0.md`, `Summary_Report.md`, `Section_Recovery.md`.

## Spend so far

About $1.64 on the LLM before `outputs/` was cleared (the ledger in `outputs/llm_usage.csv`
restarted at $0 then), plus any runs since.
