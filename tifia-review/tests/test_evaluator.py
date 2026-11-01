import csv
import json

import pytest
import yaml

from tests.helpers import EXPECTED, FIXTURES, ROOT, TEMPLATE
from tests.test_pipeline import QuotingClient
from tifia_review.classifier import NOT_CLASSIFIED
from tifia_review.cli import evaluate_main
from tifia_review.evaluator import (
    ALIGNMENT, CLASSIFICATION, DIFF, EXTRACTION, SEGMENTATION, GoldDeviation, GoldError,
    GoldSet, RunContext, _cover, convert_gold, error_rows, evaluate, load_gold,
    normalize_section, overlap, rank_causes, read_gold_csv,
)
from tifia_review.models import (
    AlignMethod, Alignment, ChangeType, Deviation, Document, LabeledDeviation, Page, Section,
    SegmentedDocument,
)
from tifia_review.pipeline import Pipeline, Source
from tifia_review.report.builder import find_browser

MODIFIED, ADDED, REMOVED = ChangeType.MODIFIED, ChangeType.ADDED, ChangeType.REMOVED
HEADER = ["template_section", "agreement_section", "agreement_page", "change", "excerpt",
          "category", "substantive", "notes"]


def lab(n, change=MODIFIED, t="15(g)", e="15(g)", old="give notice within thirty (30) days",
        new="give notice within ten (10) days", category="Covenants", substantive=True,
        needs_review=False, page=10) -> LabeledDeviation:
    d = Deviation(f"d{n:04d}", change, t, e, old if t else "", new if e else "",
                  page if t else None, page if e else None)
    return LabeledDeviation(d, category, substantive, "x", needs_review)


def gold(n, t, a, change, excerpt, category="Covenants", substantive=True, page=""):
    return GoldDeviation(f"g{n:03d}", t, a, page, change, excerpt, category, substantive)


def write_csv(path, rows, encoding="utf-8-sig"):
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)


# --- answer keys -----------------------------------------------------------------------

def test_answer_key_rows_are_checked(tmp_path):
    path = tmp_path / "tla_x.csv"
    write_csv(path, [
        ["12(a)(x)", "12(a)(x)", "44", "Modified", "ten (10) days", "Covenants", "Yes", ""],
        ["", "", "", "", "", "", "", ""],                     # blank: skipped
        ["", "4(f)", "", "changed", "a fee", "Fees", "yes", ""],
        ["4(e)", "", "", "removed", "", "Other", "no", ""],
        ["", "", "", "added", "new text", "Other", "maybe", ""],
    ])
    with pytest.raises(GoldError) as e:
        read_gold_csv(path)
    message = str(e.value)
    assert "line 2" not in message and "line 3" not in message
    assert "line 4: change must be" in message and "line 5: the excerpt is empty" in message
    assert "substantive must be yes or no" in message and "needs agreement_section" in message


def test_answer_keys_convert_to_json(tmp_path):
    write_csv(tmp_path / "gold_template.csv", [])
    write_csv(tmp_path / "tla_x.csv", [["", "16(g)", "71", "added",
                                        "shall not engage in any business", "Covenants",
                                        "yes", "new covenant"]])
    tmp_path.joinpath("tla_y.csv").write_bytes(
        b"template_section,agreement_section,agreement_page,change,excerpt,category,"
        b"substantive,notes\r\n1,1,8,modified,the Borrower\x92s knowledge,Definitions,no,\r\n")
    written = convert_gold(tmp_path)
    assert [p.name for p in written] == ["tla_x.json", "tla_y.json"]  # not the template
    key = load_gold(tmp_path / "tla_x.json")
    assert key == GoldSet("tla_x", "tla_x.csv", [GoldDeviation(
        "g002", None, "16(g)", "71", "added", "shall not engage in any business", "Covenants",
        True, "new covenant")])
    assert load_gold(tmp_path / "tla_y.json").deviations[0].excerpt == "the Borrower’s knowledge"


@pytest.mark.parametrize("written, normalized", [
    ("Section 12 (a)(xii) Base Case Financial Model", "12(a)(xii)"),
    ("§ 15(G)", "15(g)"), ("12(a)", "12(a)"), ("Exh. H-1", "exhibit h-1"),
    ("Exhibit D", "exhibit d"), ("Sch. 13(f)", "schedule 13(f)"), ("Preamble", "preamble"),
    ("", ""), (None, ""),
])
def test_section_spellings_are_unified(written, normalized):
    assert normalize_section(written) == normalized


def test_overlap_counts_excerpt_words_in_order():
    assert overlap("ten (10) days", "notice within ten (10) days prior") == 1.0
    assert overlap("days ten", "ten days") == 0.5
    assert overlap("‘A-’, ‘A3’ ... ‘BBB+’, ‘Baa1’",
                   "rated 'A-' or 'A3', or at least 'BBB+' and 'Baa1' by") == 1.0
    # repeated words: the shortcut bound must not hide a real match
    assert _cover(["the"] * 4 + ["x", "y"], ["the"] * 4, need=0.5) == pytest.approx(4 / 6)
    assert _cover(["x", "y", "z"], ["a", "b"], need=0.5) == 0


# --- scores ----------------------------------------------------------------------------

def test_scores_follow_the_matching_rule():
    key = GoldSet("e", "e.csv", [
        gold(2, "15(g)", "15(g)", "modified", "notice within ten (10) days"),
        gold(3, "12(a)(xii)", "12(a)(x)", "modified", "Base Case Financial Model"),
        gold(4, "16(b)", "16(b)", "modified", "ten (10) days", category="Other",
             substantive=False),
    ])
    labeled = [lab(1), lab(2, t="12(a)(xi)", e="12(a)(xi)", new="Base Case Financial Model"),
               lab(3, t="16(b)", e="16(b)"), lab(4, t="17", e="17", new="indemnify"),
               lab(5, ChangeType.FILL_IN)]
    result = evaluate(key, labeled)
    s = result.scores
    assert (s.gold, s.gold_substantive, s.predicted, s.predicted_substantive) == (3, 2, 4, 4)
    assert (s.recall, s.precision) == (2 / 3, 2 / 4)  # 12(a)(xi) is not 12(a)(x)
    assert (s.substantive_recall, s.substantive_precision) == (1 / 2, 1 / 4)
    assert (s.category_accuracy, s.flag_accuracy) == (1 / 2, 1 / 2)
    assert [g.gold_id for g in result.misses] == ["g003"]
    assert [ld.deviation.deviation_id for ld in result.false_flags] == ["d0002", "d0004"]
    assert [(g.gold_id, ld.deviation.deviation_id) for g, ld in result.wrong_labels] == [
        ("g004", "d0003")]


def test_template_side_matches_and_one_deviation_can_cover_two_rows():
    text = ("in no event shall the TIFIA Lender have any obligation to make any disbursement "
            "if a shutdown of any federal department or agency due to a lapse in "
            "appropriations by Congress impairs it")
    key = GoldSet("e", "e.csv", [
        gold(2, "4(e)", None, "removed", "obligation to make any disbursement"),
        gold(3, "4(e)", None, "removed", "a lapse in appropriations by Congress"),
    ])
    result = evaluate(key, [lab(1, REMOVED, t="4(e)", e=None, old=text)])
    assert result.scores.recall == 1 and result.scores.precision == 1


def test_needs_review_counts_as_flagged_and_unclassified_runs_have_no_label_scores():
    key = GoldSet("e", "e.csv", [gold(2, "15(g)", "15(g)", "modified", "ten (10) days")])
    flagged = evaluate(key, [lab(1, substantive=False, needs_review=True)])
    assert flagged.scores.substantive_recall == 1
    plain = evaluate(key, [lab(1, category=NOT_CLASSIFIED)])
    assert not plain.classified and plain.scores.category_accuracy is None


# --- error analysis --------------------------------------------------------------------

def _context(labeled):
    executed_doc = Document("e", "executed", [Page(n, str(n - 2), "", needs_review=n == 95)
                                              for n in range(1, 100)])
    t_sections = [Section(sid, "", text, 1, 1) for sid, text in (
        ("12(a)(xii)", "The Borrower shall have delivered a Base Case Financial Model."),
        ("15(g)", "give notice within thirty (30) days"), ("19(b)", "development default"),
        ("19(c)", "bankruptcy"))]
    e_sections = [Section(sid, "", text, 1, 1) for sid, text in (
        ("12(a)(x)", "The Borrower shall have delivered a Base Case Financial Model."),
        ("15(g)", "give notice within ten (10) days"), ("19(b)", "bankruptcy"),
        ("16(g)", "No Prohibited Business"))]
    alignments = [Alignment(t_sections[1], e_sections[1], AlignMethod.NUMBER, 1.0),
                  Alignment(t_sections[2], e_sections[2], AlignMethod.NUMBER, 1.0)]  # 19(b)
    return RunContext(executed_doc, SegmentedDocument("t", t_sections, {}),
                      SegmentedDocument("e", e_sections, {}), alignments, labeled)


def test_error_rows_suggest_the_stage_at_fault():
    labeled = [
        lab(1, t="15(g)", e="15(g)", old="the Borrower shall, give notice",
            new="the Borrower shall give notice"),  # punctuation only
        lab(2, ADDED, t=None, e="16(g)", new="No Prohibited Business. The Borrower shall "
            "not engage in any business other than as authorized"),
        lab(3, REMOVED, t="16(h)", e=None, old="No Prohibited Business. The Borrower shall "
            "not engage in any business other than as authorized by law"),
        lab(4, t="19(c)", e="19(c)", new="all obligations shall automatically terminate"),
        lab(5, t="15(g)", e="15(g)", category="Other"),
    ]
    key = GoldSet("e", "e.csv", [
        gold(2, None, "Exhibit Z", "added", "signature of the Borrower", page="93"),
        gold(3, None, "13(zz)", "added", "a representation that does not exist"),
        gold(4, "12(a)(xii)", "12(a)(x)", "modified", "Base Case Financial Model"),
        gold(5, "19(b)", "19(b)", "modified", "all obligations shall automatically terminate"),
        gold(6, "15(g)", "15(g)", "modified", "notice within ten (10) days"),
    ])
    rows = error_rows(evaluate(key, labeled), _context(labeled))
    causes = {(r["kind"], r["gold_id"] or r["deviation_id"]): r["suggested_cause"] for r in rows}
    assert causes[("miss", "g002")] == EXTRACTION          # printed page 93 has no text
    assert causes[("miss", "g003")] == SEGMENTATION        # no such section
    assert causes[("miss", "g004")] == ALIGNMENT           # 12(a)(x) was not paired
    assert causes[("miss", "g005")] == ALIGNMENT           # found under 19(c)
    assert causes[("false flag", "d0001")] == DIFF         # punctuation only
    assert causes[("false flag", "d0002")] == ALIGNMENT    # reads like the removed d0003
    assert causes[("wrong category", "g006")] == CLASSIFICATION
    assert all(r["cause"] == "" for r in rows)


def test_confirmed_causes_rank_ahead_of_suggestions():
    rows = [{"suggested_cause": DIFF, "cause": ""}, {"suggested_cause": DIFF, "cause": ""},
            {"suggested_cause": DIFF, "cause": ALIGNMENT},
            {"suggested_cause": CLASSIFICATION, "cause": " alignment "}]
    assert rank_causes(rows) == [(DIFF, 2), (ALIGNMENT, 2)]


# --- the command -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A project folder with pipeline runs of two fixtures and their answer keys."""
    if not TEMPLATE.exists() or find_browser() is None:
        pytest.skip("template or PDF browser not present")
    root = tmp_path_factory.mktemp("project")
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["paths"].update(outputs_dir=str(root / "outputs"), gold_dir=str(root / "gold"),
                           cache_db=str(root / "cache.sqlite"))
    config["evaluation"]["holdout"] = ["fx_02_removed"]
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    pipeline = Pipeline(config, ROOT, client=QuotingClient(), run_id="20990101-000000")
    template = Source("tla_template_2023_public_project_revenue", "template", TEMPLATE)
    (root / "gold").mkdir()
    for doc_id in ("fx_02_removed", "fx_03_modified"):
        pipeline.run(Source(doc_id, "executed", FIXTURES / f"{doc_id}.pdf"), template)
        write_csv(root / "gold" / f"{doc_id}.csv", [
            [p["template_section_id"] or "", p["executed_section_id"] or "", "",
             p["change_type"], " ".join((p["executed_text"] or p["template_text"]).split()[:20]),
             "Covenants", "yes", ""] for p in EXPECTED[doc_id]["planted"]])
    return root


def test_evaluate_command_scores_runs_and_keeps_the_holdout_apart(project, capsys):
    assert evaluate_main([], root=project) == 0
    (out,) = (project / "outputs" / "evaluation").iterdir()
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert [a["doc_id"] for a in results["agreements"]] == ["fx_03_modified"]
    total = results["total"]
    assert (total["recall"], total["precision"], total["category_accuracy"]) == (1, 1, 1)
    assert "Held out, not scored here: fx_02_removed" in (out / "results.md").read_text(
        encoding="utf-8")
    assert (out / "errors.csv").read_text(encoding="utf-8-sig").startswith("doc_id,kind")

    with pytest.raises(SystemExit, match="held out"):
        evaluate_main(["fx_02_removed"], root=project)
    assert evaluate_main(["--holdout"], root=project) == 0
    holdout = [p for p in (project / "outputs" / "evaluation").iterdir()
               if p.name.endswith("-holdout")]
    assert len(holdout) == 1
    assert "TOTAL" in capsys.readouterr().out
