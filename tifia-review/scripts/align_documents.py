"""Align executed agreements with a template and export each result for the week-4 hand
check to outputs/alignment/<executed>__vs__<template>.csv (UTF-8 with BOM, so Excel shows
curly quotes): one row per alignment with the method, confidence, both sections' ids,
headings, pages and opening words, and empty "correct" and "notes" columns to fill in.

    python scripts/align_documents.py                         # every executed document
    python scripts/align_documents.py tla_2021_federal_way --template <template doc_id>

The template defaults to the project-revenue public borrower template.
"""

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.aligner import Aligner  # noqa: E402
from tifia_review.loader import DocumentLoader  # noqa: E402
from tifia_review.models import Section  # noqa: E402
from tifia_review.segmenter import Segmenter  # noqa: E402

OUT = ROOT / "outputs" / "alignment"
DEFAULT_TEMPLATE = "tla_template_2023_public_project_revenue"
EXCERPT_CHARS = 300
COLUMNS = ["row", "status", "method", "confidence",
           "template_id", "template_heading", "template_page", "template_pdf_page",
           "executed_id", "executed_heading", "executed_page", "executed_pdf_page",
           "template_text", "executed_text", "correct", "notes"]


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


def section_cells(section: Section | None) -> list:
    if section is None:
        return ["", "", "", ""]
    return [section.section_id, section.heading, section.label_start or "", section.pdf_start]


def excerpt(section: Section | None) -> str:
    return " ".join(section.text.split())[:EXCERPT_CHARS] if section else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executed", nargs="*", help="executed doc_ids (default: all)")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="template doc_id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("tifia_review.loader").setLevel(logging.ERROR)
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["aligner"]
    files = manifest_files()
    executed_ids = args.executed or [d for d, (kind, _) in files.items() if kind == "executed"]

    loader, segmenter = DocumentLoader(), Segmenter()
    aligner = Aligner(threshold=config["similarity_threshold"],
                      model_name=config["embedding_model"])

    def segment(doc_id):
        kind, path = files[doc_id]
        return segmenter.segment(loader.load(str(path), doc_id, kind))

    template = segment(args.template)
    OUT.mkdir(parents=True, exist_ok=True)
    for doc_id in executed_ids:
        alignments = aligner.align(template, segment(doc_id))
        path = OUT / f"{doc_id}__vs__{args.template}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for n, a in enumerate(alignments, start=1):
                status = ("paired" if a.template_section and a.executed_section
                          else "removed" if a.template_section else "added")
                writer.writerow([n, status, a.method.value if a.method else "", a.confidence,
                                 *section_cells(a.template_section),
                                 *section_cells(a.executed_section),
                                 excerpt(a.template_section), excerpt(a.executed_section),
                                 "", ""])
        counts = Counter(a.method.value if a.method else
                         ("removed" if a.executed_section is None else "added")
                         for a in alignments)
        print(f"{doc_id}: {len(alignments)} rows {dict(counts)} -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
