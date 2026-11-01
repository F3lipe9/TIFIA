# tifia-review

Prototype that compares the public borrower TIFIA TLA template with an executed agreement (both PDFs) and produces a PDF deviation report with section and page citations. Runs locally on public documents only. See `../PHASE0.md` for the plan.

## Setup

```
pip install -e ".[dev,anthropic]"
cp .env.example .env   # add LLM_API_KEY
```

## Reports

```
python scripts/run.py                    # every executed agreement in data/manifest.csv
python scripts/run.py tla_2021_drle      # only these
python scripts/run.py --no-llm           # without classification (no API key needed)
```

Each agreement is compared with the template named in its manifest `template` column. The
PDF goes to `outputs/reports/<agreement>__vs__<template>.pdf`, and every stage's output to
`outputs/<run_id>/<agreement>/` (load, segment, align, diff, classify JSON, report.html and
report.pdf). Classifying the four 2021 agreements costs roughly $2-3 each the first time;
labels are cached after that.

PDFs are printed with WeasyPrint when it can load. On Windows it needs the Pango libraries:
in MSYS2 run `pacman -S mingw-w64-ucrt-x86_64-pango`, set the environment variable
`WEASYPRINT_DLL_DIRECTORIES=C:/msys64/ucrt64/bin`, and `pip install weasyprint`. Until then
the same HTML is printed by headless Microsoft Edge or Google Chrome (`TIFIA_BROWSER` can
point to another Chromium browser). The command line says which renderer it used.

## Evaluation

```
python scripts/evaluate.py                    # score the newest runs against data/gold/*.csv
python scripts/evaluate.py --holdout          # the held-out agreement, once, in week 9
python scripts/evaluate.py --causes outputs/evaluation/<time>/errors.csv
```

The answer keys come from the advisor (`data/gold/INSTRUCTIONS.md`). Results, the error
worksheet and how to read them: `docs/evaluation.md`.

## Pipeline

load → segment → align → diff → classify → report (`tifia_review.pipeline`).

Each stage also has a script for debugging that writes to `outputs/<stage>/`:

```
python scripts/segment_documents.py
python scripts/align_documents.py
python scripts/diff_documents.py
python scripts/classify_documents.py tla_2021_drle --dry-run   # calls and cost estimate
python scripts/classify_documents.py fx_03_modified            # one call, about $0.01
python scripts/classify_documents.py tla_2021_drle             # about 43 calls
```

The classifier needs `LLM_API_KEY` in `.env`. Its taxonomy and the definition of a
substantive change are drafts in `config.yaml` until the advisor confirms them
(`docs/taxonomy-draft.md`). Labels are cached in `outputs/cache.sqlite`; every run's tokens
and estimated cost go to `outputs/llm_usage.csv`.
