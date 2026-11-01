"""Segment every template and executed agreement in the manifest and save the result to
outputs/segmented/<doc_id>.json, plus an outline <doc_id>.txt for hand review: one line
per section (id, PDF pages, printed pages, heading), then the defined terms. Mismatches
with the document's own table of contents are logged as warnings."""

import csv
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.loader import DocumentLoader  # noqa: E402
from tifia_review.segmenter import Segmenter  # noqa: E402
from tifia_review.store import to_jsonable  # noqa: E402

OUT = ROOT / "outputs" / "segmented"


def main():
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("tifia_review.loader").setLevel(logging.ERROR)
    OUT.mkdir(parents=True, exist_ok=True)
    loader, segmenter = DocumentLoader(), Segmenter()
    with open(ROOT / "data" / "manifest.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        m = re.match(r"File: ([^;]+\.pdf)", row["notes"])
        if not m or row["type"] not in ("template", "executed"):
            continue  # the omnibus amendment is not a full agreement
        doc = loader.load(str(ROOT / m.group(1)), row["doc_id"], row["type"])
        seg = segmenter.segment(doc)
        (OUT / f"{doc.doc_id}.json").write_text(
            json.dumps(to_jsonable(seg), indent=1, ensure_ascii=False), encoding="utf-8")
        with open(OUT / f"{doc.doc_id}.txt", "w", encoding="utf-8") as t:
            for s in seg.sections:
                pages = f"p{s.pdf_start}-{s.pdf_end}"
                printed = f"[{s.label_start or ''}-{s.label_end or ''}]"
                t.write(f"{'  ' * (s.level - 1)}{s.section_id:<20} {pages:<9} {printed:<14} "
                        f"{s.heading}\n")
            t.write(f"\n===== {len(seg.defined_terms)} defined terms =====\n")
            for term in seg.defined_terms.values():
                t.write(f"{term.term}: {' '.join(term.definition.split())[:100]}\n")
        top = sum(s.level == 1 for s in seg.sections)
        print(f"{doc.doc_id}: {len(seg.sections)} sections ({top} top level), "
              f"{len(seg.defined_terms)} defined terms")


if __name__ == "__main__":
    main()
