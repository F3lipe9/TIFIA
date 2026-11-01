"""Classifier: give each deviation a category, a substantive flag, a one-sentence
explanation and an evidence quote, with an LLM.

The LLM only labels what the diff found; it never decides whether something changed.
Fill-ins and renumberings are labeled without it. Deviations go out in batches (10-20 per
call), and every label is checked: category from the taxonomy (or "unclear"), substantive
a boolean, an explanation, and evidence found word for word in the executed text (the
template text when there is no executed text) after diff_engine.normalize. A deviation
that fails is sent once more with the problem; if it fails again, or the answer is
"unclear", it is marked needs_review.

Batches can go out in parallel (llm.max_workers). Only the requests run in worker
threads: answers are checked, cached and counted on the calling thread, in batch order,
so the cache (a SQLite connection that belongs to one thread) is written from one place
and a run gives the same labels, retries and output order however the requests finish.

Labels that pass are cached by a hash of the prompt version, the model and the deviation
as sent (its texts, sections and defined term), so a rerun costs nothing and a changed
prompt or taxonomy misses the cache. Failures are not cached, so a rerun tries them again.
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from tifia_review.diff_engine import normalize
from tifia_review.llm.client import LLMClient, LLMError
from tifia_review.llm.prompts import UNCLEAR, build_system_prompt, label_schema, render_batch
from tifia_review.models import (
    ChangeType, Deviation, LabeledDeviation, Section, SegmentedDocument,
)
from tifia_review.store import ResultCache

log = logging.getLogger(__name__)

FILL_IN_CATEGORY = "Fill-in"
RENUMBERED_CATEGORY = "Renumbering"
NOT_CLASSIFIED = "Not classified"  # a run without the LLM (see unclassified())
_EVIDENCE_WORDS = (2, 40)  # the prompt asks for at most 25 words
_QUOTE_MARKS = "\"'“”‘’"

_Pending = tuple[Deviation, dict, str]  # deviation, item sent to the LLM, cache key


class Classifier:
    def __init__(self, client: LLMClient, taxonomy: dict[str, str], substantive: str,
                 cache: ResultCache | None = None, batch_size: int = 15,
                 max_batch_chars: int = 40_000, max_retries: int = 1, max_workers: int = 1):
        """taxonomy: category name -> definition; substantive: the definition of a
        substantive change. Both go into the system prompt. max_workers: requests in
        flight at once."""
        self.client, self.cache = client, cache
        self.batch_size, self.max_batch_chars, self.max_retries = (
            batch_size, max_batch_chars, max_retries)
        self.max_workers = max(1, max_workers)
        self.categories = [*taxonomy, UNCLEAR]
        self.system = build_system_prompt(taxonomy, substantive)
        self.schema = label_schema(self.categories)
        self.prompt_version = hashlib.sha256(
            (self.system + json.dumps(self.schema, sort_keys=True)).encode()).hexdigest()[:12]
        self.stats: Counter[str] = Counter()

    @classmethod
    def from_config(cls, client: LLMClient, config: dict,
                    cache: ResultCache | None = None) -> "Classifier":
        """From config.yaml: taxonomy, substantive and the llm section."""
        llm = config["llm"]
        return cls(client, config["taxonomy"], config["substantive"], cache,
                   batch_size=llm.get("batch_size", 15),
                   max_batch_chars=llm.get("max_batch_chars", 40_000),
                   max_retries=llm.get("max_retries", 1),
                   max_workers=llm.get("max_workers", 1))

    def classify(self, deviations: list[Deviation], template: SegmentedDocument | None = None,
                 executed: SegmentedDocument | None = None) -> list[LabeledDeviation]:
        """One LabeledDeviation per deviation, in order. The documents, if given, supply
        section captions as context."""
        labeled: dict[str, LabeledDeviation] = {}
        pending = self._prepare(deviations, template, executed, labeled)
        problems: dict[str, str] = {}
        last: dict[str, dict | None] = {}
        for attempt in range(self.max_retries + 1):
            if not pending:
                break
            if attempt:
                self.stats["retried"] += len(pending)
            failed = []
            batches = list(self._batches(pending))
            for batch, answers in zip(batches, self._ask_all(batches, problems)):
                for d, item, key in batch:
                    label, problem = answers[d.deviation_id]
                    if problem is None:
                        labeled[d.deviation_id] = self._labeled(d, label)
                        self.stats["llm"] += 1
                        if self.cache is not None:
                            self.cache.put(key, label)
                    else:
                        failed.append((d, item, key))
                        problems[d.deviation_id], last[d.deviation_id] = problem, label
            pending = failed
        for d, _, _ in pending:
            labeled[d.deviation_id] = self._unverified(d, last.get(d.deviation_id),
                                                       problems[d.deviation_id])
        self.stats["needs_review"] += sum(labeled[d.deviation_id].needs_review
                                          for d in deviations)
        return [labeled[d.deviation_id] for d in deviations]

    def plan(self, deviations: list[Deviation], template: SegmentedDocument | None = None,
             executed: SegmentedDocument | None = None) -> list[tuple[str, int]]:
        """(user message, number of deviations) for each call classify() would make
        before any retry, without calling the LLM: for a cost estimate."""
        pending = self._prepare(deviations, template, executed, {})
        return [(render_batch([item for _, item, _ in batch]), len(batch))
                for batch in self._batches(pending)]

    # --- steps -----------------------------------------------------------------------

    def _prepare(self, deviations, template, executed,
                 labeled: dict[str, LabeledDeviation]) -> list[_Pending]:
        """Label what needs no LLM call (fill-ins, renumberings, cached labels) into
        labeled; return the rest."""
        pending = []
        t_index, e_index = _index(template), _index(executed)
        for d in deviations:
            fixed = label_without_llm(d)
            if fixed is not None:
                labeled[d.deviation_id] = fixed
                self.stats[d.change_type.value] += 1
                continue
            item = self._item(d, t_index, e_index)
            key = hashlib.sha256(json.dumps(
                {"prompt": self.prompt_version, "model": self.client.model_id,
                 "item": {k: v for k, v in item.items() if k != "id"}},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            cached = self.cache.get(key) if self.cache is not None else None
            if cached is not None:
                labeled[d.deviation_id] = self._labeled(d, cached)
                self.stats["cached"] += 1
            else:
                pending.append((d, item, key))
        return pending

    def _item(self, d: Deviation, t_index: dict[str, Section],
              e_index: dict[str, Section]) -> dict:
        item: dict = {"id": d.deviation_id, "change": d.change_type.value}
        if d.template_section_id:
            item["template_section"] = _place(t_index, d.template_section_id)
        if d.executed_section_id:
            item["agreement_section"] = _place(e_index, d.executed_section_id)
        if d.affects_defined_term:
            item["defined_term"] = d.affects_defined_term
        if d.template_text:
            item["template_text"] = d.template_text
        if d.executed_text:
            item["agreement_text"] = d.executed_text
        return item

    def _batches(self, pending: list[_Pending]) -> Iterator[list[_Pending]]:
        """Up to batch_size deviations and about max_batch_chars of JSON per call."""
        batch: list[_Pending] = []
        size = 0
        for entry in pending:
            n = len(json.dumps(entry[1], ensure_ascii=False))
            if batch and (len(batch) >= self.batch_size or size + n > self.max_batch_chars):
                yield batch
                batch, size = [], 0
            batch.append(entry)
            size += n
        if batch:
            yield batch

    def _ask_all(self, batches: list[list[_Pending]], problems: dict[str, str]
                 ) -> Iterator[dict[str, tuple[dict | None, str | None]]]:
        """_ask for each batch, up to max_workers at a time; answers come back in batch
        order as they are ready. The messages are built here, before any worker starts,
        so workers only send requests and read nothing that changes."""
        messages = [[dict(item, previous_problem=problems[d.deviation_id])
                     if d.deviation_id in problems else item for d, item, _ in batch]
                    for batch in batches]
        if self.max_workers == 1 or len(batches) < 2:
            yield from map(self._ask, batches, messages)
            return
        pool = ThreadPoolExecutor(max_workers=min(self.max_workers, len(batches)),
                                  thread_name_prefix="classify")
        try:
            yield from pool.map(self._ask, batches, messages)
        finally:
            # after an API error (bad key, rate limits past the retries) or Ctrl+C, send
            # nothing more: requests not started are dropped, the ones in flight finish
            pool.shutdown(wait=True, cancel_futures=True)

    def _ask(self, batch: list[_Pending],
             items: list[dict]) -> dict[str, tuple[dict | None, str | None]]:
        """deviation id -> (label, problem); problem is None if the label passed.
        items: the batch as sent, with any previous problem. Runs in a worker thread:
        it must not write to the cache or the stats."""
        try:
            reply = self.client.complete_json(self.system, render_batch(items), self.schema)
        except LLMError as e:
            log.warning("batch of %d: %s", len(batch), e)
            return {d.deviation_id: (None, f"no usable reply: {e}") for d, _, _ in batch}
        labels = reply.get("labels")
        if not isinstance(labels, list):
            return {d.deviation_id: (None, "the reply had no labels list") for d, _, _ in batch}
        by_id: dict[str, dict] = {}
        for label in labels:
            if isinstance(label, dict) and isinstance(label.get("id"), str):
                by_id.setdefault(label["id"], label)
        answers = {}
        for d, _, _ in batch:
            label = by_id.get(d.deviation_id)
            answers[d.deviation_id] = (label, "no label was returned for this id"
                                       if label is None else self._check(d, label))
        return answers

    def _check(self, d: Deviation, label: dict) -> str | None:
        """What is wrong with a label, or None."""
        if label.get("category") not in self.categories:
            return f"category {label.get('category')!r} is not one of the categories"
        if not isinstance(label.get("substantive"), bool):
            return "substantive must be true or false"
        if not isinstance(label.get("explanation"), str) or not label["explanation"].strip():
            return "the explanation is empty"
        evidence = label.get("evidence")
        if not isinstance(evidence, str):
            return "evidence is missing"
        quote = normalize(_strip_quotes(evidence))
        if not _EVIDENCE_WORDS[0] <= len(quote.split()) <= _EVIDENCE_WORDS[1]:
            return (f"evidence must be a passage of {_EVIDENCE_WORDS[0]} to 25 words, "
                    f"not {len(quote.split())}")
        source, name = ((d.executed_text, "agreement") if d.executed_text.strip()
                        else (d.template_text, "template"))
        if quote.casefold() not in normalize(source).casefold():
            return f"evidence is not copied word for word from the {name} text"
        return None

    def _labeled(self, d: Deviation, label: dict) -> LabeledDeviation:
        unclear = label["category"] == UNCLEAR
        return LabeledDeviation(
            d, label["category"], label["substantive"], label["explanation"].strip(),
            needs_review=unclear, evidence=_strip_quotes(label["evidence"]),
            review_reason="no category fits (the model answered unclear)" if unclear else "")

    def _unverified(self, d: Deviation, label: dict | None, problem: str) -> LabeledDeviation:
        """Whatever usable fields the last answer had, marked needs_review."""
        label = label or {}
        category = label.get("category")
        explanation, evidence = label.get("explanation"), label.get("evidence")
        substantive = label.get("substantive")
        return LabeledDeviation(
            d, category if category in self.categories else UNCLEAR,
            substantive if isinstance(substantive, bool) else True,
            explanation.strip() if isinstance(explanation, str) else "",
            needs_review=True, evidence=evidence if isinstance(evidence, str) else "",
            review_reason=problem)


def label_without_llm(d: Deviation) -> LabeledDeviation | None:
    """The fixed label of a fill-in or renumbering; None for anything else."""
    if d.change_type is ChangeType.FILL_IN:
        return LabeledDeviation(d, FILL_IN_CATEGORY, False,
                                "A template blank or optional passage was filled in or left "
                                "out as the template provides; not a negotiated change.")
    if d.change_type is ChangeType.RENUMBERED:
        return LabeledDeviation(d, RENUMBERED_CATEGORY, False,
                                f"Template section {d.template_section_id} is numbered "
                                f"{d.executed_section_id} in the agreement.")
    return None


def unclassified(deviations: list[Deviation]) -> list[LabeledDeviation]:
    """Labels for a run without the LLM: fill-ins and renumberings as usual, everything
    else NOT_CLASSIFIED and shown as substantive, so the report leaves nothing out."""
    return [label_without_llm(d) or LabeledDeviation(d, NOT_CLASSIFIED, True, "")
            for d in deviations]


def _index(doc: SegmentedDocument | None) -> dict[str, Section]:
    return {s.section_id: s for s in doc.sections} if doc is not None else {}


def _place(index: dict[str, Section], section_id: str) -> str:
    """'12(a)(xii) (Conditions Precedent > Conditions Precedent to Effectiveness)': the
    section with its caption and its parents' captions, if the document was given."""
    captions = []
    section = index.get(section_id)
    while section is not None:
        if section.heading:
            captions.append(section.heading)
        section = index.get(section.parent_id) if section.parent_id else None
    return f"{section_id} ({' > '.join(reversed(captions))})" if captions else section_id


def _strip_quotes(evidence: str) -> str:
    """The quote as written, without quote marks around it; normalized only to match."""
    return evidence.strip().strip(_QUOTE_MARKS).strip()
