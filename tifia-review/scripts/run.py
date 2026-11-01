"""CLI: run the whole pipeline on the executed agreements in data/manifest.csv and write
a PDF report for each to outputs/reports/ (see tifia_review.cli for the options).

    python scripts/run.py [doc_id ...] [--no-llm]

Evaluation against the advisor's answer key comes in week 8.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(root=ROOT))
