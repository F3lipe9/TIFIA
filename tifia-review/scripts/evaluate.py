"""Score the pipeline's latest runs against the advisor's answer keys (PHASE0 week 8) and
write outputs/evaluation/<time>/results.md, results.json and errors.csv (the error
worksheet: confirm a cause for every row). See tifia_review.cli.evaluate_main.

    python scripts/evaluate.py [doc_id ...] [--run <run_id>] [--holdout]
    python scripts/evaluate.py --causes outputs/evaluation/<time>/errors.csv
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tifia_review.cli import evaluate_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(evaluate_main(root=ROOT))
