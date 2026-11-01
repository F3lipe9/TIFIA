"""TopicExtractor: one plain-English line per summary topic, written by the LLM from that
topic's sections only, and accepted only if it passes code checks:

- every quote (1-3, each 2-25 words) is in the excerpt word for word, after
  diff_engine.normalize and ignoring case;
- every number in the summary is in the excerpt, so no amount, rate or date is invented;
- it describes and does not advise ("should", "recommend", ...);
- the status agrees with the quotes: a value in brackets ("$[310,194,800]", "[___]%")
  is a draft blank whatever the model says; "not inserted" needs a quote such as
  "[to be inserted]"; "draft blank" needs a quote with the bracketed blank.

A row that fails is sent once more with the problem; if it fails again, or the model
says the excerpt does not cover the topic, the row says "See agreement §X". Requests go
out up to llm.max_workers at a time; checks and the cache stay on the calling thread
(as in the classifier). Rows that pass are cached by a hash of the prompt, the model, the
topic's question and the excerpt, so a rerun costs nothing until one of them changes.
"""

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from tifia_review.llm.client import LLMClient, LLMError
from tifia_review.models import Alignment, Document, SegmentedDocument, TopicRow, ValueStatus
from tifia_review.store import ResultCache
from tifia_review.summary import text as tx
from tifia_review.summary.mapping import RECITALS, TopicChanges, is_state_law_term

log = logging.getLogger(__name__)

MAX_SUMMARY_WORDS = 60
STATUSES = [s.value for s in ValueStatus]

SYSTEM_PROMPT = """\
You write one row of a two-page summary of a TIFIA loan agreement for an agency or \
client executive. The reader is not a lawyer: use plain English and minimal legal detail.

You get one topic, the question to answer for it, and excerpts of the agreement that \
cover it. Use only those excerpts.

Rules:
- Describe what the agreement says. Never advise, recommend or judge ("Interest is fixed \
at 4.12%", never "the rate is favorable"). Do not use the words "should", "recommend", \
"advise" or "suggest" unless the agreement itself uses them.
- Answer only this topic's question; other topics have their own rows.
- Write about the agreement, never about the excerpts: leave out what they do not say \
rather than noting that it is missing.
- At most two sentences and {max_words} words. No section numbers, no defined-term \
quotation marks, no legal citations unless the question asks for the law.
- Never guess. Every amount, rate, date and number you write must appear in the excerpts.
- quotes: one to three passages copied exactly from the excerpts that support the \
summary, each one continuous passage of at most 25 words, with no ellipses or edits. \
Copy brackets and blanks exactly as written.
- status:
  "found": the excerpts answer the question.
  "draft_blank": the key value is a bracketed blank or bracketed value in a draft \
(for example "$[310,194,800]" or "[_____] percent"). Say the value as written, and that \
it is not final. Quote the bracketed text.
  "not_inserted": the excerpts say the content is "[to be inserted]", "[Attached.]" or \
similar. Quote that.
  "not_found": the excerpts do not answer the question. Give an empty summary and no \
quotes.

Answer with a JSON object with the fields status, summary and quotes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": STATUSES},
        "summary": {"type": "string"},
        "quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "quotes"],
    "additionalProperties": False,
}


@dataclass
class _Topic:
    key: str
    label: str
    ask: str
    pieces: list[tx.Piece]
    sections: list[str]          # "§6": where to look when there is no summary
    cache_key: str = ""
    previous_problem: str = ""


class TopicExtractor:
    def __init__(self, client: LLMClient | None, topics: dict[str, dict],
                 cache: ResultCache | None = None, max_excerpt_chars: int = 60_000,
                 max_retries: int = 1, max_workers: int = 1):
        """client None: no LLM, every row falls back to "See agreement"."""
        self.client, self.topics, self.cache = client, topics, cache
        self.max_excerpt_chars, self.max_retries = max_excerpt_chars, max_retries
        self.max_workers = max(1, max_workers)
        self.system = SYSTEM_PROMPT.format(max_words=MAX_SUMMARY_WORDS)
        self.prompt_version = hashlib.sha256(
            (self.system + json.dumps(SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]

    def extract(self, template: SegmentedDocument, executed: SegmentedDocument,
                alignments: list[Alignment], executed_doc: Document | None,
                changes: dict[str, TopicChanges]) -> list[TopicRow]:
        """One TopicRow per topic, in config order."""
        pages = tx.Pages(executed_doc)
        topics = [self._topic(key, spec, executed, tx.aligned_sections(alignments))
                  for key, spec in self.topics.items()]
        answers: dict[str, tuple[dict | None, str | None]] = {}
        pending = []
        for t in topics:
            if not t.pieces:
                answers[t.key] = (None, "no section of the agreement matches this topic")
            elif self.client is None:
                answers[t.key] = (None, "not summarized: run without the LLM")
            else:
                t.cache_key = self._cache_key(t)
                cached = self.cache.get(t.cache_key) if self.cache is not None else None
                if cached is not None:
                    answers[t.key] = (cached, None)
                else:
                    pending.append(t)
        for _ in range(self.max_retries + 1):
            if not pending:
                break
            failed = []
            for t, reply in zip(pending, self._ask_all(pending)):
                problem = reply if isinstance(reply, str) else self._check(reply, t)
                if problem is None:
                    answers[t.key] = (reply, None)
                    if self.cache is not None:
                        self.cache.put(t.cache_key, reply)
                else:
                    t.previous_problem = problem
                    answers[t.key] = (None, problem)
                    failed.append(t)
            pending = failed
        return [self._row(t, *answers[t.key], changes.get(t.key, TopicChanges()), pages)
                for t in topics]

    # --- building the request --------------------------------------------------------

    def _topic(self, key: str, spec: dict, executed: SegmentedDocument,
               aligned: dict) -> _Topic:
        pieces: list[tx.Piece] = []
        for entry in spec.get("sections", []):
            entry = str(entry)
            if ":" in entry:
                piece = tx.definition_piece(entry.split(":", 1)[1], executed)
            else:
                section = tx.executed_section("preamble" if entry == RECITALS else entry,
                                              aligned, executed)
                piece = section and tx.Piece(
                    f"{tx.section_cite(section.section_id)}"
                    + (f" {section.heading}" if section.heading else ""),
                    tx.section_cite(section.section_id), section.text, section)
            if piece is not None and all(p.label != piece.label for p in pieces):
                pieces.append(piece)
        if spec.get("state_law_definitions"):
            for term, t in executed.defined_terms.items():
                if is_state_law_term(term, t.definition):
                    piece = tx.definition_piece(term, executed)
                    if piece is not None:
                        pieces.insert(0, piece)
        listed = ["1" if ":" in str(e) else "preamble" if str(e) == RECITALS else str(e)
                  for e in spec.get("sections", [])]
        sections = list(dict.fromkeys(p.cite for p in pieces)) or list(
            dict.fromkeys(map(tx.section_cite, listed)))
        return _Topic(key, spec.get("label", key), " ".join(spec["ask"].split()), pieces,
                      sections)

    def _message(self, t: _Topic) -> str:
        parts, room = [], self.max_excerpt_chars
        for p in t.pieces:
            body = p.text if len(p.text) <= room else p.text[:max(0, room)] + " [excerpt cut]"
            room -= len(p.text)
            parts.append(f"<excerpt source=\"{p.label}\">\n{body.strip()}\n</excerpt>")
            if room <= 0:
                break
        retry = (f"\n\nYour last answer failed a check: {t.previous_problem}. Fix that."
                 if t.previous_problem else "")
        return (f"Topic: {t.label}\nQuestion: {t.ask}{retry}\n\n" + "\n\n".join(parts))

    def _cache_key(self, t: _Topic) -> str:
        return hashlib.sha256(json.dumps(
            {"prompt": self.prompt_version, "model": self.client.model_id, "kind": "summary",
             "topic": t.label, "ask": t.ask, "message": self._message(t)},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _ask_all(self, topics: list[_Topic]):
        """The reply to each topic (a dict, or the problem as text), in order."""
        messages = [self._message(t) for t in topics]
        if self.max_workers == 1 or len(topics) < 2:
            yield from map(self._ask, messages)
            return
        pool = ThreadPoolExecutor(max_workers=min(self.max_workers, len(topics)),
                                  thread_name_prefix="summary")
        try:
            yield from pool.map(self._ask, messages)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _ask(self, message: str) -> dict | str:
        """Runs in a worker thread: no cache or shared state."""
        try:
            return self.client.complete_json(self.system, message, SCHEMA)
        except LLMError as e:
            log.warning("summary topic: %s", e)
            return f"no usable reply: {e}"

    # --- checks ----------------------------------------------------------------------

    def _check(self, reply: dict, t: _Topic) -> str | None:
        status = reply.get("status")
        if status not in STATUSES:
            return f"status {status!r} is not one of {STATUSES}"
        if status == ValueStatus.NOT_FOUND.value:
            return None
        summary, quotes = reply.get("summary"), reply.get("quotes")
        if not isinstance(summary, str) or not summary.strip():
            return "the summary is empty"
        if len(summary.split()) > MAX_SUMMARY_WORDS:
            return f"the summary has {len(summary.split())} words; the limit is {MAX_SUMMARY_WORDS}"
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 3:
            return "give one to three quotes"
        for quote in quotes:
            if problem := tx.quote_problem(quote, t.pieces):
                return problem
        source = "\n".join(p.text for p in t.pieces)
        if problem := (tx.numbers_problem(summary, source)
                       or tx.advice_problem(summary, source)):
            return problem
        if status == ValueStatus.NOT_INSERTED.value and not any(map(tx.not_inserted, quotes)):
            return "status not_inserted needs a quote showing the content was not inserted"
        if status == ValueStatus.DRAFT_BLANK.value and not any(map(tx.has_blank, quotes)):
            return "status draft_blank needs a quote showing the bracketed blank or value"
        return None

    def _row(self, t: _Topic, reply: dict | None, problem: str | None, c: TopicChanges,
             pages: tx.Pages) -> TopicRow:
        row = TopicRow(t.key, t.label, None, [], [], ValueStatus.NOT_FOUND,
                       len(c.substantive), [ld.deviation.deviation_id for ld in c.substantive[:3]],
                       c.other, c.needs_review, c.unclassified, t.sections, problem or "")
        if reply is None or reply["status"] == ValueStatus.NOT_FOUND.value:
            if reply is not None:
                row.problem = "the model found no answer in these sections"
            return row
        quotes = [tx.strip_quotes(q) for q in reply["quotes"]]
        status = ValueStatus(reply["status"])
        # code, not the model, has the last word on draft values
        if status is ValueStatus.FOUND and any(map(tx.has_blank, quotes)):
            status = ValueStatus.DRAFT_BLANK
        row.status, row.quotes = status, quotes
        row.summary = " ".join(reply["summary"].split())
        for q in quotes:
            found = tx.find_quote(q, t.pieces)
            if found:
                piece, offset = found
                cite = f"{piece.cite}, {pages.cite(tx.page_at(piece.section, offset))}"
                if cite not in row.sources:
                    row.sources.append(cite)
        return row
