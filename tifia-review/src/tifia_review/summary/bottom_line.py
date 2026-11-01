"""BottomLineWriter: two or three sentences on how close the agreement is to the
template and where the main changes are, from the topic rows' change counts only (the
LLM sees no agreement text).

Checks: 2-3 sentences, at most 80 words; every topic it names exists, and is listed as
named; every number is one of the counts given; no advice. If any check fails, or there
is no LLM, a sentence built from the counts is used instead.
"""

import hashlib
import json
import logging
import re

from tifia_review.llm.client import LLMClient, LLMError
from tifia_review.models import TopicRow
from tifia_review.store import ResultCache
from tifia_review.summary.text import advice_problem

log = logging.getLogger(__name__)

MAX_WORDS = 80

SYSTEM_PROMPT = """\
You write the bottom line at the top of a two-page summary of a TIFIA loan agreement, for \
an agency or client executive. You get counts of how the agreement differs from the \
standard U.S. DOT template, by topic. Write two or three plain-English sentences: \
overall, how close the agreement is to the template, and which topics have the most \
substantive changes. Describe with the counts; never advise or judge (no words such as \
"heavily", "favorable" or "concerning"). Use only the counts given; any \
number you write must be one of them. Name topics exactly as given. At most 80 words.

Answer with a JSON object: {"text": "...", "topics_mentioned": ["<topic name>", ...]}."""

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"},
                   "topics_mentioned": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "topics_mentioned"],
    "additionalProperties": False,
}


def facts(rows: list[TopicRow], totals: dict, other_topic: int) -> dict:
    return {
        "total_deviations": totals["total"],
        "substantive_deviations": totals["substantive"],
        "needs_review": totals["needs_review"],
        "substantive_outside_these_topics": other_topic,
        "topics": [{"topic": r.label, "substantive_changes": r.substantive_changes,
                    "minor_changes": r.other_changes} for r in rows],
    }


def fallback(rows: list[TopicRow], totals: dict, other_topic: int) -> str:
    changed = sorted((r for r in rows if r.substantive_changes),
                     key=lambda r: -r.substantive_changes)
    standard = [r.label for r in rows if not r.substantive_changes and not r.needs_review]
    first = (f"The agreement differs from the template in {totals['substantive']} substantive "
             f"places out of {totals['total']} differences found.")
    if changed:
        top = ", ".join(f"{r.label} ({r.substantive_changes})" for r in changed[:3])
        second = f" Among the topics below, the most substantive changes are in {top}"
        second += (f"; {other_topic} more are outside these topics." if other_topic else ".")
    else:
        second = " None of the topics below has a substantive change."
    if standard:
        second += f" {_join(standard[:4])} follow{'s' if len(standard) == 1 else ''} the template."
    return first + second


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class BottomLineWriter:
    def __init__(self, client: LLMClient | None, cache: ResultCache | None = None,
                 max_retries: int = 1):
        self.client, self.cache, self.max_retries = client, cache, max_retries
        self.version = hashlib.sha256(
            (SYSTEM_PROMPT + json.dumps(SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]

    def write(self, rows: list[TopicRow], totals: dict, other_topic: int) -> tuple[str, bool]:
        """(text, written by the LLM)."""
        given = facts(rows, totals, other_topic)
        if self.client is None:
            return fallback(rows, totals, other_topic), False
        key = hashlib.sha256(json.dumps({"prompt": self.version, "model": self.client.model_id,
                                         "kind": "bottom_line", "facts": given},
                                        sort_keys=True).encode()).hexdigest()
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is not None:
            return cached["text"], True
        message = json.dumps(given, indent=1)
        for _ in range(self.max_retries + 1):
            try:
                reply = self.client.complete_json(SYSTEM_PROMPT, message, SCHEMA)
            except LLMError as e:
                log.warning("bottom line: %s", e)
                continue
            problem = check(reply, given)
            if problem is None:
                if self.cache is not None:
                    self.cache.put(key, reply)
                return " ".join(reply["text"].split()), True
            log.info("bottom line failed a check: %s", problem)
            message = json.dumps(given, indent=1) + f"\n\nYour last answer failed a check: {problem}."
        return fallback(rows, totals, other_topic), False


def check(reply: dict, given: dict) -> str | None:
    text, named = reply.get("text"), reply.get("topics_mentioned")
    if not isinstance(text, str) or not text.strip():
        return "the text is empty"
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if not 2 <= len(sentences) <= 3:
        return f"write two or three sentences, not {len(sentences)}"
    if len(text.split()) > MAX_WORDS:
        return f"at most {MAX_WORDS} words"
    labels = {t["topic"].casefold() for t in given["topics"]}
    if not isinstance(named, list) or not all(isinstance(n, str) for n in named):
        return "topics_mentioned must be a list of topic names"
    for name in named:
        if name.casefold() not in labels:
            return f"{name!r} is not one of the topics"
        if name.casefold() not in text.casefold():
            return f"{name!r} is listed as mentioned but is not in the text"
    numbers = {str(v) for v in (given["total_deviations"], given["substantive_deviations"],
                                given["needs_review"], given["substantive_outside_these_topics"])}
    numbers |= {str(t[k]) for t in given["topics"] for k in ("substantive_changes",
                                                             "minor_changes")}
    for n in re.findall(r"\d[\d,]*", text):
        if n.replace(",", "") not in numbers:
            return f"the number {n} is not one of the counts given"
    return advice_problem(text)
