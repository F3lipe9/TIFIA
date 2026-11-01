"""Diff engine: the sentences added, removed or changed in each aligned section pair.

Both texts are normalized (whitespace, quotes, dashes, ligatures, hyphens at line breaks,
leftover page-label lines), split into sentences and compared with difflib. Neighboring
changed sentences become one Deviation. A sentence whose only differences fall inside the
template's placeholders is a FILL_IN, not a deviation: blanks and instructions ("[____]",
"[BORROWER]", "[insert ...]") may hold any text, and optional or alternative text ("[or
Subordinated Obligations]", "[each][at least one (1)]") may only be kept or dropped.

Each section's own text is compared (the text before its first sub-clause, which has its
own alignment), so nothing is reported twice; a section added or removed with all its
sub-clauses is one deviation. The definitions section is compared term by term and its
deviations name the term. A paired section that moved, or whose number changed beyond
what added, removed or moved siblings explain, is RENUMBERED. Signature blocks are skipped.
"""

import bisect
import difflib
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from tifia_review.models import (
    Alignment, ChangeType, Deviation, Section, SegmentedDocument,
)
from tifia_review.segmenter import SIGNATURES_ID, definition_spans, label_readings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------------
# all quotes and apostrophes -> one straight quote mark (the template writes 'A+' where
# agreements write "A+"); dash and hyphen variants -> "-"; soft hyphens, zero-width spaces
# and byte-order marks dropped
_QUOTES = (0x22, 0x27, 0x2018, 0x2019, 0x201A, 0x201B, 0x201C, 0x201D, 0x201E, 0x201F,
           0x2032, 0x2033)
_DASHES = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)
_INVISIBLE = (0x00AD, 0x200B, 0xFEFF)
_CHARS = str.maketrans({chr(c): '"' for c in _QUOTES} | {chr(c): "-" for c in _DASHES}
                       | {chr(c): None for c in _INVISIBLE})
# Page labels the loader did not recognize and notes about the page layout
_ARTIFACT_LINE = re.compile(
    r"(?im)^[ \t]*(?:sch\.[ \t]*[\w()]+|schedule[ \t]+[\w()]+-\d+"
    r"|\[?(?:the[ \t]+)?remainder of (?:this[ \t]+)?page (?:is[ \t]+)?intentionally"
    r"[ \t]+left[ \t]+blank\.?\]?|\[signature pages?[^\]\n]*\]|\[do not delete[^\]\n]*\])"
    r"[ \t]*$")


def normalize(text: str) -> str:
    """Comparable text: NFKC (ligatures, no-break spaces), one straight quote mark for all
    quotes and apostrophes, hyphens for dashes, no soft hyphens or private-use glyphs, one
    space between words. A hyphen at a line break is kept ("Semi-" + newline + "Annual"
    becomes "Semi-Annual"): in these documents it is always part of a compound word, never
    a word broken across lines."""
    text = unicodedata.normalize("NFKC", text).translate(_CHARS)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Co")
    text = re.sub(r"-[ \t]*\n\s*", "-", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------------
_ABBREVIATIONS = frozenset("""
    Sec. Secs. No. Nos. Inc. Co. Corp. Ltd. L.P. L.L.C. LLC. N.A. U.S. U.S.A. U.S.C.
    C.F.R. Pub. L. Stat. Fed. Reg. Cir. Art. i.e. e.g. etc. seq. al. v. vs. St. Ave.
    Dept. Mr. Mrs. Ms. Dr. Jr. Sr. a.m. p.m. S.E. N.E. S.W. N.W. D.C.
""".split())
_SENTENCE_END = re.compile(r"[.?!][\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])")
# "...shall be entered; (b) such Person ..." / "...as follows: (i) ..." / "...; and
# WHEREAS, ...": list items and recitals too
_CLAUSE_END = re.compile(r"[;:](?:\s+(?:and|or))?\s+"
                         r"(?=\((?:[a-z]{1,5}|[A-Z]|\d{1,2})\)\s|WHEREAS\b|NOW,? THEREFORE\b)")
_LEADING_LABEL = re.compile(
    r"\s*(?:\([A-Za-z0-9]{1,7}\)|(?:Section|SECTION)[ \t]+\d+\."
    r"|\[?(?:EXHIBIT|SCHEDULE|Exhibit|Schedule)[ \t]+[A-Za-z0-9()\-]+\]?)")


def split_sentences(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """(start, end) offsets of the sentences in text[start:end]: a sentence ends at ".",
    "?" or "!" before a capital letter or digit, unless the word is an abbreviation
    ("U.S.C.", "No.", "e.g."); a list item ends at ";" or ":" before "(b)", "(ii)"..."""
    end = len(text) if end is None else end
    cuts = []
    for m in _SENTENCE_END.finditer(text, start, end):
        word = re.search(r"\S*$", text[max(start, m.start() - 20):m.start() + 1]).group(0)
        if word.lstrip("(\"'“‘[") not in _ABBREVIATIONS:
            cuts.append(m)
    cuts += _CLAUSE_END.finditer(text, start, end)
    spans, pos = [], start
    for m in sorted(cuts, key=lambda m: m.start()):
        if m.start() < pos:
            continue
        spans.append((pos, m.start() + len(m.group(0).rstrip())))
        pos = m.end()
    spans.append((pos, end))
    return [(a + len(text[a:b]) - len(text[a:b].lstrip()), a + len(text[a:b].rstrip()))
            for a, b in spans if text[a:b].strip()]


# ---------------------------------------------------------------------------------
# Template placeholders
# ---------------------------------------------------------------------------------
_TOKEN = re.compile(r"_+|[A-Za-z0-9]+(?:['.\-][A-Za-z0-9]+)*|\S")
_MINOR_WORDS = frozenset("a an and as at by for from in into of on or the to with".split())
_FILL_MIN_WORDS = 15   # a blank may take at least this many words ...
_FILL_FACTOR = 3       # ... or this many times its own length


def _is_blank(content: str) -> bool:
    """Whether bracketed content invites any text: a blank, an instruction, a name in
    capitals or a short Title Case description ("[Traffic Consultant]"). Otherwise it is
    optional text, kept or dropped as written ("[or Subordinated Obligations]")."""
    inner = " ".join(content.strip("[]").split())
    if not inner.strip("_.●- ") or "_" in inner or "●" in inner:
        return True
    if inner.lower() == "reserved":
        return False
    if re.match(r"(?i)(insert|describe|specify|identify|include|to be|name of|date of)\b",
                inner):
        return True
    letters = re.sub(r"[^A-Za-z]", "", inner)
    if len(letters) > 1 and letters.isupper():
        return True
    words = re.findall(r"[A-Za-z][\w'-]*", inner)
    return (0 < len(words) <= 5 and words[0][0].isupper()
            and all(w[0].isupper() or w in _MINOR_WORDS for w in words))


def placeholder_spans(text: str) -> list[tuple[int, int, bool]]:
    """(start, end, is_blank) of each placeholder in one sentence, nested ones included:
    [bracketed] text, a bracket left open or closed by a neighboring sentence, and runs of
    three or more underscores. is_blank is judged on the span's own text, so optional text
    holding a blank ("[The TIFIA Lender has designated [____] as Servicer.]") is optional."""
    spans, opened = [], []
    for i, ch in enumerate(text):
        if ch == "[":
            opened.append(i)
        elif ch == "]":
            # an unmatched "]" closes a bracket opened in an earlier sentence
            spans.append((opened.pop() if opened else 0, i + 1))
    spans += [(a, len(text)) for a in opened]
    for m in re.finditer(r"_{3,}", text):
        if not any(a <= m.start() < b for a, b in spans):
            spans.append((m.start(), m.end()))
    out = []
    for a, b in sorted(spans):
        own = text[a:b]
        for c, d in sorted(spans, reverse=True):  # blank out nested spans
            if a < c and d <= b and (c, d) != (a, b):
                own = own[:c - a] + " " * (d - c) + own[d - a:]
        out.append((a, b, _is_blank(own)))
    return out


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def is_fill_in(template: str, executed: str) -> bool:
    """Whether two normalized sentences differ only inside the template's placeholders:
    blanks hold any text up to a length limit; optional text is kept or dropped whole."""
    holders = placeholder_spans(template)
    if not holders:
        return False
    t_tokens, e_tokens = _tokens(template), _tokens(executed)
    # each token belongs to the innermost placeholder around it
    owner = [min((n for n, (a, b, _) in enumerate(holders) if a <= s < b),
                 key=lambda n: holders[n][1] - holders[n][0], default=None)
             for _, s, _ in t_tokens]
    filled: dict[int, int] = defaultdict(int)   # blank -> words written into it
    dropped: dict[int, int] = defaultdict(int)  # optional text -> its words left out
    matcher = difflib.SequenceMatcher(None, [t for t, _, _ in t_tokens],
                                      [t for t, _, _ in e_tokens], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        touched = set(owner[i1:i2])
        if None in touched:
            return False  # a change outside the placeholders
        if not touched:  # text inserted next to a placeholder
            touched = {owner[k] for k in (i1 - 1, i1) if 0 <= k < len(owner)} - {None}
            touched = {h for h in touched if holders[h][2]}
            if not touched:
                return False
        if j2 > j1:
            if not all(holders[h][2] for h in touched):
                return False  # optional text replaced by other text
            filled[min(touched)] += j2 - j1
        for k in range(i1, i2):
            if not holders[owner[k]][2] and t_tokens[k][0] not in "[]":
                dropped[owner[k]] += 1
    for h, words in dropped.items():
        own = sum(1 for k, o in enumerate(owner) if o == h and t_tokens[k][0] not in "[]")
        if words != own:
            return False  # optional text partly dropped
    for h, words in filled.items():
        size = len(_tokens(template[holders[h][0]:holders[h][1]]))
        if words > max(_FILL_MIN_WORDS, _FILL_FACTOR * size):
            return False
    return True


def is_all_placeholder(template: str, heading: str = "", blanks_only: bool = False) -> bool:
    """Whether a normalized template text is only placeholders, apart from its caption
    words (a schedule that reads "PROJECT BUDGET [To be provided by Borrower]"). With
    blanks_only, optional text does not count: it is kept or dropped, not filled."""
    holders = [h for h in placeholder_spans(template) if h[2] or not blanks_only]
    caption = {w.lower() for w in re.findall(r"[A-Za-z]+", heading)}
    rest = [t for t, s, _ in _tokens(template)
            if not any(a <= s < b for a, b, _ in holders) and re.match(r"[A-Za-z0-9]", t)]
    return bool(holders) and all(t.lower() in caption for t in rest)


# ---------------------------------------------------------------------------------
# Comparing two texts
# ---------------------------------------------------------------------------------
_PAIR_MIN = 0.35   # word similarity for two changed sentences to be compared one to one
_PAIR_MAX_CELLS = 2500  # larger changed blocks are reported whole


@dataclass
class _Sentence:
    start: int   # offset in the section text
    end: int
    raw: str     # whitespace collapsed, for the report
    key: str     # normalized, for comparison


@dataclass
class _Side:
    """Part of one section's text to compare: text[start:end]."""
    section: Section
    start: int
    end: int

    def sentences(self) -> list[_Sentence]:
        text = _ARTIFACT_LINE.sub(lambda m: " " * len(m.group(0)), self.section.text)
        out = []
        for a, b in split_sentences(text, self.start, self.end):
            # one space between tokens, so "§ 1001" and "§1001" compare equal
            key = " ".join(t for t, _, _ in _tokens(normalize(text[a:b])))
            if key:
                out.append(_Sentence(a, b, " ".join(self.section.text[a:b].split()), key))
        return out

    def page(self, offset: int) -> int:
        return self.section.pdf_start + bisect.bisect_right(self.section.page_breaks, offset)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, [t for t, _, _ in _tokens(a)],
                                   [t for t, _, _ in _tokens(b)], autojunk=False).ratio()


def _pair_up(t: list[_Sentence], e: list[_Sentence]) -> list[tuple[int | None, int | None]]:
    """Order-keeping one-to-one pairing of changed sentences (most similar overall);
    unpaired ones come out as (i, None) or (None, j)."""
    if len(t) * len(e) > _PAIR_MAX_CELLS:
        return [(None, j) for j in range(len(e))] + [(i, None) for i in range(len(t))]
    score = [[1.0 if is_fill_in(a.key, b.key) else _similarity(a.key, b.key) for b in e]
             for a in t]
    best = [[0.0] * (len(e) + 1) for _ in range(len(t) + 1)]
    for i in range(1, len(t) + 1):
        for j in range(1, len(e) + 1):
            s = score[i - 1][j - 1]
            best[i][j] = max(best[i - 1][j], best[i][j - 1],
                             best[i - 1][j - 1] + s if s >= _PAIR_MIN else 0.0)
    out, i, j = [], len(t), len(e)
    while i or j:
        if i and j and score[i - 1][j - 1] >= _PAIR_MIN \
                and best[i][j] == best[i - 1][j - 1] + score[i - 1][j - 1]:
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and (not j or best[i][j] == best[i - 1][j]):
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]


def compare(t_side: _Side | None, e_side: _Side | None,
            term: str | None = None) -> list[Deviation]:
    """Deviations between two section texts (either may be missing), ids unset."""
    t_sents = t_side.sentences() if t_side else []
    e_sents = e_side.sentences() if e_side else []
    t_keys, e_keys = [s.key for s in t_sents], [s.key for s in e_sents]
    if t_keys == e_keys:
        return []
    # a template text that is only a blank ("[To be provided by Borrower]"): whatever the
    # agreement put there is filled in
    if t_side and e_side and e_sents and is_all_placeholder(
            " ".join(t_keys), t_side.section.heading, blanks_only=True):
        return [_deviation(ChangeType.FILL_IN, t_side, t_sents, e_side, e_sents, term)]
    # items: (kind, template sentence, executed sentence, offsets cited on each side);
    # an addition is cited on the template side right after the sentence before it
    items: list[tuple[str, int | None, int | None, int | None, int | None]] = []
    t_at = t_side.start if t_side else None
    e_at = e_side.start if e_side else None
    matcher = difflib.SequenceMatcher(None, t_keys, e_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            items.append(("equal", None, None, None, None))
            t_at, e_at = t_sents[i2 - 1].end, e_sents[j2 - 1].end
            continue
        for i, j in _pair_up(t_sents[i1:i2], e_sents[j1:j2]):
            ti = None if i is None else i1 + i
            ej = None if j is None else j1 + j
            if ti is not None and ej is not None:
                kind = "fill" if is_fill_in(t_sents[ti].key, e_sents[ej].key) else "change"
            elif ti is not None:
                kind = "fill" if is_all_placeholder(t_sents[ti].key) else "remove"
            else:
                kind = "add"
            t_cite = t_sents[ti].start if ti is not None else t_at
            e_cite = e_sents[ej].start if ej is not None else e_at
            items.append((kind, ti, ej, t_cite, e_cite))
            t_at = t_sents[ti].end if ti is not None else t_at
            e_at = e_sents[ej].end if ej is not None else e_at
    deviations, run = [], []
    for item in items + [("equal", None, None, None, None)]:
        if run and (item[0] == "equal" or (item[0] == "fill") != (run[0][0] == "fill")):
            deviations.append(_run_deviation(run, t_side, t_sents, e_side, e_sents, term))
            run = []
        if item[0] != "equal":
            run.append(item)
    return deviations


def _run_deviation(run, t_side, t_sents, e_side, e_sents, term) -> Deviation:
    kinds = {item[0] for item in run}
    change = (ChangeType.FILL_IN if kinds == {"fill"} else ChangeType.ADDED if kinds == {"add"}
              else ChangeType.REMOVED if kinds == {"remove"} else ChangeType.MODIFIED)
    t_run = [t_sents[item[1]] for item in run if item[1] is not None]
    e_run = [e_sents[item[2]] for item in run if item[2] is not None]
    return _deviation(change, t_side, t_run, e_side, e_run, term, run[0][3], run[0][4])


def _deviation(change, t_side, t_sents, e_side, e_sents, term,
               t_cite=None, e_cite=None) -> Deviation:
    t_at = t_sents[0].start if t_sents else t_cite
    e_at = e_sents[0].start if e_sents else e_cite
    return Deviation(
        deviation_id="", change_type=change,
        template_section_id=t_side.section.section_id if t_side else None,
        executed_section_id=e_side.section.section_id if e_side else None,
        template_text=" ".join(s.raw for s in t_sents),
        executed_text=" ".join(s.raw for s in e_sents),
        template_page=t_side.page(t_at) if t_side and t_at is not None else None,
        executed_page=e_side.page(e_at) if e_side and e_at is not None else None,
        affects_defined_term=term,
    )


# ---------------------------------------------------------------------------------
# Sections, definitions and numbering
# ---------------------------------------------------------------------------------
class _Doc:
    def __init__(self, doc: SegmentedDocument):
        self.by_id = {s.section_id: s for s in doc.sections}
        self.children: dict[str | None, list[str]] = defaultdict(list)
        for s in doc.sections:
            self.children[s.parent_id].append(s.section_id)
        self.definitions = next((t.section_id for t in doc.defined_terms.values()), None)

    def content_start(self, section: Section) -> int:
        """Offset after the section's own number or label (and stray page labels)."""
        text = _ARTIFACT_LINE.sub(lambda m: " " * len(m.group(0)), section.text)
        m = _LEADING_LABEL.match(text)
        return m.end() if m else 0

    def own_end(self, section: Section) -> int:
        """Offset of the first sub-clause, or the end of the text."""
        kids = self.children[section.section_id]
        cut = section.text.find(self.by_id[kids[0]].text) if kids else -1
        return cut if cut > 0 else len(section.text)

    def own(self, section: Section) -> _Side:
        return _Side(section, self.content_start(section), self.own_end(section))

    def descendants(self, section_id: str) -> list[str]:
        out, stack = [], list(self.children[section_id])
        while stack:
            out.append(stack.pop())
            stack.extend(self.children[out[-1]])
        return out

    def label(self, section: Section) -> str:
        return section.section_id[len(section.parent_id):] if section.parent_id \
            else section.section_id

    def ordinal(self, section: Section) -> int | None:
        """The number a label stands for: 5 for "Section 5", 11 for "(k)", 12 for "(xii)"
        in a roman list; None for labels such as "Exhibit H-1" or "preamble"."""
        label = self.label(section)
        if label.isdigit():
            return int(label)
        m = re.fullmatch(r"\((\w+)\)", label)
        if not m:
            return None
        return dict(label_readings(m.group(1))).get(self._style(section.parent_id))

    def _style(self, parent_id: str | None) -> str | None:
        """The list style of a parent's clauses ("alpha", "roman", ...), from the labels
        that can be read only one way ("(ii)" is roman, "(b)" a letter)."""
        votes: dict[str, int] = defaultdict(int)
        for kid in self.children[parent_id]:
            m = re.fullmatch(r"\((\w+)\)", self.label(self.by_id[kid]))
            readings = label_readings(m.group(1)) if m else []
            if len(readings) == 1:
                votes[readings[0][0]] += 1
        return max(votes, key=votes.get) if votes else None


def _printed_heading(section: Section) -> str:
    """Number and caption as a heading reads: "Section 5. Term.", "13(dd) Patriot Act."."""
    sid = section.section_id
    name = f"Section {sid}." if sid[:1].isdigit() and "(" not in sid else sid
    caption = section.heading.rstrip(". ")
    return f"{name} {caption}." if caption else name


class DiffEngine:
    def diff(self, template: SegmentedDocument, executed: SegmentedDocument,
             alignments: list[Alignment]) -> list[Deviation]:
        """Deviations in alignment order (definition changes at the definitions section,
        alphabetical by term), numbered d0001, d0002, ..."""
        t_doc, e_doc = _Doc(template), _Doc(executed)
        t_unmatched = {a.template_section.section_id for a in alignments
                       if a.template_section and not a.executed_section}
        e_unmatched = {a.executed_section.section_id for a in alignments
                       if a.executed_section and not a.template_section}
        renumbered = _renumbered(alignments, t_doc, e_doc)
        out: list[Deviation] = []
        done: set[str] = set()  # sub-clauses already reported with their parent
        for a in alignments:
            t, e = a.template_section, a.executed_section
            if (t and t.section_id == SIGNATURES_ID) or (e and e.section_id == SIGNATURES_ID):
                continue
            if t and e:
                if (t.section_id, e.section_id) in renumbered:
                    out.append(Deviation(
                        "", ChangeType.RENUMBERED, t.section_id, e.section_id,
                        _printed_heading(t), _printed_heading(e), t.pdf_start, e.pdf_start))
                if t.section_id == t_doc.definitions and e.section_id == e_doc.definitions:
                    out += _compare_definitions(t, e, t_doc, e_doc)
                else:
                    out += compare(t_doc.own(t), e_doc.own(e))
                continue
            section, doc, unmatched, key = ((t, t_doc, t_unmatched, "t:") if t
                                            else (e, e_doc, e_unmatched, "e:"))
            if key + section.section_id in done:
                continue
            kids = doc.descendants(section.section_id)
            if all(k in unmatched for k in kids):  # the whole sub-tree goes as one
                side = _Side(section, doc.content_start(section), len(section.text))
                done.update(key + k for k in kids)
            else:
                side = doc.own(section)
            out += compare(side, None) if t else compare(None, side)
        for n, deviation in enumerate(out, start=1):
            deviation.deviation_id = f"d{n:04d}"
        counts = defaultdict(int)
        for d in out:
            counts[d.change_type.value] += 1
        log.info("%s vs %s: %d deviations %s", executed.doc_id, template.doc_id, len(out),
                 dict(counts))
        return out


def _compare_definitions(t: Section, e: Section, t_doc: _Doc, e_doc: _Doc) -> list[Deviation]:
    """The text before the first term, then term by term, alphabetically. A term defined
    on one side only is cited on the other where it would sit alphabetically."""
    t_spans = {term: (a, b) for term, a, _, b in definition_spans(t)}
    e_spans = {term: (a, b) for term, a, _, b in definition_spans(e)}
    t_first = min((a for a, _ in t_spans.values()), default=len(t.text))
    e_first = min((a for a, _ in e_spans.values()), default=len(e.text))
    out = compare(_Side(t, t_doc.content_start(t), t_first),
                  _Side(e, e_doc.content_start(e), e_first))

    def side(section: Section, spans: dict[str, tuple[int, int]], term: str) -> _Side:
        if term in spans:
            return _Side(section, *spans[term])
        at = min((a for name, (a, _) in spans.items() if name.lower() > term.lower()),
                 default=max((b for _, b in spans.values()), default=len(section.text)))
        return _Side(section, at, at)

    for term in sorted(set(t_spans) | set(e_spans), key=str.lower):
        out += compare(side(t, t_spans, term), side(e, e_spans, term), term)
    return out


def _renumbered(alignments: list[Alignment], t_doc: _Doc,
                e_doc: _Doc) -> set[tuple[str, str]]:
    """Pairs that moved to another parent or level, moved within their list, or whose
    number changed beyond the shift caused by siblings added, removed or moved ahead of
    them (13(l) becoming 13(k) after 13(k) was dropped is not reported)."""
    pairs = {a.template_section.section_id: a.executed_section.section_id
             for a in alignments if a.template_section and a.executed_section}
    out = set()
    for t_parent in [None, *pairs]:
        e_parent = pairs[t_parent] if t_parent else None
        t_sibs, e_sibs = t_doc.children[t_parent], e_doc.children[e_parent]
        e_index = {e: n for n, e in enumerate(e_sibs)}
        inside = [t for t in t_sibs if pairs.get(t) in e_index]
        # the longest run that kept its order stays; the rest moved within the list
        kept = {inside[n] for n in _increasing([e_index[pairs[t]] for t in inside])}
        kept_partners = {pairs[t] for t in kept}
        for t_id in t_sibs:
            e_id = pairs.get(t_id)
            if e_id is None:
                continue
            if e_id not in e_index:  # moved to another list or level
                out.add((t_id, e_id))
                continue
            t, e = t_doc.by_id[t_id], e_doc.by_id[e_id]
            if t_doc.label(t) == e_doc.label(e):
                continue
            if t_id not in kept:  # out of order
                out.add((t_id, e_id))
                continue
            i, j = t_sibs.index(t_id), e_sibs.index(e_id)
            removed = sum(1 for s in t_sibs[:i] if s not in kept)
            added = sum(1 for s in e_sibs[:j] if s not in kept_partners)
            # compare numbers where the label has one (two sections can swap numbers
            # without moving), positions otherwise ("Exhibit H-1")
            t_number, e_number = t_doc.ordinal(t), e_doc.ordinal(e)
            if t_number is None or e_number is None:
                t_number, e_number = i, j
            if e_number != t_number - removed + added:
                out.add((t_id, e_id))
    # clauses whose template parent found no partner, paired somewhere else
    for t_id, e_id in pairs.items():
        parent = t_doc.by_id[t_id].parent_id
        if parent is not None and parent not in pairs:
            out.add((t_id, e_id))
    return out


def _increasing(seq: list[int]) -> set[int]:
    """Indexes of a longest strictly increasing subsequence of seq."""
    tails, tail_at, prev = [], [], [-1] * len(seq)
    for i, x in enumerate(seq):
        k = bisect.bisect_left(tails, x)
        if k == len(tails):
            tails.append(x)
            tail_at.append(i)
        else:
            tails[k], tail_at[k] = x, i
        prev[i] = tail_at[k - 1] if k else -1
    out, i = set(), tail_at[-1] if tail_at else -1
    while i >= 0:
        out.add(i)
        i = prev[i]
    return out
