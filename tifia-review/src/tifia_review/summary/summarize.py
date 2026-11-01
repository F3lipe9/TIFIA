"""Summarizer: everything the summary needs, in order, for one agreement (config summary).

    deviations -> TopicMapper -> change counts per topic + other significant changes
    sections   -> TopicExtractor (LLM) -> one verified line per topic
    PDF        -> ExhibitLocator + ScheduleParser -> debt service digest (no LLM)
    counts     -> BottomLineWriter (LLM) -> the bottom line
    all        -> SummaryBuilder -> summary.html / summary.pdf / summary.json
"""

from pathlib import Path

from tifia_review.classifier import FILL_IN_CATEGORY, NOT_CLASSIFIED, RENUMBERED_CATEGORY
from tifia_review.llm.client import LLMClient
from tifia_review.models import Alignment, Document, LabeledDeviation, SegmentedDocument
from tifia_review.store import ResultCache
from tifia_review.summary import text as tx
from tifia_review.summary.bottom_line import BottomLineWriter
from tifia_review.summary.builder import SummaryBuilder, SummaryData, other_changes, parties
from tifia_review.summary.exhibits import ExhibitLocator, ExhibitPages
from tifia_review.summary.mapping import TopicMapper
from tifia_review.summary.schedule import ScheduleParser, loan_amount
from tifia_review.summary.topics import TopicExtractor


class Summarizer:
    def __init__(self, config: dict, client: LLMClient | None,
                 cache: ResultCache | None = None):
        """client None: no LLM; rows then say "See agreement" and the bottom line is built
        from the counts."""
        s, llm = config["summary"], config.get("llm", {})
        self.topics = s["topics"]
        self.priority = s.get("category_priority") or list(config.get("taxonomy", {}))
        self.top_n = s.get("other_significant_changes", {}).get("top_n", 3)
        self.loan_section = str(s.get("loan_amount_section", "3"))
        self.extractor = TopicExtractor(client, self.topics, cache,
                                        s.get("max_excerpt_chars", 60_000),
                                        llm.get("max_retries", 1), llm.get("max_workers", 1))
        self.writer = BottomLineWriter(client, cache, llm.get("max_retries", 1))
        self.builder = SummaryBuilder(s.get("max_pages", 2))

    def build(self, labeled: list[LabeledDeviation], counts: dict, template: SegmentedDocument,
              executed: SegmentedDocument, alignments: list[Alignment],
              executed_doc: Document | None, meta: dict, exhibit_pdf: Path | None,
              exhibit_pages: str = "") -> SummaryData:
        """The summary's content. counts: the full report's counts, so the totals match.
        exhibit_pdf: the agreement PDF with its exhibits (None: no debt service digest)."""
        mapper = TopicMapper(self.topics, self.priority, alignments, template, executed)
        changes, outside = mapper.map(labeled)
        rows = self.extractor.extract(template, executed, alignments, executed_doc, changes)
        classified = any(ld.category != NOT_CLASSIFIED for ld in labeled
                         if ld.category not in (FILL_IN_CATEGORY, RENUMBERED_CATEGORY))
        totals = {k: counts[k] for k in ("total", "substantive", "needs_review")}
        bottom, by_llm = self.writer.write(rows, totals, len(outside) if classified else 0)

        aligned = tx.aligned_sections(alignments)
        section = tx.executed_section(self.loan_section, aligned, executed)
        loan = loan_amount(section.text) if section is not None else None
        schedule = None
        if any(r.topic == "payment_schedule" for r in rows):
            exhibit = (ExhibitLocator().find(exhibit_pdf, exhibit_pages)
                       if exhibit_pdf is not None and exhibit_pdf.exists()
                       else ExhibitPages(None, [], ""))
            schedule = ScheduleParser().parse(exhibit_pdf, exhibit, loan)
        preamble = tx.executed_section("preamble", aligned, executed)
        borrower, lender = parties(preamble.text if preamble else "")
        meta = {**meta, "borrower": borrower, "lender": lender,
                "date": meta.get("signing_date") or "Draft — not dated",
                "draft": not meta.get("signing_date")}
        return SummaryData(meta, bottom, by_llm, rows,
                           other_changes(outside, self.top_n) if classified else [],
                           len(outside) if classified else 0, schedule, totals, classified)

    def write(self, data: SummaryData, out_dir: Path):
        return self.builder.write(data, out_dir)

