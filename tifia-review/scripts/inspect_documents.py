"""Run DocumentLoader on every document in the manifest (templates, fixtures, agreements)
and dump the extracted text for hand inspection to outputs/inspect/<doc_id>.txt, one block
per page headed by its PDF index, printed label and review flag, plus the Document as
<doc_id>.json. Findings are written up in docs/extraction-notes.md."""

import csv
import dataclasses
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.loader import DocumentLoader  # noqa: E402

OUT = ROOT / "outputs" / "inspect"


def main():
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    loader = DocumentLoader()
    with open(ROOT / "data" / "manifest.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        m = re.match(r"File: ([^;]+\.pdf)", row["notes"])
        if not m:
            continue
        doc = loader.load(str(ROOT / m.group(1)), row["doc_id"], row["type"])
        (OUT / f"{doc.doc_id}.json").write_text(
            json.dumps(dataclasses.asdict(doc), indent=1, ensure_ascii=False), encoding="utf-8")
        with open(OUT / f"{doc.doc_id}.txt", "w", encoding="utf-8") as t:
            for p in doc.pages:
                flag = "  [NEEDS REVIEW]" if p.needs_review else ""
                t.write(f"\n===== PDF page {p.pdf_index} | printed {p.printed_label}{flag} =====\n")
                t.write(p.text)
        review = [p.pdf_index for p in doc.pages if p.needs_review]
        print(f"{doc.doc_id}: {len(doc.pages)} pages, is_scanned={doc.is_scanned}, "
              f"needs_review={review}")


if __name__ == "__main__":
    main()
