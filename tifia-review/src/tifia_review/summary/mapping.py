"""TopicMapper: which summary topic each deviation belongs to, by code, never the LLM.

A deviation belongs to a topic when its template section, or a parent of it, is listed
for the topic (config summary.topics.<topic>.count_sections, else .sections): "9" takes 9,
9(a), 9(a)(i). count_sections narrows the count when the LLM needs more context than the
topic covers: borrowing authority reads all of Sections 12 and 13, but only 13(a), 13(b)
and the resolution clause are about borrowing authority.
"recitals" is the template's preamble. A deviation inside a definition belongs to a topic
only through "1:<term>" for that term, or, for a topic with state_law_definitions, when the
term names a statute and its definition cites state law (the borrower's enabling act is
named differently in every agreement: "HRTAC Act", "RCW 81.112"). A section added in the
agreement has no template section: it counts under the template section its agreement
section (or a parent) is paired with, else the same number.

"Other significant changes": substantive deviations in no topic, ranked by category
priority (summary.category_priority, else the taxonomy order), then by how many words
changed. Deviations that need review are counted, never listed as highlights; nor is
a template exhibit or schedule "removed" from an agreement PDF that has no exhibits at all
(a body-only file), since it is missing from the file, not from the deal.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from tifia_review.classifier import FILL_IN_CATEGORY, NOT_CLASSIFIED, RENUMBERED_CATEGORY
from tifia_review.diff_engine import normalize
from tifia_review.models import Alignment, LabeledDeviation, SegmentedDocument

RECITALS = "recitals"
# a state's statutes, not federal ones: "Code of Virginia", "Va. Code", "RCW", "Revised Code"
STATE_LAW = re.compile(r"\bCode of (?!Federal)[A-Z][a-z]+|\b[A-Z][a-z]{1,4}\.? Code\b|\bRCW\b|"
                       r"\bRevised Code\b|\b(?:General )?Laws of (?!the United)|"
                       r"\b[A-Z][a-z]+ (?:Revised )?Statutes\b")
STATUTE_NAME = re.compile(r"\b(Act|Code|Law|Statute)s?$")


@dataclass
class TopicChanges:
    substantive: list[LabeledDeviation] = field(default_factory=list)  # ranked
    other: int = 0           # not substantive, fill-ins and renumberings included
    needs_review: int = 0
    unclassified: int = 0


def is_state_law_term(term: str, definition: str) -> bool:
    return bool(STATUTE_NAME.search(term.strip())) and bool(STATE_LAW.search(definition))


def changed_words(template: str, executed: str) -> int:
    """Words removed plus words added."""
    a, b = normalize(template).split(), normalize(executed).split()
    return sum((i2 - i1) + (j2 - j1) for op, i1, i2, j1, j2 in
               SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")


class TopicMapper:
    def __init__(self, topics: dict[str, dict], category_priority: list[str],
                 alignments: list[Alignment], template: SegmentedDocument,
                 executed: SegmentedDocument):
        self.topics = topics
        self.priority = {c: n for n, c in enumerate(category_priority)}
        self.template, self.executed = template, executed
        # agreement section -> template section, for sections added in the agreement
        self.to_template = {a.executed_section.section_id: a.template_section.section_id
                            for a in alignments
                            if a.executed_section is not None and a.template_section is not None}
        self.body_only = not any(s.section_id.startswith(("Exhibit", "Schedule"))
                                 for s in executed.sections)
        self.parents = {s.section_id: s.parent_id for s in executed.sections}
        self.parents.update({s.section_id: s.parent_id for s in template.sections})

    def topic_of(self, ld: LabeledDeviation) -> str | None:
        d = ld.deviation
        term = d.affects_defined_term
        section = d.template_section_id or self._template_for_added(d.executed_section_id)
        for topic, spec in self.topics.items():
            for entry in spec.get("count_sections", spec.get("sections", [])):
                entry = str(entry)
                if term is not None:
                    if ":" in entry and entry.split(":", 1)[1].casefold() == term.casefold():
                        return topic
                elif entry == RECITALS and section == "preamble":
                    return topic
                elif ":" not in entry and entry != RECITALS and section is not None \
                        and self._within(section, entry):
                    return topic
            if term is not None and spec.get("state_law_definitions") and \
                    is_state_law_term(term, self._definition(term)):
                return topic
        return None

    def map(self, labeled: list[LabeledDeviation]) -> tuple[dict[str, TopicChanges],
                                                            list[LabeledDeviation]]:
        """Changes per topic (every topic present, maybe empty) and every substantive
        deviation outside the topics, both ranked most significant first."""
        changes = {topic: TopicChanges() for topic in self.topics}
        outside = []
        for ld in labeled:
            topic = self.topic_of(ld)
            if topic is None:
                if self._significant(ld):
                    outside.append(ld)
                continue
            c = changes[topic]
            if ld.needs_review:
                c.needs_review += 1
            elif ld.category == NOT_CLASSIFIED:
                c.unclassified += 1
            elif ld.substantive and ld.category not in (FILL_IN_CATEGORY, RENUMBERED_CATEGORY):
                c.substantive.append(ld)
            else:
                c.other += 1
        for c in changes.values():
            c.substantive.sort(key=self._rank)
        return changes, sorted(outside, key=self._rank)

    # --- helpers ---------------------------------------------------------------------

    def _significant(self, ld: LabeledDeviation) -> bool:
        d = ld.deviation
        missing_exhibit = (self.body_only and d.executed_section_id is None
                           and (d.template_section_id or "").startswith(("Exhibit", "Schedule")))
        return (ld.substantive and not ld.needs_review and not missing_exhibit
                and ld.category not in (FILL_IN_CATEGORY, RENUMBERED_CATEGORY, NOT_CLASSIFIED))

    def _rank(self, ld: LabeledDeviation) -> tuple[int, int, str]:
        d = ld.deviation
        return (self.priority.get(ld.category, len(self.priority)),
                -changed_words(d.template_text, d.executed_text), d.deviation_id)

    def _within(self, section: str, listed: str) -> bool:
        seen = set()
        while section is not None and section not in seen:
            if section == listed:
                return True
            seen.add(section)
            section = self.parents.get(section)
        return False

    def _template_for_added(self, executed_id: str | None) -> str | None:
        section, seen = executed_id, set()
        while section is not None and section not in seen:
            if section in self.to_template:
                return self.to_template[section]
            seen.add(section)
            section = self.parents.get(section)
        return executed_id

    def _definition(self, term: str) -> str:
        for doc in (self.executed, self.template):
            for name, t in doc.defined_terms.items():
                if name.casefold() == term.casefold():
                    return t.definition
        return ""
