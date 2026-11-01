import json
from types import SimpleNamespace

import pytest
import yaml

from tests.helpers import FIXTURES, ROOT, TEMPLATE
from tifia_review.cli import read_manifest, source
from tifia_review.llm.client import LLMClient
from tifia_review.pipeline import Pipeline, Source
from tifia_review.report.builder import find_browser
from tifia_review.store import RunStore

STAGES = ["load_template", "load_executed", "segment_template", "segment_executed", "align",
          "diff", "classify"]


class QuotingClient(LLMClient):
    """Labels every deviation Covenants, quoting the start of the text it must quote."""
    model_id = "fake"

    def complete_json(self, system, user, schema=None):
        items = json.loads(user[user.index("\n["):])
        self.usage.add(SimpleNamespace(input_tokens=500, output_tokens=50))
        return {"labels": [{"id": i["id"], "category": "Covenants", "substantive": True,
                            "explanation": "Changed.",
                            "evidence": " ".join((i.get("agreement_text")
                                                  or i["template_text"]).split()[:5])}
                           for i in items]}


def config(tmp_path) -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["paths"]["outputs_dir"] = str(tmp_path / "outputs")
    cfg["paths"]["cache_db"] = str(tmp_path / "cache.sqlite")
    return cfg


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")
@pytest.mark.skipif(find_browser() is None, reason="no browser to print the PDF")
def test_pipeline_saves_every_stage_and_the_report(tmp_path):
    pipeline = Pipeline(config(tmp_path), ROOT, client=QuotingClient(), run_id="test")
    template = Source("tla_template_2023_public_project_revenue", "template", TEMPLATE)
    executed = Source("fx_03_modified", "executed", FIXTURES / "fx_03_modified.pdf",
                      title="Fixture 3")
    result = pipeline.run(executed, template)

    assert result.run_dir == tmp_path / "outputs" / "test" / "fx_03_modified"
    store = RunStore(tmp_path / "outputs", "test/fx_03_modified")
    assert all(store.exists(stage) for stage in STAGES)
    assert (result.run_dir / "report.html").exists() and (result.run_dir / "report.pdf").exists()
    assert result.pdf == (tmp_path / "outputs" / "reports"
                          / "fx_03_modified__vs__tla_template_2023_public_project_revenue.pdf")
    assert result.counts["total"] == 4 and result.counts["fill_in"] == 2
    assert result.usage.calls == 1 and result.stats["llm"] == 2
    html = (result.run_dir / "report.html").read_text(encoding="utf-8")
    assert "Fixture 3" in html and "<ins>ninety (90)</ins>" in html


def test_manifest_rows_name_their_pdf_and_template():
    manifest = read_manifest(ROOT / "data" / "manifest.csv")
    executed = [row for row in manifest.values() if row["type"] == "executed"]
    assert executed and all(row["template"] in manifest for row in executed)
    drle = source(manifest["tla_2021_drle"], ROOT)
    assert drle.path.name.endswith(".pdf") and drle.title.startswith("Downtown Redmond")
    with pytest.raises(SystemExit, match="names no PDF"):
        source({**manifest["tla_2021_drle"], "notes": "no file"}, ROOT)
