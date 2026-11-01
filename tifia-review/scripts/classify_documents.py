"""Label the deviations of executed agreements with the LLM classifier (week 6) and write
them for review to outputs/classify/<executed>__vs__<template>.csv (UTF-8 with BOM, empty
category_ok / substantive_ok / explanation_ok / notes columns to fill in), .txt and .json
(the LabeledDeviation list). Token use and estimated cost are appended to
outputs/llm_usage.csv, with the running total against the budget in config.yaml.

Reads the week-5 output of scripts/diff_documents.py and the segmented documents of
scripts/segment_documents.py (for section captions), so run those first.

    python scripts/classify_documents.py fx_03_modified --dry-run    # cost estimate only
    python scripts/classify_documents.py fx_03_modified              # cheap first run
    python scripts/classify_documents.py tla_2021_drle [--limit 30] [--template <doc_id>]

Needs LLM_API_KEY in .env (see .env.example). Labels are cached in outputs/cache.sqlite,
so a rerun only pays for deviations whose text, prompt or model changed.
"""

import argparse
import csv
import json
import logging
import sys
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.classifier import Classifier  # noqa: E402
from tifia_review.llm.client import (  # noqa: E402
    LLMClient, Usage, append_usage_log, make_client, model_id,
)
from tifia_review.models import Deviation, LabeledDeviation, SegmentedDocument  # noqa: E402
from tifia_review.store import ResultCache, from_jsonable, to_jsonable  # noqa: E402

OUT = ROOT / "outputs" / "classify"
LEDGER = ROOT / "outputs" / "llm_usage.csv"
DEFAULT_TEMPLATE = "tla_template_2023_public_project_revenue"
EXCERPT_CHARS = 300
CHARS_PER_TOKEN = 3.5      # rough, for the dry-run estimate
OUTPUT_TOKENS_PER_LABEL = 120
REVIEW_COLUMNS = ["row", "deviation_id", "change", "template_section", "executed_section",
                  "template_page", "executed_page", "defined_term", "category", "substantive",
                  "needs_review", "review_reason", "explanation", "evidence", "template_text",
                  "executed_text", "category_ok", "substantive_ok", "explanation_ok", "notes"]


class _NoLLM(LLMClient):
    """Stands in for the real client in a dry run; must never be called."""

    def __init__(self, model_id: str):
        super().__init__()
        self.model_id = model_id

    def complete_json(self, system, user, schema=None):
        raise AssertionError("dry run")


def shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def load_json(path: Path, tp):
    if not path.exists():
        sys.exit(f"missing {shown(path)}: run the earlier stage scripts first")
    return from_jsonable(json.loads(path.read_text(encoding="utf-8")), tp)


def describe(n: int, ld: LabeledDeviation) -> str:
    d = ld.deviation
    flag = "SUBSTANTIVE" if ld.substantive else "non-substantive"
    review = f"  NEEDS REVIEW: {ld.review_reason}" if ld.needs_review else ""
    lines = [f"{n}. {d.deviation_id} {d.change_type.value} | template "
             f"{d.template_section_id or '-'} / executed {d.executed_section_id or '-'} | "
             f"{ld.category} | {flag}{review}"]
    lines += textwrap.wrap(ld.explanation, 100, initial_indent="   ", subsequent_indent="   ")
    if ld.evidence:
        lines += textwrap.wrap(f'evidence: "{ld.evidence}"', 100, initial_indent="   ",
                               subsequent_indent="   ")
    return "\n".join(lines)


def write_outputs(stem: Path, labeled: list[LabeledDeviation], header: str) -> None:
    stem.with_suffix(".json").write_text(
        json.dumps(to_jsonable(labeled), indent=1, ensure_ascii=False), encoding="utf-8")
    stem.with_suffix(".txt").write_text(
        header + "\n\n" + "\n\n".join(describe(n, ld) for n, ld in enumerate(labeled, 1))
        + "\n", encoding="utf-8")
    with open(stem.with_suffix(".csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REVIEW_COLUMNS)
        for n, ld in enumerate(labeled, 1):
            d = ld.deviation
            writer.writerow([
                n, d.deviation_id, d.change_type.value, d.template_section_id or "",
                d.executed_section_id or "", d.template_page or "", d.executed_page or "",
                d.affects_defined_term or "", ld.category, "y" if ld.substantive else "n",
                "y" if ld.needs_review else "", ld.review_reason, ld.explanation, ld.evidence,
                d.template_text[:EXCERPT_CHARS], d.executed_text[:EXCERPT_CHARS],
                "", "", "", ""])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executed", nargs="+", help="executed doc_ids")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="template doc_id")
    parser.add_argument("--limit", type=int, help="only the first N deviations (a trial run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count calls and estimate the cost; send nothing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    load_dotenv(ROOT / ".env")
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    llm = config["llm"]
    prices = llm["price_per_mtok"]
    cache = ResultCache(ROOT / config["paths"]["cache_db"])
    try:
        client = _NoLLM(model_id(llm)) if args.dry_run else make_client(llm)
    except RuntimeError as e:
        sys.exit(str(e))
    classifier = Classifier.from_config(client, config, cache)
    template = load_json(ROOT / "outputs" / "segmented" / f"{args.template}.json",
                         SegmentedDocument)
    OUT.mkdir(parents=True, exist_ok=True)

    for doc_id in args.executed:
        name = f"{doc_id}__vs__{args.template}"
        deviations = load_json(ROOT / "outputs" / "diff" / f"{name}.json", list[Deviation])
        deviations = deviations[:args.limit] if args.limit else deviations
        executed = load_json(ROOT / "outputs" / "segmented" / f"{doc_id}.json",
                             SegmentedDocument)
        if args.dry_run:
            calls = classifier.plan(deviations, template, executed)
            to_label = sum(n for _, n in calls)
            chars = sum(len(classifier.system) + len(message) for message, _ in calls)
            estimate = Usage(input_tokens=int(chars / CHARS_PER_TOKEN),
                             output_tokens=to_label * OUTPUT_TOKENS_PER_LABEL)
            print(f"{doc_id}: {len(deviations)} deviations, {to_label} to send in "
                  f"{len(calls)} call{'s' * (len(calls) != 1)}; about "
                  f"{estimate.input_tokens:,} input and "
                  f"{estimate.output_tokens:,} output tokens before thinking: "
                  f"${estimate.cost(prices):.2f} at {llm['model']} prices (thinking can "
                  f"add as much again or more)")
            continue

        before = Usage(**vars(client.usage))
        classifier.stats.clear()
        labeled = classifier.classify(deviations, template, executed)
        run = Usage(**{k: v - getattr(before, k) for k, v in vars(client.usage).items()})
        cost = run.cost(prices)
        stats = classifier.stats
        categories = Counter(ld.category for ld in labeled)
        substantive = sum(ld.substantive for ld in labeled)
        header = (f"{doc_id} vs {args.template}: {len(labeled)} deviations, {substantive} "
                  f"substantive, {stats['needs_review']} need review; "
                  f"{dict(categories.most_common())}")
        write_outputs(OUT / name, labeled, header)
        total = append_usage_log(LEDGER, {
            "time": datetime.now().isoformat(timespec="seconds"), "executed": doc_id,
            "template": args.template, "model": client.model_id,
            "deviations": len(labeled), "llm_labels": stats["llm"], "cached": stats["cached"],
            "needs_review": stats["needs_review"], "calls": run.calls,
            "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "cache_write_tokens": run.cache_creation_input_tokens,
            "cache_read_tokens": run.cache_read_input_tokens, "cost_usd": round(cost, 4)})
        print(f"{header}\n  {run.calls} call{'s' * (run.calls != 1)}, {stats['cached']} "
              f"cached, {stats['retried']} retried; ${cost:.2f} this run, ${total:.2f} of "
              f"${llm['budget_usd']} so far "
              f"-> {shown((OUT / name).with_suffix('.csv'))}")
    cache.close()


if __name__ == "__main__":
    main()
