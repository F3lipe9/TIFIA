"""Evaluator (week 8): compare the pipeline's deviations with the advisor's answer key.

The answer key is one CSV per agreement, data/gold/<doc_id>.csv (columns in
data/gold/gold_template.csv, instructions in data/gold/INSTRUCTIONS.md), converted to
data/gold/<doc_id>.json before scoring.

A deviation matches an answer-key row when both are in the same section and the
deviation's text covers at least half of the row's excerpt (config.yaml
evaluation.match_text_overlap). Words are compared in order after diff_engine.normalize,
against the agreement text or the template text, whichever covers more. Same section: the
agreement sections are equal, or the template sections are, once written the same way
("Section 12 (a)" is "12(a)"). Fill-ins and renumberings are not deviations on either
side.

Scores, per agreement and in total:
- recall: rows matched by some deviation; precision: deviations matching some row.
- substantive recall: substantive rows matched by a deviation the report shows as
  substantive or needs review; substantive precision: deviations shown as substantive or
  needs review that match a substantive row. The week-6 threshold applies to these two.
- category and substantive-flag accuracy: over matched rows, against the deviation that
  covers the row best.

Every miss, false flag and wrong label becomes a row of an error worksheet with a
suggested cause (the stage most likely at fault), for F to confirm or correct. The
confirmed causes, ranked, are the week-9 hand-off.
"""

import csv
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from tifia_review.classifier import NOT_CLASSIFIED
from tifia_review.diff_engine import normalize
from tifia_review.models import (
    Alignment, ChangeType, Document, LabeledDeviation, SegmentedDocument,
)
from tifia_review.store import from_jsonable, to_jsonable

GOLD_COLUMNS = ["template_section", "agreement_section", "agreement_page", "change",
                "excerpt", "category", "substantive", "notes"]
SCORED = (ChangeType.ADDED, ChangeType.REMOVED, ChangeType.MODIFIED)
EXTRACTION, SEGMENTATION, ALIGNMENT = "extraction", "segmentation", "alignment"
DIFF, CLASSIFICATION, ANSWER_KEY = "diff/normalization", "classification", "answer key"
CAUSES = [EXTRACTION, SEGMENTATION, ALIGNMENT, DIFF, CLASSIFICATION, ANSWER_KEY]
_PARTIAL = 0.2  # a miss covered at least this much in its section was found in part


class GoldError(ValueError):
    """An answer-key spreadsheet with rows that cannot be scored."""


# --- answer key ------------------------------------------------------------------------

@dataclass
class GoldDeviation:
    gold_id: str                  # "g012": line 12 of the spreadsheet
    template_section: str | None
    agreement_section: str | None
    agreement_page: str
    change: str                   # added | removed | modified
    excerpt: str
    category: str
    substantive: bool
    notes: str = ""


@dataclass
class GoldSet:
    doc_id: str
    source: str                   # the spreadsheet it was converted from
    deviations: list[GoldDeviation]


def read_gold_csv(path: Path, doc_id: str | None = None) -> GoldSet:
    """An answer-key spreadsheet saved as CSV. Raises GoldError listing every bad row."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:  # Excel's plain "CSV" format
        text = raw.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    if missing := [c for c in GOLD_COLUMNS if c not in (reader.fieldnames or [])]:
        raise GoldError(f"{path.name}: missing columns {', '.join(missing)}")
    rows, problems = [], []
    for line, raw_row in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw_row.items() if k}
        if not any(row.values()):
            continue
        change, flag = row["change"].lower(), row["substantive"].lower()
        errors = []
        if change not in ("added", "removed", "modified"):
            errors.append(f"change must be added, removed or modified, not {row['change']!r}")
        if flag not in ("yes", "no", "y", "n"):
            errors.append(f"substantive must be yes or no, not {row['substantive']!r}")
        if not row["excerpt"]:
            errors.append("the excerpt is empty")
        if change in ("added", "modified") and not row["agreement_section"]:
            errors.append(f"an {change} deviation needs agreement_section")
        if change in ("removed", "modified") and not row["template_section"]:
            errors.append(f"a {change} deviation needs template_section")
        if errors:
            problems.append(f"line {line}: " + "; ".join(errors))
            continue
        rows.append(GoldDeviation(
            f"g{line:03d}", row["template_section"] or None, row["agreement_section"] or None,
            row["agreement_page"], change, row["excerpt"], row["category"],
            flag in ("yes", "y"), row["notes"]))
    if problems:
        raise GoldError(f"{path.name}:\n  " + "\n  ".join(problems))
    return GoldSet(doc_id or path.stem, path.name, rows)


def convert_gold(gold_dir: Path) -> list[Path]:
    """Write <doc_id>.json for every answer-key CSV in gold_dir (not the blank template)."""
    written = []
    for path in sorted(gold_dir.glob("*.csv")):
        if path.name == "gold_template.csv":
            continue
        gold = read_gold_csv(path)
        out = path.with_suffix(".json")
        out.write_text(_json(to_jsonable(gold)), encoding="utf-8")
        written.append(out)
    return written


def load_gold(path: Path) -> GoldSet:
    return from_jsonable(json.loads(path.read_text(encoding="utf-8")), GoldSet)


def _json(data) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False)


# --- matching --------------------------------------------------------------------------

_PREFIX = re.compile(r"^(?:section|sec\.?|§)\s*", re.I)
_NUMBERED = re.compile(r"^\d+(?:\([a-z0-9]+\))*", re.I)
_NAMED = re.compile(r"^(exhibit|exh\.?|schedule|sch\.?)\s*([a-z0-9]+(?:-[a-z0-9]+)?"
                    r"(?:\([a-z0-9]+\))*)", re.I)


def normalize_section(section: str | None) -> str:
    """One spelling per section: 'Section 12 (a)(xii) Base Case' -> '12(a)(xii)',
    'Exh. H-1' -> 'exhibit h-1', 'Sch. 13(f)' -> 'schedule 13(f)'."""
    if not section:
        return ""
    text = re.sub(r"\s+(?=\()", "", _PREFIX.sub("", section.strip()))
    if m := _NUMBERED.match(text):
        return m.group(0).lower()
    if m := _NAMED.match(text):
        kind = "exhibit" if m.group(1).lower().startswith("exh") else "schedule"
        return f"{kind} {m.group(2).lower()}"
    return text.lower()


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", normalize(text).casefold())


def overlap(excerpt: str, text: str) -> float:
    """Share of the excerpt's words that text contains, in the same order."""
    return _cover(_words(excerpt), _words(text))


def _cover(a: list[str], b: list[str], need: float = 0.0,
           b_counts: Counter | None = None) -> float:
    """overlap() on word lists. When the shared word counts already show the result is
    below need, that smaller upper bound is returned without the exact computation."""
    if not a:
        return 0.0
    b_counts = b_counts if b_counts is not None else Counter(b)
    bound = sum(min(n, b_counts[w]) for w, n in Counter(a).items()) / len(a)
    if bound < need or bound == 0:
        return bound
    blocks = SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(a)


class _Texts:
    """Each deviation's words and word counts, computed once."""

    def __init__(self):
        self._cache: dict[int, list[tuple[list[str], Counter]]] = {}

    def coverage(self, excerpt: list[str], ld: LabeledDeviation, need: float = 0.0) -> float:
        """Of the excerpt by the deviation's executed or template text, whichever is more
        (exact when at least need)."""
        key = id(ld)
        if key not in self._cache:
            self._cache[key] = [(words, Counter(words)) for words in (
                _words(ld.deviation.executed_text), _words(ld.deviation.template_text))]
        return max(_cover(excerpt, words, need, counts) for words, counts in self._cache[key])


def _flagged(ld: LabeledDeviation) -> bool:
    """Shown to the reviewer as substantive, or as needing review."""
    return ld.substantive or ld.needs_review


# --- scores ----------------------------------------------------------------------------

@dataclass
class Scores:
    gold: int = 0
    gold_substantive: int = 0
    predicted: int = 0
    predicted_substantive: int = 0
    gold_found: int = 0
    predicted_right: int = 0
    gold_substantive_found: int = 0
    predicted_substantive_right: int = 0
    labels_compared: int = 0      # matched rows, when the run was classified
    category_right: int = 0
    flag_right: int = 0

    def add(self, other: "Scores") -> None:
        for name in vars(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @staticmethod
    def _rate(n: int, d: int) -> float | None:
        return n / d if d else None

    @property
    def recall(self):
        return self._rate(self.gold_found, self.gold)

    @property
    def precision(self):
        return self._rate(self.predicted_right, self.predicted)

    @property
    def substantive_recall(self):
        return self._rate(self.gold_substantive_found, self.gold_substantive)

    @property
    def substantive_precision(self):
        return self._rate(self.predicted_substantive_right, self.predicted_substantive)

    @property
    def category_accuracy(self):
        return self._rate(self.category_right, self.labels_compared)

    @property
    def flag_accuracy(self):
        return self._rate(self.flag_right, self.labels_compared)


@dataclass
class Evaluation:
    doc_id: str
    run_id: str
    scores: Scores
    classified: bool
    matches: list[tuple[str, str, float]]           # gold id, deviation id, coverage
    misses: list[GoldDeviation] = field(default_factory=list)
    false_flags: list[LabeledDeviation] = field(default_factory=list)
    wrong_labels: list[tuple[GoldDeviation, LabeledDeviation]] = field(default_factory=list)


def evaluate(gold: GoldSet, labeled: list[LabeledDeviation], min_overlap: float = 0.5,
             run_id: str = "") -> Evaluation:
    predictions = [ld for ld in labeled if ld.deviation.change_type in SCORED]
    by_section: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, ld in enumerate(predictions):
        d = ld.deviation
        if d.executed_section_id:
            by_section[("a", normalize_section(d.executed_section_id))].append(i)
        if d.template_section_id:
            by_section[("t", normalize_section(d.template_section_id))].append(i)

    pairs: dict[tuple[int, int], float] = {}
    texts = _Texts()
    for gi, g in enumerate(gold.deviations):
        candidates = set(by_section.get(("a", normalize_section(g.agreement_section)), [])
                         if g.agreement_section else [])
        if g.template_section:
            candidates |= set(by_section.get(("t", normalize_section(g.template_section)), []))
        excerpt = _words(g.excerpt)
        for pi in candidates:
            coverage = texts.coverage(excerpt, predictions[pi], min_overlap)
            if coverage >= min_overlap:
                pairs[(gi, pi)] = coverage

    classified = any(ld.category != NOT_CLASSIFIED for ld in predictions)
    s = Scores(gold=len(gold.deviations),
               gold_substantive=sum(g.substantive for g in gold.deviations),
               predicted=len(predictions),
               predicted_substantive=sum(_flagged(ld) for ld in predictions))
    best: dict[int, tuple[float, int]] = {}
    for (gi, pi), coverage in pairs.items():
        if gi not in best or coverage > best[gi][0]:
            best[gi] = (coverage, pi)
    matched_predictions = {pi for _, pi in pairs}
    s.gold_found = len(best)
    s.predicted_right = len(matched_predictions)
    s.gold_substantive_found = sum(
        1 for gi, g in enumerate(gold.deviations) if g.substantive
        and any(_flagged(predictions[pi]) for (g2, pi) in pairs if g2 == gi))
    s.predicted_substantive_right = sum(
        1 for pi, ld in enumerate(predictions) if _flagged(ld)
        and any(gold.deviations[gi].substantive for (gi, p2) in pairs if p2 == pi))

    result = Evaluation(gold.doc_id, run_id, s, classified,
                        [(gold.deviations[gi].gold_id, predictions[pi].deviation.deviation_id,
                          round(c, 2)) for (gi, pi), c in sorted(pairs.items())])
    for gi, g in enumerate(gold.deviations):
        if gi not in best:
            result.misses.append(g)
        elif classified:
            ld = predictions[best[gi][1]]
            s.labels_compared += 1
            right_category = ld.category.casefold() == g.category.casefold()
            right_flag = _flagged(ld) == g.substantive
            s.category_right += right_category
            s.flag_right += right_flag
            if not (right_category and right_flag):
                result.wrong_labels.append((g, ld))
    result.false_flags = [ld for pi, ld in enumerate(predictions)
                          if pi not in matched_predictions]
    return result


# --- error analysis --------------------------------------------------------------------

@dataclass
class RunContext:
    """The saved stage outputs of one agreement's run, for suggesting causes."""
    executed_doc: Document
    template: SegmentedDocument
    executed: SegmentedDocument
    alignments: list[Alignment]
    labeled: list[LabeledDeviation]


ERROR_COLUMNS = ["doc_id", "kind", "gold_id", "deviation_id", "template_section",
                 "agreement_section", "page", "text", "answer_key_label", "report_label",
                 "detail", "suggested_cause", "cause", "notes"]


def error_rows(result: Evaluation, ctx: RunContext, min_overlap: float = 0.5) -> list[dict]:
    """One worksheet row per miss, false flag and wrong label, with a suggested cause."""
    look = _Lookup(ctx, min_overlap)
    rows = []
    for g in result.misses:
        cause, detail = look.miss_cause(g)
        rows.append(_row(result.doc_id, "miss", g.gold_id, "", g.template_section or "",
                         g.agreement_section or "", g.agreement_page, g.excerpt,
                         _gold_label(g), "", detail, cause))
    for ld in result.false_flags:
        d = ld.deviation
        cause, detail = look.false_flag_cause(ld)
        rows.append(_row(result.doc_id, "false flag", "", d.deviation_id,
                         d.template_section_id or "", d.executed_section_id or "",
                         str(d.executed_page or d.template_page or ""),
                         d.executed_text or d.template_text, "", _report_label(ld), detail,
                         cause))
    for g, ld in result.wrong_labels:
        kinds = [kind for kind, wrong in (
            ("wrong category", ld.category.casefold() != g.category.casefold()),
            ("wrong substantive flag", _flagged(ld) != g.substantive)) if wrong]
        rows.append(_row(result.doc_id, " and ".join(kinds), g.gold_id,
                         ld.deviation.deviation_id, g.template_section or "",
                         g.agreement_section or "", g.agreement_page, g.excerpt,
                         _gold_label(g), _report_label(ld), ld.explanation, CLASSIFICATION))
    return rows


def _gold_label(g: GoldDeviation) -> str:
    return f"{g.category or '?'}, {'substantive' if g.substantive else 'not substantive'}"


def _report_label(ld: LabeledDeviation) -> str:
    flag = ("needs review" if ld.needs_review else
            "substantive" if ld.substantive else "not substantive")
    return f"{ld.category}, {flag}"


class _Lookup:
    """What the suggestions need from a run: sections, pairings, pages without text."""

    def __init__(self, ctx: RunContext, min_overlap: float):
        self.min_overlap = min_overlap
        self.predictions = [ld for ld in ctx.labeled if ld.deviation.change_type in SCORED]
        self.texts = _Texts()
        self.executed = {normalize_section(s.section_id): s.text for s in ctx.executed.sections}
        self.template = {normalize_section(s.section_id): s.text for s in ctx.template.sections}
        self.paired = {normalize_section(a.executed_section.section_id):
                       a.template_section.section_id
                       for a in ctx.alignments if a.executed_section and a.template_section}
        self.flagged = {p.pdf_index for p in ctx.executed_doc.pages if p.needs_review}
        labels = Counter(p.printed_label for p in ctx.executed_doc.pages if p.printed_label)
        self.page_of = {p.printed_label: p.pdf_index for p in ctx.executed_doc.pages
                        if p.printed_label and labels[p.printed_label] == 1}

    def miss_cause(self, g: GoldDeviation) -> tuple[str, str]:
        a_sec = normalize_section(g.agreement_section)
        t_sec = normalize_section(g.template_section)
        if self.page_of.get(g.agreement_page) in self.flagged:
            return EXTRACTION, f"agreement page {g.agreement_page} has almost no text layer"
        if a_sec and a_sec not in self.executed:
            return SEGMENTATION, f"no section {g.agreement_section} was found in the agreement"
        if t_sec and t_sec not in self.template:
            return SEGMENTATION, f"no section {g.template_section} was found in the template"
        if a_sec and t_sec and normalize_section(self.paired.get(a_sec)) != t_sec:
            other = self.paired.get(a_sec)
            return ALIGNMENT, (f"the agreement's {g.agreement_section} was paired with "
                               f"template {other}" if other else
                               f"the agreement's {g.agreement_section} was not paired")
        excerpt = _words(g.excerpt)
        scored = sorted(((self.texts.coverage(excerpt, ld, _PARTIAL), n, ld)
                         for n, ld in enumerate(self.predictions)), key=lambda c: -c[0])
        if scored and scored[0][0] >= self.min_overlap:
            ld = scored[0][2]
            where = ld.deviation.executed_section_id or ld.deviation.template_section_id
            return ALIGNMENT, (f"found as {ld.deviation.deviation_id} under section {where}, "
                               "not this section")
        same = [(c, ld) for c, _, ld in scored
                if (a_sec and normalize_section(ld.deviation.executed_section_id) == a_sec)
                or (t_sec and normalize_section(ld.deviation.template_section_id) == t_sec)]
        if same and same[0][0] >= _PARTIAL:
            return DIFF, (f"only {same[0][0]:.0%} of the excerpt is in "
                          f"{same[0][1].deviation.deviation_id}")
        if (a_sec and t_sec and _cover(excerpt, _words(self.executed[a_sec]), 0.8) >= 0.8
                and _cover(excerpt, _words(self.template[t_sec]), 0.8) >= 0.8):
            return DIFF, ("the excerpt reads the same in both documents after normalization "
                          "(or it quotes unchanged words)")
        return DIFF, "no deviation was reported for this text"

    def false_flag_cause(self, ld: LabeledDeviation) -> tuple[str, str]:
        d = ld.deviation
        if {d.executed_page, d.template_page} & self.flagged:
            return EXTRACTION, "on a page with almost no text layer"
        if (d.change_type is ChangeType.MODIFIED
                and _words(d.template_text) == _words(d.executed_text)):
            return DIFF, "only punctuation, capitals or spacing differ"
        if d.template_section_id is None or d.executed_section_id is None:
            added = d.template_section_id is None
            text = _words(d.executed_text or d.template_text)[:200]
            opposite = [p for p in self.predictions
                        if (p.deviation.template_section_id is None) != added
                        and (p.deviation.executed_section_id is None) == added]
            best = max(((self.texts.coverage(text, p, self.min_overlap), p.deviation.deviation_id)
                        for p in opposite), default=(0.0, ""))
            if best[0] >= self.min_overlap:
                return ALIGNMENT, f"a whole section that reads like {best[1]}: a missed pairing"
            return ALIGNMENT, ("a whole section the answer key does not list: a missed "
                               "pairing, or an answer-key omission")
        return DIFF, ("not in the answer key: a formatting change, a sentence split "
                      "differently, or a deviation the answer key leaves out")


def _row(doc_id, kind, gold_id, deviation_id, t_sec, a_sec, page, text, gold_label,
         report_label, detail, cause) -> dict:
    return dict(zip(ERROR_COLUMNS, [doc_id, kind, gold_id, deviation_id, t_sec, a_sec, page,
                                    _clip(text), gold_label, report_label, detail, cause,
                                    "", ""]))


def _clip(text: str, n: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + " …"


def rank_causes(rows: list[dict]) -> list[tuple[str, int]]:
    """Errors per cause, most first: the confirmed cause where filled in, otherwise the
    suggestion."""
    counts = Counter((row.get("cause") or row["suggested_cause"]).strip() for row in rows)
    return counts.most_common()


# --- results ---------------------------------------------------------------------------

def percent(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.0%}"


def threshold_verdict(total: Scores, target_recall: float, target_precision: float) -> list[str]:
    """Substantive recall and precision against the week-6 threshold, one line each."""
    lines = []
    for name, value, target in (("substantive recall", total.substantive_recall, target_recall),
                                ("substantive precision", total.substantive_precision,
                                 target_precision)):
        verdict = ("cannot be measured: nothing to count" if value is None else
                   f"meets the {target:.0%} target" if value >= target else
                   f"misses the {target:.0%} target")
        lines.append(f"{name} {percent(value)}: {verdict}")
    return lines


def results_markdown(results: list[Evaluation], total: Scores, evaluation_config: dict,
                     ranking: list[tuple[str, int]], holdout_note: str, when: str,
                     gold_dir: str = "data/gold") -> str:
    target_recall = evaluation_config["target_recall"]
    target_precision = evaluation_config["target_precision"]
    agreed = evaluation_config.get("threshold_agreed") or ""
    lines = [
        "# Evaluation results", "",
        f"Scored {when}, against the answer keys in {gold_dir}. Match: same section and at "
        f"least {evaluation_config['match_text_overlap']:.0%} of the excerpt's words "
        "(PHASE0 week 8). Fill-ins and renumberings are not counted on either side.", "",
        f"Threshold (set in week 6, config.yaml `evaluation`): substantive recall at least "
        f"{target_recall:.0%} and substantive precision at least {target_precision:.0%}. "
        + (f"Agreed: {agreed}." if agreed else
           "**Not yet recorded as agreed in writing** (`evaluation.threshold_agreed`)."), "",
    ]
    if holdout_note:
        lines += [holdout_note, ""]
    if unclassified := [r.doc_id for r in results if not r.classified]:
        lines += [f"Not classified ({', '.join(unclassified)}): the run was made without the "
                  "LLM, so every deviation counts as substantive and category and flag "
                  "accuracy are not available.", ""]
    lines += [
        "| Agreement | Run | Answer key (substantive) | Report (substantive) | Recall | "
        "Precision | Substantive recall | Substantive precision | Category accuracy | "
        "Flag accuracy |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results + [None]:
        s, name, run = (total, "**Total**", "") if r is None else (r.scores, r.doc_id, r.run_id)
        lines.append(
            f"| {name} | {run} | {s.gold} ({s.gold_substantive}) | {s.predicted} "
            f"({s.predicted_substantive}) | {percent(s.recall)} | {percent(s.precision)} | "
            f"{percent(s.substantive_recall)} | {percent(s.substantive_precision)} | "
            f"{percent(s.category_accuracy)} | {percent(s.flag_accuracy)} |")
    lines += ["", "Against the threshold:", ""]
    lines += [f"- {line}" for line in threshold_verdict(total, target_recall, target_precision)]
    lines += ["", "## Errors by cause", "",
              "From errors.csv: the `cause` column where it is filled in, otherwise the "
              "suggested cause. Confirm each row, then rank again with "
              "`python scripts/evaluate.py --causes <errors.csv>`.", "",
              "| Cause | Errors |", "|---|---|"]
    lines += [f"| {cause} | {n} |" for cause, n in ranking] or ["| (none) | 0 |"]
    lines += ["", "## Time comparison (ask the advisor)", "",
              "| Agreement | Manual review | Reviewing the report | Notes |", "|---|---|---|---|"]
    lines += [f"| {r.doc_id} | | | |" for r in results]
    return "\n".join(lines) + "\n"


def results_json(results: list[Evaluation], total: Scores, evaluation_config: dict) -> dict:
    def scores(s: Scores) -> dict:
        rates = {name: getattr(s, name) for name in (
            "recall", "precision", "substantive_recall", "substantive_precision",
            "category_accuracy", "flag_accuracy")}
        return {**vars(s), **rates}
    return {
        "evaluation": {k: evaluation_config.get(k) for k in (
            "match_text_overlap", "target_recall", "target_precision", "threshold_agreed",
            "holdout")},
        "agreements": [{"doc_id": r.doc_id, "run_id": r.run_id, "classified": r.classified,
                        "scores": scores(r.scores),
                        "matches": [list(m) for m in r.matches]} for r in results],
        "total": scores(total),
    }
