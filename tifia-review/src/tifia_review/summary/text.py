"""Text helpers for the summary: the excerpts sent to the LLM (with where each one is in
the agreement), page citations, and the checks every LLM-written line must pass."""

import re
from bisect import bisect_right
from dataclasses import dataclass

from tifia_review.diff_engine import normalize
from tifia_review.models import Alignment, Document, Section, SegmentedDocument

QUOTE_WORDS = (2, 40)  # the prompt asks for at most 25
_QUOTE_MARKS = "\"'“”‘’"
# a printed page label worth citing: "36", "ii", "G-1"; not running text like "Exhibit J-5"
_PAGE_LABEL = re.compile(r"^(\d+|[ivxlcdm]+|[A-Z]{1,2}-\d+)$", re.I)
_BLANK = re.compile(r"\[[^\]]*\]")
_NOT_INSERTED = re.compile(r"to be (inserted|attached|provided|completed)|\[\s*attached\s*\.?\s*\]",
                           re.I)
_ADVICE = re.compile(r"\b(should|recommend\w*|advis\w*|suggest\w*|we urge|ought to|"
                     r"you may want|heavily|favorabl\w*|unfavorabl\w*|risky|riskier|"
                     r"concerning|onerous|generous|aggressive|excerpts?)\b", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class Piece:
    """A passage of the agreement sent to the LLM."""
    label: str           # "§6 Interest Rate", "§1 “Project”", "Recitals"
    cite: str            # "§6": what a citation and a fallback name
    text: str
    section: Section     # the agreement section the text is in, for pages
    offset: int = 0      # where text starts in section.text


class Pages:
    """Page citations in one document: the printed label where it is a page number,
    else the PDF page."""

    def __init__(self, doc: Document | None):
        self.labels = {p.pdf_index: p.printed_label for p in doc.pages} if doc else {}

    def cite(self, pdf_page: int) -> str:
        label = self.labels.get(pdf_page)
        if label and _PAGE_LABEL.match(label.strip()):
            return f"p. {label.strip()}"
        return f"PDF p. {pdf_page}"


def page_at(section: Section, offset: int) -> int:
    """The PDF page of a character offset in section.text."""
    return section.pdf_start + bisect_right(section.page_breaks, offset)


def strip_quotes(quote: str) -> str:
    return quote.strip().strip(_QUOTE_MARKS).strip()


def find_quote(quote: str, pieces: list[Piece]) -> tuple[Piece, int] | None:
    """The piece holding quote word for word (after normalize, ignoring case) and the
    quote's offset in its section, or None."""
    key = normalize(strip_quotes(quote)).casefold()
    if not key:
        return None
    for piece in pieces:
        if key in normalize(piece.text).casefold():
            words = re.findall(r"\w+", key)[:8]
            pattern = r"\W+".join(map(re.escape, words))
            m = re.search(pattern, piece.text, re.I | re.S) if words else None
            return piece, piece.offset + (m.start() if m else 0)
    return None


def quote_problem(quote: object, pieces: list[Piece]) -> str | None:
    """What is wrong with a quote, or None."""
    if not isinstance(quote, str):
        return "a quote is not text"
    n = len(normalize(strip_quotes(quote)).split())
    if not QUOTE_WORDS[0] <= n <= QUOTE_WORDS[1]:
        return f"each quote must be {QUOTE_WORDS[0]} to 25 words, not {n}"
    if find_quote(quote, pieces) is None:
        return f"quote not found word for word in the excerpt: {quote[:80]!r}"
    return None


def numbers_problem(text: str, source: str) -> str | None:
    """Every number in text must appear in source, so no value is invented."""
    digits = {n.replace(",", "") for n in _NUMBER.findall(normalize(source))}
    for n in _NUMBER.findall(text):
        bare = n.replace(",", "").rstrip(".")
        if bare and bare not in digits:
            return f"the number {n} is not in the text given"
    return None


def advice_problem(text: str, source: str = "") -> str | None:
    """Advice, judgment or talk about the excerpts; a word the agreement itself uses
    ("notices ... should be made by email") is describing it, not advising."""
    used = {w.casefold() for w in re.findall(r"\w+", source)}
    for m in _ADVICE.finditer(text):
        if m.group(0).casefold() not in used:
            return (f"describe the agreement; do not advise, judge or mention the excerpts "
                    f"(found {m.group(0)!r})")
    return None


def has_blank(text: str) -> bool:
    """A bracketed draft blank or option: [____], $[310,194,800], [Attached.]."""
    return bool(_BLANK.search(text))


def not_inserted(text: str) -> bool:
    return bool(_NOT_INSERTED.search(text))


def section_cite(section_id: str) -> str:
    """'§6', '§12(a)', 'Exhibit G', 'Recitals'."""
    if section_id == "preamble":
        return "Recitals"
    return f"§{section_id}" if section_id[:1].isdigit() else section_id


def aligned_sections(alignments: list[Alignment]) -> dict[str, Section]:
    """Template section id -> the agreement section paired with it."""
    return {a.template_section.section_id: a.executed_section for a in alignments
            if a.template_section is not None and a.executed_section is not None}


def executed_section(template_id: str, alignments: dict[str, Section],
                     executed: SegmentedDocument) -> Section | None:
    """The agreement section for a template section: the aligned one, else the one with
    the same number."""
    if template_id in alignments:
        return alignments[template_id]
    return next((s for s in executed.sections if s.section_id == template_id), None)


def definition_piece(term: str, executed: SegmentedDocument) -> Piece | None:
    """A defined term's definition, located in the definitions section."""
    found = next((t for name, t in executed.defined_terms.items()
                  if name.casefold() == term.casefold()), None)
    if found is None:
        return None
    section = next((s for s in executed.sections if s.section_id == found.section_id), None)
    if section is None:
        return None
    offset = max(section.text.find(f"“{found.term}”"), section.text.find(f'"{found.term}"'),
                 0)
    text = f"“{found.term}” {found.definition}"
    return Piece(f"§{found.section_id} definition of “{found.term}”",
                 section_cite(found.section_id), text, section, offset)
