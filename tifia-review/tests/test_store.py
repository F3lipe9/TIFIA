import pytest

from tests.helpers import FIXTURES, TEMPLATE, no_embeddings, segment_pdf
from tifia_review.aligner import Aligner
from tifia_review.diff_engine import DiffEngine
from tifia_review.models import (
    AlignMethod, Alignment, ChangeType, DefinedTerm, Deviation, Document,
    LabeledDeviation, Page, Section, SegmentedDocument,
)
from tifia_review.store import ResultCache, RunStore

SECTION = Section("16(b)", "No Lien Extinguishment or Adverse Amendments",
                  "(b) The Borrower shall ...", 73, 74, "70", "71", level=2, parent_id="16")


def test_document_round_trip(tmp_path):
    doc = Document("tla_x", "executed", [
        Page(1, None, "cover “quoted” text"),
        Page(2, "i", "toc", needs_review=True),
    ], is_scanned=False)
    store = RunStore(tmp_path, "run1")
    store.save("load_executed", doc)
    assert store.exists("load_executed")
    assert store.load("load_executed", Document) == doc


def test_segmented_document_round_trip(tmp_path):
    seg = SegmentedDocument("tla_x", [SECTION],
                            {"Act": DefinedTerm("Act", "means the Act ...", "1")})
    store = RunStore(tmp_path, "run1")
    store.save("segment", seg)
    assert store.load("segment", SegmentedDocument) == seg


def test_list_of_alignments_with_none_and_enum(tmp_path):
    aligns = [
        Alignment(SECTION, SECTION, AlignMethod.NUMBER, 1.0),
        Alignment(None, SECTION, None, 0.0),  # added section
    ]
    store = RunStore(tmp_path, "run1")
    store.save("align", aligns)
    loaded = store.load("align", list[Alignment])
    assert loaded == aligns
    assert loaded[0].method is AlignMethod.NUMBER


def test_labeled_deviations_round_trip(tmp_path):
    dev = Deviation("d1", ChangeType.MODIFIED, "16(b)", "16(b)",
                    "thirty (30) days", "ten (10) days", 73, 73)
    labeled = [LabeledDeviation(dev, "Covenants", True, "Notice period shortened.")]
    store = RunStore(tmp_path, "run1")
    store.save("classify", labeled)
    assert store.load("classify", list[LabeledDeviation]) == labeled


def test_saved_file_is_readable_json(tmp_path):
    store = RunStore(tmp_path, "run1")
    path = store.save("align", [Alignment(None, None, AlignMethod.EMBEDDING, 0.8)])
    assert path == tmp_path / "run1" / "align.json"
    assert '"method": "embedding"' in path.read_text(encoding="utf-8")
    assert not list((tmp_path / "run1").glob("*.tmp"))


def test_default_run_id_creates_dir(tmp_path):
    store = RunStore(tmp_path)
    assert store.dir.is_dir() and store.dir.parent == tmp_path


def test_result_cache_persists_and_overwrites(tmp_path):
    path = tmp_path / "sub" / "cache.sqlite"
    cache = ResultCache(path)
    assert cache.get("k") is None
    cache.put("k", {"category": "Covenants", "evidence": "the “Agreement”"})
    cache.put("k", {"category": "Other"})
    cache.close()
    again = ResultCache(path)
    assert again.get("k") == {"category": "Other"} and len(again) == 1
    again.close()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")
def test_real_stage_outputs_round_trip(tmp_path):
    """Each stage's output for a fixture comes back unchanged: page breaks, defined terms,
    renumberings and fill-ins included."""
    document, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(FIXTURES / "fx_04_renumbered.pdf")
    alignments = Aligner(embed=no_embeddings).align(template, executed)
    deviations = DiffEngine().diff(template, executed, alignments)
    assert {d.change_type for d in deviations} == {ChangeType.RENUMBERED, ChangeType.FILL_IN}
    assert template.defined_terms and any(s.page_breaks for s in template.sections)
    store = RunStore(tmp_path, "run1")
    for stage, obj, tp in [("load_template", document, Document),
                           ("segment_template", template, SegmentedDocument),
                           ("segment_executed", executed, SegmentedDocument),
                           ("align", alignments, list[Alignment]),
                           ("diff", deviations, list[Deviation])]:
        store.save(stage, obj)
        assert store.load(stage, tp) == obj, stage
