"""Pipeline: load -> segment -> align -> diff -> classify -> report -> summary, for one
executed agreement against its template.

Every stage's output is saved to outputs/<run_id>/<executed doc_id>/<stage>.json
(store.RunStore), so any stage can be rerun or debugged from the one before; the report
goes next to them as report.html and report.pdf, and the PDF is copied to
outputs/reports/<executed>__vs__<template>.pdf. The two-page executive summary
(summary/, config summary) goes next to them as summary.html/.pdf/.json, copied to
outputs/reports/<executed>_summary.pdf; it is skipped for a held-out agreement
(evaluation.holdout) until its score is recorded. A template used for several agreements
is loaded and segmented once per run.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from tifia_review.aligner import Aligner
from tifia_review.classifier import Classifier, unclassified
from tifia_review.diff_engine import DiffEngine
from tifia_review.llm.client import LLMClient, Usage, make_client
from tifia_review.loader import DocumentLoader
from tifia_review.models import Document, SegmentedDocument
from tifia_review.report.builder import build_report_data, write_report
from tifia_review.segmenter import Segmenter
from tifia_review.store import ResultCache, RunStore, new_run_id
from tifia_review.summary.builder import SummaryTooLong
from tifia_review.summary.summarize import Summarizer

log = logging.getLogger(__name__)


@dataclass
class Source:
    """A document to compare, from its manifest row."""
    doc_id: str
    doc_type: str
    path: Path
    title: str = ""
    signing_date: str = ""
    template_version: str = ""
    full_path: Path | None = None  # the PDF with its exhibits, if path is the body only
    exhibit_pages: str = ""        # override for the debt service exhibit, e.g. "120-122"


@dataclass
class Result:
    executed_id: str
    template_id: str
    pdf: Path               # the copy in outputs/reports/
    run_dir: Path
    renderer: str
    counts: dict
    usage: Usage | None = None  # LLM tokens of this agreement, None without the LLM
    stats: dict = field(default_factory=dict)
    summary_pdf: Path | None = None  # outputs/reports/<executed>_summary.pdf
    summary_note: str = ""           # why there is no summary, or how it was shortened


class Pipeline:
    def __init__(self, config: dict, root: Path, classify: bool = True,
                 client: LLMClient | None = None, run_id: str | None = None):
        """config: config.yaml as a dict; paths in it are relative to root. classify=False
        skips the LLM (the report then says the deviations are not classified)."""
        self.config, self.root = config, Path(root)
        self.run_id = run_id or new_run_id()
        self.outputs = self.root / config["paths"]["outputs_dir"]
        loader, aligner = config["loader"], config["aligner"]
        self.loader = DocumentLoader(loader["scanned_page_min_chars"],
                                     loader["max_low_text_fraction"])
        self.segmenter = Segmenter()
        self.aligner = Aligner(threshold=aligner["similarity_threshold"],
                               model_name=aligner["embedding_model"])
        self.engine = DiffEngine()
        self.classifier: Classifier | None = None
        cache = ResultCache(self.root / config["paths"]["cache_db"])
        if classify:
            self.classifier = Classifier.from_config(
                client or make_client(config["llm"]), config, cache)
        summary = config.get("summary") or {}
        self.summarizer = (Summarizer(config, self.classifier.client if self.classifier
                                      else None, cache)
                           if summary.get("enabled", True) and summary.get("topics") else None)
        self.holdout = set((config.get("evaluation") or {}).get("holdout") or [])
        self._docs: dict[str, tuple[Document, SegmentedDocument]] = {}

    def run(self, executed: Source, template: Source) -> Result:
        store = RunStore(self.outputs, f"{self.run_id}/{executed.doc_id}")
        t_doc, t_seg = self._load(template)
        e_doc, e_seg = self._load(executed)
        for stage, output in (("load_template", t_doc), ("load_executed", e_doc),
                              ("segment_template", t_seg), ("segment_executed", e_seg)):
            store.save(stage, output)
        alignments = self.aligner.align(t_seg, e_seg)
        store.save("align", alignments)
        deviations = self.engine.diff(t_seg, e_seg, alignments)
        store.save("diff", deviations)

        usage, stats, before = None, {}, None
        if self.classifier is not None:
            client = self.classifier.client
            before = Usage(**vars(client.usage))
            self.classifier.stats.clear()
            labeled = self.classifier.classify(deviations, t_seg, e_seg)
            stats = dict(self.classifier.stats)
        else:
            labeled = unclassified(deviations)
        store.save("classify", labeled)

        data = build_report_data(labeled, t_doc, e_doc, t_seg, e_seg, alignments,
                                 list(self.config["taxonomy"]), self._meta(executed, template))
        _, pdf, renderer = write_report(data, store.dir)
        reports = self.outputs / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        copy = reports / f"{executed.doc_id}__vs__{template.doc_id}.pdf"
        shutil.copyfile(pdf, copy)
        summary_pdf, note = self._summary(executed, template, labeled, data.counts, t_seg,
                                          e_seg, alignments, e_doc, store, copy.name)
        if before is not None:  # the classifier's and the summary's LLM calls
            client = self.classifier.client
            usage = Usage(**{k: v - getattr(before, k) for k, v in vars(client.usage).items()})
        return Result(executed.doc_id, template.doc_id, copy, store.dir, renderer, data.counts,
                      usage, stats, summary_pdf, note)

    def _summary(self, executed: Source, template: Source, labeled, counts, t_seg, e_seg,
                 alignments, e_doc, store: RunStore, full_report: str
                 ) -> tuple[Path | None, str]:
        if self.summarizer is None:
            return None, "summary turned off (config summary)"
        if executed.doc_id in self.holdout:
            return None, "held out: no summary until its score is recorded"
        meta = {"agreement_id": executed.doc_id,
                "agreement_title": executed.title or executed.doc_id,
                "template_title": template.title or template.doc_id,
                "signing_date": executed.signing_date, "run_id": self.run_id,
                "full_report": full_report}
        data = self.summarizer.build(labeled, counts, t_seg, e_seg, alignments, e_doc, meta,
                                     executed.full_path or executed.path, executed.exhibit_pages)
        try:
            _, pdf, _, shown = self.summarizer.write(data, store.dir)
        except SummaryTooLong as e:
            log.error("%s", e)
            return None, str(e)
        copy = self.outputs / "reports" / f"{executed.doc_id}_summary.pdf"
        shutil.copyfile(pdf, copy)
        return copy, "shortened to fit" if shown.level else ""

    def _load(self, source: Source) -> tuple[Document, SegmentedDocument]:
        if source.doc_id not in self._docs:
            doc = self.loader.load(str(source.path), source.doc_id, source.doc_type)
            self._docs[source.doc_id] = (doc, self.segmenter.segment(doc))
        return self._docs[source.doc_id]

    def _meta(self, executed: Source, template: Source) -> dict:
        llm = self.config["llm"]
        if self.classifier is not None:
            model = f"{llm['model']} (effort {llm.get('effort') or 'default'})"
            classification = (f"{model}, prompt version {self.classifier.prompt_version}, "
                              "draft taxonomy")
            method = (f"Each deviation was labeled by {model} with a category, a substantive "
                      "flag and a one-sentence explanation. The model only labels the "
                      "deviations found above; it does not decide what changed. Every label's "
                      "evidence quote was checked word for word against the agreement text "
                      "(the template text for a removal); labels that failed twice, or that "
                      "the model could not place in a category, are under Needs review. "
                      "Fill-ins and renumberings are labeled without the model.")
        else:
            classification = "not run: no categories, substantive flags or explanations"
            method = ("Not run for this report, so the deviations have no categories, "
                      "substantive flags or explanations, and all of them are shown.")
        return {
            "agreement_id": executed.doc_id, "agreement_title": executed.title or executed.doc_id,
            "agreement_file": executed.path.name, "signing_date": executed.signing_date,
            "template_id": template.doc_id, "template_title": template.title or template.doc_id,
            "template_file": template.path.name, "template_version": template.template_version,
            "run_id": self.run_id, "threshold": self.config["aligner"]["similarity_threshold"],
            "classification": classification, "classification_method": method,
        }
