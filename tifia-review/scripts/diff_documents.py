"""Diff executed agreements against a template and dump the deviations for the week-5
sanity check: outputs/diff/<executed>__vs__<template>.txt (to skim: is it mostly real
changes or formatting noise?) and .json (the Deviation list).

    python scripts/diff_documents.py                          # every executed document
    python scripts/diff_documents.py tla_2021_federal_way --template <template doc_id>

The template defaults to the project-revenue public borrower template.
"""

import argparse
import csv
import json
import logging
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.aligner import Aligner  # noqa: E402
from tifia_review.diff_engine import DiffEngine  # noqa: E402
from tifia_review.loader import DocumentLoader  # noqa: E402
from tifia_review.models import Deviation  # noqa: E402
from tifia_review.segmenter import Segmenter  # noqa: E402
from tifia_review.store import to_jsonable  # noqa: E402

OUT = ROOT / "outputs" / "diff"
DEFAULT_TEMPLATE = "tla_template_2023_public_project_revenue"


def manifest_files() -> dict[str, tuple[str, Path]]:
    """doc_id -> (type, PDF path) for manifest rows that name a PDF."""
    with open(ROOT / "data" / "manifest.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    files = {}
    for row in rows:
        m = re.match(r"File: ([^;]+\.pdf)", row["notes"])
        if m:
            files[row["doc_id"]] = (row["type"], ROOT / m.group(1))
    return files


def describe(d: Deviation) -> str:
    where = (f"template {d.template_section_id or '-'} p.{d.template_page or '-'} / "
             f"executed {d.executed_section_id or '-'} p.{d.executed_page or '-'}")
    term = f"  [{d.affects_defined_term}]" if d.affects_defined_term else ""
    lines = [f"{d.deviation_id}  {d.change_type.value.upper():<10} {where}{term}"]
    for sign, text in (("-", d.template_text), ("+", d.executed_text)):
        if text:
            lines += textwrap.wrap(text, 100, initial_indent=f"  {sign} ",
                                   subsequent_indent="    ")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executed", nargs="*", help="executed doc_ids (default: all)")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="template doc_id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    for name in ("tifia_review.loader", "tifia_review.segmenter"):
        logging.getLogger(name).setLevel(logging.ERROR)
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["aligner"]
    files = manifest_files()
    executed_ids = args.executed or [d for d, (kind, _) in files.items() if kind == "executed"]

    loader, segmenter, engine = DocumentLoader(), Segmenter(), DiffEngine()
    aligner = Aligner(threshold=config["similarity_threshold"],
                      model_name=config["embedding_model"])

    def segment(doc_id):
        kind, path = files[doc_id]
        return segmenter.segment(loader.load(str(path), doc_id, kind))

    template = segment(args.template)
    OUT.mkdir(parents=True, exist_ok=True)
    for doc_id in executed_ids:
        executed = segment(doc_id)
        deviations = engine.diff(template, executed, aligner.align(template, executed))
        counts = Counter(d.change_type.value for d in deviations)
        stem = OUT / f"{doc_id}__vs__{args.template}"
        stem.with_suffix(".json").write_text(
            json.dumps(to_jsonable(deviations), indent=1, ensure_ascii=False), encoding="utf-8")
        header = f"{doc_id} vs {args.template}: {len(deviations)} deviations {dict(counts)}"
        stem.with_suffix(".txt").write_text(
            header + "\n\n" + "\n\n".join(describe(d) for d in deviations) + "\n",
            encoding="utf-8")
        print(f"{header} -> {stem.with_suffix('.txt').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
