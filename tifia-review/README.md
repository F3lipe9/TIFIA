# tifia-review

The pipeline that compares an executed TIFIA loan agreement with the template it was
drafted from, and produces a deviation report and a two-page executive summary as PDFs.
What the project is and who it is for: [`../README.md`](../README.md).

## Contents

- [Setup](#setup)
- [Running it](#running-it)
- [How the pipeline works](#how-the-pipeline-works)
- [Outputs](#outputs)
- [Configuration](#configuration)
- [Adding documents](#adding-documents)
- [Evaluation](#evaluation)
- [Debugging a stage](#debugging-a-stage)
- [Tests](#tests)
- [Code layout](#code-layout)

## Setup

Python 3.10 or newer. Commands run from this folder.

```
pip install -e ".[dev,anthropic]"
cp .env.example .env        # then put your Anthropic API key in LLM_API_KEY
```

The first run downloads a small sentence-embedding model
(`sentence-transformers/all-MiniLM-L6-v2`) for section alignment.

**Documents.** PDFs are not committed (`.gitignore`). Put templates in `data/templates/`
and agreements in `data/agreements/`, and give each a row in `data/manifest.csv` (see
[Adding documents](#adding-documents)).

**PDF printing.** Reports are HTML printed to PDF. WeasyPrint is used when it loads; on
Windows it needs the Pango libraries: in MSYS2 run
`pacman -S mingw-w64-ucrt-x86_64-pango`, set
`WEASYPRINT_DLL_DIRECTORIES=C:/msys64/ucrt64/bin`, then `pip install weasyprint`. Without
it, headless Microsoft Edge or Google Chrome prints the same HTML (`TIFIA_BROWSER` can
point to another Chromium browser). The command line says which renderer it used.

## Running it

```
python scripts/run.py                              # every executed agreement in the manifest
python scripts/run.py tla_2025_hrtac_draft_body    # only these
python scripts/run.py --no-llm                     # no API key needed: no labels or summaries
```

Each agreement is compared with the template named in its manifest `template` column
(default: `pipeline.default_template` in `config.yaml`). The command prints the counts,
where the PDFs went, and the LLM cost:

```
tla_2025_hrtac_draft_body vs tla_template_2023_public_project_revenue: 609 deviations,
421 substantive, 2 need review -> outputs\reports\..._vs_....pdf (msedge (headless));
LLM $1.13, $1.14 of $300 so far
  summary -> outputs\reports\tla_2025_hrtac_draft_body_summary.pdf
```

**Cost and time.** A first run on a full agreement makes about 40 classifier calls and 10
summary calls to Claude: roughly $1.25 and 2–3 minutes. Results are cached, so a rerun of
an unchanged agreement is free and takes under a minute (mostly reading the PDFs).

## How the pipeline works

```
template PDF ─┐
              ├─> load ─> segment ─> align ─> diff ─> classify ─> report ─> summary
agreement PDF ┘                                          (LLM)               (LLM)
```

`tifia_review.pipeline.Pipeline.run` runs every stage for one agreement and saves each
stage's output, so any stage can be inspected or rerun from the one before.

1. **Load** (`loader.py`). Reads each PDF page by page with PyMuPDF. Keeps the PDF page
   number and the page number as printed, for citations. Pages with almost no text (a
   scanned signature page, an exhibit that is only a heading) are flagged, not compared.
2. **Segment** (`segmenter.py`). Splits each document at its headings into sections:
   `Section 6`, `6(a)`, `6(a)(i)`, schedules, exhibits. Removes running headers, footers
   and the template's drafting notes, and parses the definitions section into defined
   terms.
3. **Align** (`aligner.py`). Pairs each template section with an agreement section: by
   number, then by caption, then by meaning (sentence embeddings, threshold
   `aligner.similarity_threshold`). Unpaired sections are added or removed. A section that
   moved to another number is matched, not reported as a deletion plus an addition.
4. **Diff** (`diff_engine.py`). Compares paired sections sentence by sentence, and
   definitions term by term, after normalizing quote marks, dashes and spacing. Each
   difference becomes a deviation: added, removed, modified, renumbered, or **fill-in** (a
   template blank such as `[Borrower]` filled in as the template intends, which is not a
   negotiated change).
5. **Classify** (`classifier.py`, `llm/`). Claude labels each deviation with a category
   (taxonomy in `config.yaml`), whether it is substantive, a one-sentence explanation, and
   an evidence quote. It only labels; it never decides what changed. Code checks every
   label: the category exists, and the quote appears word for word in the agreement. A
   label that fails is retried once with the problem, then marked **needs review**.
   Fill-ins and renumberings are labeled without the model. Deviations go out 15 per call,
   3 calls at a time (`llm.max_workers`), with retries on rate limits.
6. **Report** (`report/`). The deviation report: summary counts, defined-term changes,
   substantive changes by category, sections added and removed, non-substantive changes,
   needs review, and a method note. Each deviation shows both documents' sections and
   pages, with changed words marked.
7. **Summary** (`summary/`). The two-page executive summary, one row per topic:
   - which deviations belong to each topic is decided by code, by section
     (`summary.topics.*.sections` / `count_sections`);
   - the plain-English line is written by Claude from that topic's sections only, and
     accepted only if its quotes are in the agreement word for word, every number it
     writes is in the agreement, and it does not advise or judge; otherwise the row says
     "See agreement §X";
   - bracketed draft values (`$[310,194,800]`, `[____]%`) are reported as "draft, not
     final", by code;
   - the debt service exhibit (found by its "TIFIA DEBT SERVICE" heading) is read as a
     table and summarized only if its principal adds up to the loan amount;
   - the bottom line is written from the change counts, with a fixed sentence as the
     fallback; the PDF is shortened until it fits on two pages.
   It is skipped for an agreement listed in `evaluation.holdout`.

## Outputs

Everything goes to `outputs/` (not committed; safe to delete, it is all regenerated):

| Path | What |
|---|---|
| `outputs/reports/<agreement>__vs__<template>.pdf` | The deviation report |
| `outputs/reports/<agreement>_summary.pdf` | The executive summary |
| `outputs/<run_id>/<agreement>/` | Every stage's JSON (`load_*`, `segment_*`, `align`, `diff`, `classify`), `report.html/.pdf`, `summary.html/.pdf`, and `summary.json` with the verified quotes behind each row |
| `outputs/cache.sqlite` | Cached LLM results, keyed by prompt, model and input: a changed prompt, taxonomy or model misses the cache |
| `outputs/llm_usage.csv` | Tokens and cost of every run, with the running total against `llm.budget_usd` |

## Configuration

`config.yaml`, read on every run:

| Section | What it controls |
|---|---|
| `paths` | Where documents, the manifest, outputs and the cache are |
| `loader`, `aligner` | Low-text page detection; the embedding model and match threshold |
| `pipeline.default_template` | Template for a manifest row with no `template` |
| `llm` | Provider (`anthropic`), model, effort, batch size, `max_workers` (parallel calls; raise slowly if there are no rate-limit errors), `api_retries`, prices for the cost log, budget |
| `taxonomy`, `substantive` | The categories and the definition of a substantive change. **Drafts** until the advisor confirms them (`docs/taxonomy-draft.md`) |
| `summary` | Topics (label, sections sent to the LLM, sections counted, the question asked), `category_priority` for ranking "other significant changes", page limit |
| `evaluation` | Match rules, target recall and precision, who agreed the threshold, the held-out agreement |

The API key goes in `.env` as `LLM_API_KEY` (or `ANTHROPIC_API_KEY` in the environment).

## Adding documents

1. Put the PDF in `data/agreements/` (or `data/templates/`).
2. Add a row to `data/manifest.csv`:
   - `doc_id`: short id, used on the command line and in file names;
   - `type`: `template` or `executed` (only `executed` rows are compared);
   - `template`: the template's `doc_id` to compare against;
   - `signing_date`: empty for a draft (the summary then says "Draft — not dated");
   - `notes`: must start with `File: data/agreements/<file>.pdf;`
   - `full_path` (optional): the PDF with exhibits, when the main file is the body only,
     so the summary can read the debt service schedule;
   - `exhibit_g_pages` (optional): e.g. `120-122`, if the debt service exhibit is not found
     by its heading.
3. `python scripts/run.py <doc_id>`.

Check new documents with `python scripts/inspect_documents.py`, which writes each one's
extracted text to `outputs/inspect/<doc_id>.txt`, page by page with its printed label and
review flag, for reading by hand. Known quirks are in `docs/extraction-notes.md`.

## Evaluation

```
python scripts/evaluate.py                    # score the newest runs against data/gold/*.csv
python scripts/evaluate.py --holdout          # the held-out agreement, once, at the end
python scripts/evaluate.py --causes outputs/evaluation/<time>/errors.csv
```

The answer keys come from the advisor (`data/gold/INSTRUCTIONS.md`), labeled before they
see any report. The evaluator matches predicted deviations to the answer key by section
and text, reports recall and precision (overall and for substantive changes), and writes
an error worksheet with a suggested cause for each miss (extraction, segmentation,
alignment, diff, classification, or the answer key). How to read it: `docs/evaluation.md`.

## Debugging a stage

Each stage has a script that writes its own output to `outputs/<stage>/`:

```
python scripts/segment_documents.py
python scripts/align_documents.py
python scripts/diff_documents.py
python scripts/classify_documents.py tla_2021_drle --dry-run   # calls and cost estimate, no API use
python scripts/classify_documents.py fx_03_modified            # one call, about $0.01
```

`classify_documents.py` reads `outputs/segmented/` and `outputs/diff/`, so run the segment
and diff scripts first. Its review CSV (`outputs/classify/`) has empty columns for a
reviewer to mark each label.

## Tests

```
pytest
```

About 210 tests, under a minute. They use five synthetic fixtures in `tests/fixtures/`,
built from the real template with known planted changes (`make_fixtures.py`,
`expected_deviations.json`): an added clause, a removed clause, changed numbers,
renumbered sections and a changed definition. LLM calls are replaced by fakes, so tests
cost nothing. A few tests print real PDFs and are skipped when no renderer is installed.

## Code layout

```
src/tifia_review/
  models.py          dataclasses shared by every stage
  loader.py          PDF -> pages
  segmenter.py       pages -> sections and defined terms
  aligner.py         template sections <-> agreement sections
  diff_engine.py     paired sections -> deviations
  classifier.py      deviations -> labels (LLM), with checks and cache
  llm/               client.py (Anthropic; usage and cost), prompts.py
  report/            builder.py + template.html: the deviation report, PDF rendering
  summary/           the executive summary: mapping, topics, exhibits, schedule,
                     bottom_line, builder, summarize, summary.html
  evaluator.py       scoring against the advisor's answer keys
  pipeline.py        all stages for one agreement
  cli.py             scripts/run.py and scripts/evaluate.py
  store.py           stage outputs (JSON) and the LLM cache (SQLite)
scripts/             command-line entry points
docs/                extraction notes, alignment hand-check, taxonomy draft, evaluation guide
data/                manifest, answer-key template, hand-check marks (PDFs not committed)
tests/               tests and fixtures
```
