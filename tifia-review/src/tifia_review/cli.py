"""Command line. main (scripts/run.py): run the pipeline on the executed agreements in
data/manifest.csv, each against the template in its `template` column (config.yaml
pipeline.default_template if that is empty), and write one PDF report per agreement to
outputs/reports/. evaluate_main (scripts/evaluate.py): score the runs against the
advisor's answer keys (week 8).

    python scripts/run.py                    # every executed agreement in the manifest
    python scripts/run.py tla_2021_drle      # only these
    python scripts/run.py --no-llm           # no classification, so no API key needed

Classification needs LLM_API_KEY in .env. Labels are cached, so a rerun pays only for
what changed; each agreement's tokens and estimated cost go to outputs/llm_usage.csv.
"""

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from tifia_review.evaluator import (
    ERROR_COLUMNS, GoldError, RunContext, Scores, convert_gold, error_rows, evaluate,
    load_gold, percent, rank_causes, results_json, results_markdown, threshold_verdict,
)
from tifia_review.llm.client import append_usage_log
from tifia_review.models import Alignment, Document, LabeledDeviation, SegmentedDocument
from tifia_review.pipeline import Pipeline, Source
from tifia_review.store import RunStore

_FILE = re.compile(r"File: ([^;]+\.pdf)")


def read_manifest(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return {row["doc_id"]: row for row in csv.DictReader(f)}


def source(row: dict, root: Path) -> Source:
    m = _FILE.match(row["notes"])
    if not m:
        raise SystemExit(f"manifest row {row['doc_id']} names no PDF (notes start 'File: ...')")
    return Source(row["doc_id"], row["type"], root / m.group(1), row.get("title", ""),
                  row.get("signing_date", ""), row.get("template_version", ""))


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executed", nargs="*", help="executed doc_ids (default: all)")
    parser.add_argument("--no-llm", action="store_true",
                        help="skip classification; the report says so")
    args = parser.parse_args(argv)

    root = Path(root or Path.cwd())
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    for name in ("tifia_review.loader", "tifia_review.segmenter"):
        logging.getLogger(name).setLevel(logging.ERROR)
    load_dotenv(root / ".env")
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    manifest = read_manifest(root / config["paths"]["manifest"])
    executed = args.executed or [d for d, row in manifest.items() if row["type"] == "executed"]
    if unknown := [d for d in executed if d not in manifest]:
        raise SystemExit(f"not in the manifest: {', '.join(unknown)}")

    try:
        pipeline = Pipeline(config, root, classify=not args.no_llm)
    except RuntimeError as e:
        raise SystemExit(f"{e}\nTo make reports without classification, add --no-llm.")
    llm = config["llm"]
    print(f"run {pipeline.run_id}: {len(executed)} agreement(s)")
    for doc_id in executed:
        template_id = manifest[doc_id].get("template") or config["pipeline"]["default_template"]
        result = pipeline.run(source(manifest[doc_id], root), source(manifest[template_id], root))
        c = result.counts
        line = (f"{doc_id} vs {template_id}: {c['total']} deviations, {c['substantive']} "
                f"substantive, {c['needs_review']} need review -> "
                f"{result.pdf.relative_to(root) if result.pdf.is_relative_to(root) else result.pdf}"
                f" ({result.renderer})")
        if result.usage is not None:
            cost = result.usage.cost(llm["price_per_mtok"])
            total = append_usage_log(root / config["paths"]["outputs_dir"] / "llm_usage.csv", {
                "time": datetime.now().isoformat(timespec="seconds"), "executed": doc_id,
                "template": template_id, "model": pipeline.classifier.client.model_id,
                "deviations": c["total"], "llm_labels": result.stats.get("llm", 0),
                "cached": result.stats.get("cached", 0),
                "needs_review": result.stats.get("needs_review", 0),
                "calls": result.usage.calls, "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cache_write_tokens": result.usage.cache_creation_input_tokens,
                "cache_read_tokens": result.usage.cache_read_input_tokens,
                "cost_usd": round(cost, 4)})
            line += f"; LLM ${cost:.2f}, ${total:.2f} of ${llm['budget_usd']} so far"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --- evaluation (week 8) ---------------------------------------------------------------

_RUN_ID = re.compile(r"^\d{8}-\d{6}$")


def latest_run(outputs: Path, doc_id: str) -> str | None:
    """The pipeline run in outputs/ that classified doc_id last (by file time: run ids are
    local times, which repeat an hour when daylight saving time ends)."""
    runs = [p for p in outputs.iterdir() if p.is_dir() and _RUN_ID.match(p.name)
            and (p / doc_id / "classify.json").exists()] if outputs.exists() else []
    if not runs:
        return None
    return max(runs, key=lambda p: (p / doc_id / "classify.json").stat().st_mtime).name


def evaluate_main(argv: list[str] | None = None, root: Path | None = None) -> int:
    """Score pipeline runs against the advisor's answer keys (PHASE0 week 8).

    python scripts/evaluate.py                  # every agreement with an answer key
    python scripts/evaluate.py tla_2021_drle    # only these
    python scripts/evaluate.py --holdout        # the held-out agreements, once, in week 9
    python scripts/evaluate.py --causes outputs/evaluation/<time>/errors.csv
    """
    parser = argparse.ArgumentParser(description=evaluate_main.__doc__.split("\n\n")[0])
    parser.add_argument("executed", nargs="*",
                        help="doc_ids (default: every agreement with an answer key)")
    parser.add_argument("--run", help="pipeline run id (default: the newest run of each)")
    parser.add_argument("--holdout", action="store_true",
                        help="score only the held-out agreements (config evaluation.holdout)")
    parser.add_argument("--gold-dir", help="answer-key folder (default: paths.gold_dir)")
    parser.add_argument("--causes", metavar="ERRORS_CSV",
                        help="rank the causes in a filled-in error worksheet, then stop")
    args = parser.parse_args(argv)

    root = Path(root or Path.cwd())
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    if args.causes:
        with open(args.causes, encoding="utf-8-sig", newline="") as f:
            ranking = rank_causes(list(csv.DictReader(f)))
        for cause, n in ranking:
            print(f"{n:5d}  {cause}")
        return 0

    settings = config["evaluation"]
    gold_dir = Path(args.gold_dir) if args.gold_dir else root / config["paths"]["gold_dir"]
    try:
        for path in convert_gold(gold_dir):
            print(f"answer key {path.name} written from {path.with_suffix('.csv').name}")
    except GoldError as e:
        raise SystemExit(f"fix the answer key first:\n{e}")
    keys = {p.stem: p for p in sorted(gold_dir.glob("*.json"))}
    holdout = set(settings.get("holdout") or [])
    if named_holdout := sorted(holdout & set(args.executed)) if not args.holdout else []:
        raise SystemExit(f"{', '.join(named_holdout)} is held out: score it only with "
                         "--holdout, once, after the week-9 fixes")
    doc_ids = [d for d in (args.executed or keys) if (d in holdout) == args.holdout]
    if missing := [d for d in doc_ids if d not in keys]:
        raise SystemExit(f"no answer key for {', '.join(missing)} in {gold_dir} "
                         "(see data/gold/INSTRUCTIONS.md)")
    if not doc_ids:
        print(f"no answer keys to score in {gold_dir}: the advisor fills in one CSV per "
              "agreement (see data/gold/INSTRUCTIONS.md)")
        return 1

    outputs = root / config["paths"]["outputs_dir"]
    min_overlap = settings["match_text_overlap"]
    results, rows = [], []
    for doc_id in doc_ids:
        run_id = args.run or latest_run(outputs, doc_id)
        if run_id is None or not (outputs / run_id / doc_id / "classify.json").exists():
            raise SystemExit(f"no pipeline run of {doc_id}: run python scripts/run.py first")
        store = RunStore(outputs, f"{run_id}/{doc_id}")
        labeled = store.load("classify", list[LabeledDeviation])
        context = RunContext(store.load("load_executed", Document),
                             store.load("segment_template", SegmentedDocument),
                             store.load("segment_executed", SegmentedDocument),
                             store.load("align", list[Alignment]), labeled)
        result = evaluate(load_gold(keys[doc_id]), labeled, min_overlap, run_id)
        results.append(result)
        rows += error_rows(result, context, min_overlap)

    total = Scores()
    for result in results:
        total.add(result.scores)
    when = datetime.now()
    out = outputs / "evaluation" / (when.strftime("%Y%m%d-%H%M%S")
                                    + ("-holdout" if args.holdout else ""))
    out.mkdir(parents=True, exist_ok=True)
    note = ("Held-out agreements: these are the honest numbers. Don't tune on them."
            if args.holdout else
            f"Held out, not scored here: {', '.join(sorted(holdout))}." if holdout else
            "No agreement is held out yet (config evaluation.holdout); PHASE0 asks for one.")
    ranking = rank_causes(rows)
    (out / "results.md").write_text(results_markdown(
        results, total, settings, ranking, note, when.strftime("%B %d, %Y %H:%M"),
        str(gold_dir.relative_to(root) if gold_dir.is_relative_to(root) else gold_dir)),
        encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results_json(results, total, settings),
                                                 indent=1), encoding="utf-8")
    with open(out / "errors.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ERROR_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    for r in results + [None]:
        s, name = (total, "TOTAL") if r is None else (r.scores, r.doc_id)
        print(f"{name:28} recall {percent(s.recall):>4}  precision {percent(s.precision):>4}  "
              f"substantive {percent(s.substantive_recall):>4} / "
              f"{percent(s.substantive_precision):>4}  category {percent(s.category_accuracy):>4}")
    for line in threshold_verdict(total, settings["target_recall"],
                                  settings["target_precision"]):
        print(line)
    if not settings.get("threshold_agreed"):
        print("note: the threshold is not recorded as agreed (evaluation.threshold_agreed)")
    print("errors by suggested cause: "
          + (", ".join(f"{cause} {n}" for cause, n in ranking) or "none"))
    print(f"-> {out.relative_to(root) if out.is_relative_to(root) else out}")
    return 0
