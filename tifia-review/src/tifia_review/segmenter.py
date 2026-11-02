"""Segmenter: split a loaded Document into a tree of sections plus its defined terms.

Heading formats follow the TIFIA loan agreement (checked against the 2023 template and
the 2021 executed agreements):

    Section 4.  /  SECTION 4. Caption.     top level; the caption may be on the next line
    (a) -> (i) -> (A) -> (1)               nested clauses, usually in that order
    SCHEDULE I, EXHIBIT H-1, [EXHIBIT N]   attachments, at the top of a page

Pages are joined into one text (PageMap) so a section can cross page boundaries and
still be cited by PDF page and printed label. Each page's footnote area and the matching
reference marks ("Definitions.4") are cut first: template footnotes are drafting notes,
some contain "(c)" or "(iii)" lines that would otherwise read as clause headings, and the
marks would show up as differences against an agreement without them. The loader output
keeps the full text.
"""

import bisect
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

from tifia_review.models import (
    BoundarySource, DefinedTerm, Document, Page, Section, SegmentedDocument, TocEntry,
)
from tifia_review.segment.recovery import HeadingRecovery
from tifia_review.segment.structure import StructureCheck
from tifia_review.segment.toc import TocParser, find_toc_pages, is_numbered, parse_toc

__all__ = ["PageMap", "Segmenter", "TocEntry", "find_toc_pages", "parse_toc"]

log = logging.getLogger(__name__)

PREAMBLE_ID = "preamble"      # title, parties and recitals before Section 1
SIGNATURES_ID = "signatures"  # "IN WITNESS WHEREOF" block before the attachments

# ---------------------------------------------------------------------------------
# Footnotes
# ---------------------------------------------------------------------------------
# A footnote area starts at a footnote number ("67" alone, or "100  Revise, ...") after
# a blank line, or at the rule above a footnote continued from the previous page. It
# runs to the end of the page. Footnotes are numbered through the whole document, so
# the number must be the next one expected (or a few past it, in case one was missed);
# that rejects body lines such as "401 S. Jackson Street" in a notice address.
_FN_NUMBER = re.compile(r"[ \t]*(\d{1,3})(?:[ \t]*$|([ \t]+)(\S))")
_FN_RULE = re.compile(r"[ \t]*_{8,}[ \t]*$")
_FN_CONTINUED = re.compile(r"[ \t]*\(cont.d from previous page\)", re.IGNORECASE)
_FN_WINDOW = 5
# "2 CFR Part 170" heads Exhibit K of the 2021 agreements, where 2 is the next footnote
_CITATION = re.compile(r"[ \t]*\d{1,3}[ \t]+(?:CFR|C\.F\.R\.|U\.S\.C\.|USC|Stat\.)")


def _footnote_start(lines: list[str], i: int, expected: int) -> int | None:
    """Number of the footnote that starts at lines[i], or None."""
    m = _FN_NUMBER.match(lines[i])
    if not m or _CITATION.match(lines[i]):
        return None
    n = int(m.group(1))
    if i > 0 and not lines[i - 1].strip() and expected <= n <= expected + _FN_WINDOW:
        return n
    # Some pages (e.g. those re-typeset by tests/fixtures/make_fixtures.py) have no
    # blank line before the first footnote: accept only the exact next number, and
    # only when the note itself starts on the next line or after two spaces.
    if n != expected:
        return None
    if m.group(2) is None:
        after = lines[i + 1].lstrip() if i + 1 < len(lines) else ""
    else:
        after = m.group(3) if len(m.group(2)) >= 2 else ""
    return n if after[:1].isupper() or after[:1] in "“\"[" else None


def split_footnotes(text: str, expected: int = 1) -> tuple[str, str, int]:
    """(body, footnote area, next expected footnote number) for one page's text."""
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if _FN_RULE.match(line) and i + 1 < len(lines) and _FN_CONTINUED.match(lines[i + 1]):
            start = i
            break
        n = _footnote_start(lines, i, expected)
        if n is not None:
            start, expected = i, n
            break
    if start is None:
        return text, "", expected
    for line in lines[start:]:
        m = _FN_NUMBER.match(line)
        if m and int(m.group(1)) == expected:
            expected += 1
    cut = sum(len(line) + 1 for line in lines[:start])
    return text[:cut], text[cut:], expected


# A reference mark is the note's number set right after punctuation, a bracket, a quote
# or a word ("Definitions.4", "[at least one (1)]5", "Material Adverse Effect125"),
# sometimes two together ("]6970"). Only the numbers of notes cut from the same page are
# removed; never after a digit and "." or "," ("1.50", "$520,981,378"), nor after fewer
# than four letters (the rating codes "A3", "Baa1").
_REF_BEFORE = r"(?:(?<=[.,;:)\]%”’\"'])(?<![0-9][.,])|(?<=[A-Za-z]{4}))"
_REF_AFTER = r"(?=[\s.,;:)\]”’\"']|$)"


def footnote_numbers(notes: str, first: int, end: int) -> list[int]:
    """Numbers in range(first, end) that start a note in a page's footnote area."""
    found = {int(m.group(1)) for m in re.finditer(r"(?m)^[ \t]*(\d{1,3})(?=[ \t]|$)", notes)}
    return [n for n in range(first, end) if n in found]


def strip_footnote_refs(text: str, numbers: Iterable[int]) -> str:
    """Body text without the reference marks of the given footnotes."""
    marks = sorted({str(n) for n in numbers}, key=len, reverse=True)
    if not marks:
        return text
    return re.sub(f"{_REF_BEFORE}(?:{'|'.join(marks)})+{_REF_AFTER}", "", text)


# ---------------------------------------------------------------------------------
# Character-to-page map
# ---------------------------------------------------------------------------------
class PageMap:
    """One continuous text for a list of pages, mapping character offsets to pages."""

    def __init__(self, pages: list[Page], texts: list[str] | None = None):
        self.pages = pages
        texts = [p.text for p in pages] if texts is None else texts
        self.offsets, pos = [], 0
        for text in texts:
            self.offsets.append(pos)
            pos += len(text) + 1  # +1 for the joining newline
        self.full_text = "\n".join(texts)

    def index_at(self, char_pos: int) -> int:
        return bisect.bisect_right(self.offsets, char_pos) - 1

    def page_at(self, char_pos: int) -> Page:
        return self.pages[self.index_at(char_pos)]


# ---------------------------------------------------------------------------------
# Table of contents: segment/toc.py (find_toc_pages, parse_toc re-exported here)
# ---------------------------------------------------------------------------------
def _squash(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------------
_SECTION = re.compile(r"[ \t]*(?:Section|SECTION)[ \t]+(\d{1,3})\.(?=[ \t]|$)(?P<rest>.*)")
_LABEL = re.compile(r"[ \t]*\((?P<label>[a-z]{1,7}|[A-Z]{1,2}|\d{1,2})\)(?P<rest>.*)")
# Upper-case keyword: any id. Title-case keyword: no "-N" suffix, since "Exhibit A-4"
# is a printed page label.
_ATTACHMENT = re.compile(
    r"[ \t]*\[?[ \t]*(?:(?P<kind>EXHIBIT|SCHEDULE)[ \t]+(?P<id>[A-Z]{1,2}(?:-\d{1,2})?"
    r"|[IVX]{1,5}|\d{1,2}\([a-z]{1,2}\))|(?P<kind2>Exhibit|Schedule)[ \t]+"
    r"(?P<id2>[A-Z]{1,2}|[IVX]{1,5}|\d{1,2}\([a-z]{1,2}\)))[ \t]*\]?[ \t]*$"
)
_ATTACHMENT_TOP_LINES = 3  # an attachment heading is among a page's first lines
_SIGNATURES = re.compile(r"[ \t]*IN WITNESS WHEREOF")
# A line that ends a clause: ".", ";" or ":" (maybe followed by a bracket, a quote or
# footnote numbers), or "; and" / "; or". A label that has text after it on its line
# only starts a clause if the previous line ended one; otherwise it is an inline
# enumeration that wrapped to a line start ("..., and \n(iii) Debt Service ...").
_CLAUSE_END = re.compile(r"(?:[.;:][\]\)”\"’]*\d{0,6}|;[ \t]*(?:and|or))[ \t]*$")
_CAPTION = re.compile(r"(?P<caption>[^.:]{1,150}?)[.:]\d{0,6}(?=\s|$)")
_RESERVED = re.compile(r"\[?reserved\]?\.?$", re.IGNORECASE)
_MINOR_WORDS = frozenset(
    "a an and as at by for from in into of on or other than the to under upon with".split())
_CAPTION_MAX_WORDS = 15
_MAX_SKIP = 2  # clauses a label alone on its line may skip (e.g. a deleted "(c)")
_ROMAN = re.compile(r"(?=[ivxl])(?:xl|x{0,3})(?:ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50}


def caption_of(text: str) -> str:
    """The Title Case caption that starts text ("Interest Rate.  The ..."), or ""."""
    text = _squash(text)
    if _RESERVED.match(text):
        return "Reserved"
    m = _CAPTION.match(text)
    if not m:
        return ""
    caption = m.group("caption").strip().rstrip(",;").rstrip()
    if caption.startswith("[") and (caption.endswith("]") or "]" not in caption):
        caption = caption.strip("[]").strip()  # optional template text: "[Taxes.  The ..."
    words = re.findall(r"[A-Za-z][\w’'-]*", caption)
    if 0 < len(words) <= _CAPTION_MAX_WORDS and all(
            w[0].isupper() or w in _MINOR_WORDS for w in words):
        return caption
    return ""


def _roman_value(s: str) -> int:
    values = [_ROMAN_VALUES[c] for c in s]
    return sum(-v if v < nxt else v for v, nxt in zip(values, values[1:] + [0]))


def label_readings(label: str) -> list[tuple[str, int]]:
    """Possible (style, ordinal) readings: "c" -> alpha 3; "i" -> alpha 9 or roman 1."""
    if label.isdigit():
        return [("arabic", int(label))]
    readings = []
    if len(label) <= 2 and len(set(label)) == 1:  # a..z, aa..zz, A..Z, AA..ZZ
        style = "upper" if label.isupper() else "alpha"
        readings.append((style, 26 * (len(label) - 1) + ord(label[0].lower()) - 96))
    if label.islower() and _ROMAN.match(label):
        readings.append(("roman", _roman_value(label)))
    return readings


@dataclass
class _Candidate:
    """A line that may start a heading; _resolve decides which ones do."""
    pos: int             # offset in PageMap.full_text of the line's first character
    kind: str            # "section" | "attachment" | "signatures" | "clause"
    ident: str           # section number, attachment id or clause label
    caption: str = ""
    standalone: bool = False  # clause label alone on its line
    source: BoundarySource = BoundarySource.NUMBERED


@dataclass
class _Heading:
    pos: int
    level: int
    section_id: str
    heading: str
    parent_id: str | None
    source: BoundarySource = BoundarySource.NUMBERED


@dataclass
class _Level:  # an open clause level while resolving one section's clauses
    style: str
    ordinal: int
    section_id: str


def _caption_after(rest: str, following: list[str]) -> str:
    """Caption from the rest of a heading line plus the lines after it (up to 2)."""
    parts = [rest]
    for line in following[:2]:
        if not line.strip() or _LABEL.match(line) or _SECTION.match(line):
            break
        parts.append(line)
    return caption_of(" ".join(parts))


def _scan(pmap: PageMap, texts: list[str], first_page: int) -> list[_Candidate]:
    """Every line from first_page on that looks like a heading, in document order."""
    found, prev, prev_is_label = [], "", False
    for idx in range(first_page, len(texts)):
        lines = texts[idx].split("\n")
        pos, seen, page_top = pmap.offsets[idx], 0, 0
        for i, line in enumerate(lines):
            line_pos, pos = pos, pos + len(line) + 1
            if not line.strip():
                continue
            seen += 1
            start = line_pos + len(line) - len(line.lstrip())
            page_top = page_top or start
            following = lines[i + 1:i + 3]
            attachment = _ATTACHMENT.match(line) if seen <= _ATTACHMENT_TOP_LINES else None
            section = _SECTION.match(line)
            label = _LABEL.match(line)
            if attachment:
                # attachments start a new page; lines above the heading (an unrecognized
                # page label such as "Sch. I") belong to it, not to the previous section
                kind = (attachment.group("kind") or attachment.group("kind2")).title()
                ident = attachment.group("id") or attachment.group("id2")
                title = next((ln.strip() for ln in following if ln.strip()), "")
                found.append(_Candidate(page_top, "attachment", f"{kind} {ident}", title))
            elif section:
                caption = _caption_after(section.group("rest"), following)
                if caption:
                    found.append(_Candidate(start, "section", section.group(1), caption))
            elif _SIGNATURES.match(line):
                found.append(_Candidate(start, "signatures", SIGNATURES_ID, "Signatures"))
            elif label:
                rest = label.group("rest")
                standalone = not rest.strip()
                if standalone or prev_is_label or _CLAUSE_END.search(prev):
                    found.append(_Candidate(start, "clause", label.group("label"),
                                            _caption_after(rest, following), standalone))
            prev, prev_is_label = line.strip(), bool(label) and not label.group("rest").strip()
    return found


def _place(label: str, standalone: bool, stack: list[_Level],
           roman_follows: bool) -> tuple[int, str, int] | None:
    """(depth, style, ordinal) for a clause label given the open levels, or None.

    Most reliable first: the next sibling of an open level (innermost first), then the
    first child of a style not open yet, then (for a label alone on its line) a sibling
    that skips a few ordinals. Anything else is a cross-reference or inline enumeration.
    """
    readings = label_readings(label)
    for depth in range(len(stack) - 1, -1, -1):
        for style, n in readings:
            if style == stack[depth].style and n == stack[depth].ordinal + 1:
                # "(h)" then "(i)": the letter, unless "(ii)" comes before "(j)"
                if label == "i" and roman_follows and depth == len(stack) - 1:
                    return len(stack), "roman", 1
                return depth, style, n
    open_styles = {level.style for level in stack}
    for style, n in readings:
        if n == 1 and style not in open_styles:
            return len(stack), style, n
    if standalone:
        for depth in range(len(stack) - 1, -1, -1):
            last = stack[depth].ordinal
            for style, n in readings:
                if style == stack[depth].style and last + 1 < n <= last + 1 + _MAX_SKIP:
                    return depth, style, n
    return None


def _roman_follows(found: list[_Candidate], start: _Candidate) -> bool:
    """Whether "(ii)" comes before "(j)" after start, within its section: then a "(i)"
    after "(h)" opens a roman list, even with "(A)" clauses in between."""
    for c in found[found.index(start) + 1:]:
        if c.kind != "clause":
            return False
        if c.ident in ("ii", "j"):
            return c.ident == "ii"
    return False


def _resolve(found: list[_Candidate], toc_titles: dict[str, str]) -> list[_Heading]:
    """Turn candidates into headings: levels, ids and parents.

    Numbered sections and clauses only count before the first attachment (exhibits
    have their own "Section 1."). Clauses are not split out of the definitions section,
    where "(a)", "(b)" belong to individual definitions, nor out of the preamble,
    signatures or attachments.
    """
    heads, used, stack = [], set(), []
    context, top_id = "front", None
    for c in found:
        if c.kind == "attachment" or (c.kind == "section" and context != "attachments"):
            if c.ident in used:
                log.debug("skipping repeated heading %s at %d", c.ident, c.pos)
                continue
            heading = toc_titles.get(c.ident, c.caption) if c.kind == "attachment" else c.caption
            heads.append(_Heading(c.pos, 1, c.ident, heading, None, c.source))
            used.add(c.ident)
            stack, top_id = [], c.ident
            if c.kind == "attachment":
                context = "attachments"
            else:
                context = "definitions" if c.caption.lower().startswith("definition") else "section"
        elif c.kind == "signatures" and context in ("section", "definitions"):
            heads.append(_Heading(c.pos, 1, SIGNATURES_ID, c.caption, None))
            context = "signatures"
        elif c.kind == "clause" and context == "section":
            placed = _place(c.ident, c.standalone, stack,
                            c.ident == "i" and _roman_follows(found, c))
            if placed is None:
                log.debug("not a clause heading: (%s) at %d", c.ident, c.pos)
                continue
            depth, style, n = placed
            parent = stack[depth - 1].section_id if depth else top_id
            section_id = f"{parent}({c.ident})"
            del stack[depth:]
            stack.append(_Level(style, n, section_id))
            heads.append(_Heading(c.pos, depth + 2, section_id, c.caption, parent))
    return heads


def _build_sections(heads: list[_Heading], pmap: PageMap) -> list[Section]:
    """Section text runs from its heading to the next heading at the same or higher level;
    page_breaks are the offsets in that text where each following page starts."""
    text, sections = pmap.full_text, []
    for i, h in enumerate(heads):
        end = next((o.pos for o in heads[i + 1:] if o.level <= h.level), len(text))
        body = text[h.pos:end].rstrip()
        first, last = pmap.index_at(h.pos), pmap.index_at(h.pos + max(len(body) - 1, 0))
        sections.append(Section(
            section_id=h.section_id, heading=h.heading, text=body,
            pdf_start=pmap.pages[first].pdf_index, pdf_end=pmap.pages[last].pdf_index,
            label_start=pmap.pages[first].printed_label,
            label_end=pmap.pages[last].printed_label,
            level=h.level, parent_id=h.parent_id,
            page_breaks=[pmap.offsets[k] - h.pos for k in range(first + 1, last + 1)],
            boundary_source=h.source,
        ))
    return sections


# ---------------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------------
# “Term” means ... / [“Term”8 has the meaning ... / “Term” or “Alias” means ... The
# closing quote is optional: one executed agreement has “NEPA Determination means.
_DEFINITION = re.compile(
    r"^[ \t]*\[?[“\"](?P<term>[^”\"\n]{1,120}?)[”\"]?\d{0,3}"
    r"(?:\s+or\s+[“\"][^”\"\n]{1,60}[”\"])?\s+"
    r"(?=(?:means?|ha(?:s|ve) the meanings?|shall (?:mean|have the meanings?|be deemed)"
    r"|(?:is|are) defined)\b)",
    re.MULTILINE,
)


def definition_spans(section: Section) -> list[tuple[str, int, int, int]]:
    """(term, start, meaning, end) for each definition, as offsets in section.text: the
    term's opening quote (or bracket), the word "means" or similar, and the next term."""
    matches = list(_DEFINITION.finditer(section.text))
    return [(_squash(m.group("term")),
             m.start() + len(m.group(0)) - len(m.group(0).lstrip(" \t")),
             m.end(),
             nxt.start() if nxt else len(section.text))
            for m, nxt in zip(matches, matches[1:] + [None])]


def parse_definitions(section: Section) -> dict[str, DefinedTerm]:
    """Term -> definition text (from "means" up to the next term) for a definitions section."""
    terms = {}
    for term, _, meaning, end in definition_spans(section):
        if term in terms:
            log.warning("%s: term %r defined twice; keeping the first", section.section_id, term)
            continue
        terms[term] = DefinedTerm(term, section.text[meaning:end].strip(), section.section_id)
    return terms


def find_definitions_section(sections: list[Section]) -> Section | None:
    return next((s for s in sections
                 if s.level == 1 and s.heading.lower().startswith("definition")), None)


# ---------------------------------------------------------------------------------
# Self-check and entry point
# ---------------------------------------------------------------------------------
def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def toc_mismatches(sections: list[Section], toc: list[TocEntry]) -> list[str]:
    """Differences between the top-level sections found and the document's own TOC."""
    if not toc:
        return []
    found = [s for s in sections if s.level == 1 and s.section_id not in
             (PREAMBLE_ID, SIGNATURES_ID)]
    by_id = {s.section_id: s for s in found}
    problems = []
    toc_ids = [e.section_id for e in toc]
    missing = [i for i in toc_ids if i not in by_id]
    extra = [s.section_id for s in found if s.section_id not in toc_ids]
    if missing:
        problems.append(f"in TOC but not found: {missing}")
    if extra:
        problems.append(f"found but not in TOC: {extra}")
    if not missing and not extra and toc_ids != [s.section_id for s in found]:
        problems.append("sections are in a different order than the TOC")
    for e in toc:
        s = by_id.get(e.section_id)
        if s and e.page_label and _norm_title(s.heading) != _norm_title(e.title):
            problems.append(f"{e.section_id}: heading {s.heading!r} vs TOC {e.title!r}")
    return problems


DEFAULT_RECOVERY = {
    "enabled": True,
    "min_text_coverage": 0.98,
    "toc_search_fraction": 0.10,    # look for the TOC in the first 10% of pages (at least 10)
    "toc_min_entries_ratio": 0.80,  # a TOC with fewer entries than this x the template's
                                    # sections: recover with the template's titles instead
}


def reference_entries(seg: SegmentedDocument) -> list[TocEntry]:
    """A segmented template's numbered sections, the list recovery falls back on."""
    return [TocEntry(s.section_id, s.heading) for s in seg.sections
            if s.level == 1 and s.section_id.isdigit()]


def body_texts(doc: Document) -> tuple[list[str], int]:
    """Each page's text without its footnote area and reference marks, and how many
    pages had footnotes cut."""
    texts, expected, cut = [], 1, 0
    for page in doc.pages:
        first = expected
        body, notes, expected = split_footnotes(page.text, expected)
        if notes:
            body = strip_footnote_refs(body, footnote_numbers(notes, first, expected))
            cut += 1
        texts.append(body)
    return texts, cut


class Segmenter:
    def __init__(self, recovery: dict | None = None):
        """recovery: config.yaml segmentation.recovery (keys of DEFAULT_RECOVERY)."""
        self.recovery = {**DEFAULT_RECOVERY, **(recovery or {})}
        self.toc_parser = TocParser(self.recovery["toc_search_fraction"])
        self.check = StructureCheck(self.recovery["min_text_coverage"])

    def _expected(self, toc: list[TocEntry], reference: list[TocEntry] | None
                  ) -> tuple[list[TocEntry], str]:
        """The document's own numbered TOC entries, or the template's sections when there
        is no TOC or it lists fewer than toc_min_entries_ratio of them."""
        ref = [e for e in reference or [] if is_numbered(e)]
        if toc and (not ref or len(toc) >= self.recovery["toc_min_entries_ratio"] * len(ref)):
            return toc, "toc"
        if ref:
            return ref, "template"
        return [], "none"

    def segment(self, doc: Document, reference: list[TocEntry] | None = None
                ) -> SegmentedDocument:
        """Sections in document order (parents before children) and defined terms.

        Text before Section 1 (after any TOC) becomes the "preamble" section; the cover
        page and TOC pages belong to no section. When numbered sections listed in the TOC
        (or, failing a TOC, in reference: the template's sections) were not found, they
        are searched for by title (segment/recovery.py); the result's structure says how
        complete the sections are.
        """
        texts, cut = body_texts(doc)
        pmap = PageMap(doc.pages, texts)
        full = pmap.full_text

        raw = [p.text for p in doc.pages]
        parsed = self.toc_parser.parse(raw, [p.pdf_index for p in doc.pages])
        toc, body_start = parsed.entries, parsed.toc_end
        attachment_titles = {e.section_id: e.title for e in toc if e.page_label is None}
        body_pos = pmap.offsets[body_start] if body_start < len(texts) else len(full)

        candidates = _scan(pmap, texts, body_start)
        heads = _resolve(candidates, attachment_titles)
        wanted, expected_from = self._expected(parsed.numbered, reference)
        first_pass = {h.section_id: h.pos for h in heads
                      if h.level == 1 and h.section_id.isdigit()}
        recovered, warnings = [], []
        if self.recovery["enabled"] and any(e.section_id not in first_pass for e in wanted):
            # the body ends at the signatures or the first attachment
            body_end = next((h.pos for h in heads if h.level == 1 and h.pos >= body_pos
                             and not h.section_id.isdigit()), len(full))
            source = (BoundarySource.TOC_TITLE if expected_from == "toc"
                      else BoundarySource.TEMPLATE_TITLE)
            result = HeadingRecovery().recover(full, wanted, first_pass, body_pos, body_end,
                                               source, pmap.offsets)
            if result.boundaries:
                candidates = sorted(candidates + [
                    _Candidate(b.pos, "section", b.section_id, b.title, source=b.source)
                    for b in result.boundaries], key=lambda c: c.pos)
                heads = _resolve(candidates, attachment_titles)
                recovered = [b.section_id for b in result.boundaries]
            warnings = result.warnings

        first_pos = heads[0].pos if heads else len(full)
        if full[body_pos:first_pos].strip():
            heads.insert(0, _Heading(body_pos, 1, PREAMBLE_ID, "Preamble", None))
        sections = _build_sections(heads, pmap)
        structure = self.check.check(sections, wanted, expected_from, full[body_pos:],
                                     recovered, warnings)

        definitions = find_definitions_section(sections)
        terms = parse_definitions(definitions) if definitions else {}
        if definitions is None:
            log.warning("%s: no definitions section found", doc.doc_id)
        for problem in toc_mismatches(sections, toc):
            log.warning("%s: %s", doc.doc_id, problem)
        if structure.status != "complete":
            log.warning("%s", structure.summary_line(doc.doc_id))
            for problem in structure.problems:
                log.warning("%s: %s", doc.doc_id, problem)
        log.info("%s: %d sections (%d top level), %d defined terms; footnotes cut on %d pages",
                 doc.doc_id, len(sections), sum(s.level == 1 for s in sections), len(terms), cut)
        return SegmentedDocument(doc.doc_id, sections, terms, structure)
