"""Core dataclasses shared by every pipeline stage."""

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Page:
    pdf_index: int                 # 1-based position in the PDF file
    printed_label: str | None      # page number as printed, for citations
    text: str
    needs_review: bool = False


@dataclass
class Document:
    doc_id: str
    doc_type: str  # "template" | "executed"
    pages: list[Page]
    is_scanned: bool = False


@dataclass
class Section:
    section_id: str  # normalized, e.g. "4", "12(a)(xii)", "Exhibit D", "preamble"
    heading: str     # caption, e.g. "Disbursement Conditions"; "" if none
    text: str        # from the heading to the next heading at the same or higher level
    pdf_start: int   # Page.pdf_index of the first and last page
    pdf_end: int
    label_start: str | None = None  # printed page labels, for citations
    label_end: str | None = None
    level: int = 1   # 1 = Section/Schedule/Exhibit, 2 = (a), 3 = (i), 4 = (A), ...
    parent_id: str | None = None
    # offsets in text where the next PDF page starts: offset o is on PDF page
    # pdf_start + bisect_right(page_breaks, o)
    page_breaks: list[int] = field(default_factory=list)


@dataclass
class DefinedTerm:
    term: str
    definition: str
    section_id: str


@dataclass
class SegmentedDocument:
    doc_id: str
    sections: list[Section]
    defined_terms: dict[str, DefinedTerm]


class AlignMethod(Enum):
    NUMBER = "number"
    HEADING = "heading"
    EMBEDDING = "embedding"


@dataclass
class Alignment:
    template_section: Section | None  # None = section added in executed doc
    executed_section: Section | None  # None = section removed
    method: AlignMethod | None
    confidence: float


class ChangeType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    RENUMBERED = "renumbered"  # same section under another number, beyond list shifts
    FILL_IN = "fill_in"        # differs only inside template blanks or optional text:
                               # not a negotiated deviation


@dataclass
class Deviation:
    deviation_id: str
    change_type: ChangeType
    template_section_id: str | None
    executed_section_id: str | None
    template_text: str
    executed_text: str
    template_page: int | None
    executed_page: int | None
    affects_defined_term: str | None = None


@dataclass
class LabeledDeviation:
    deviation: Deviation
    category: str  # from taxonomy, "unclear", or "Fill-in" / "Renumbering" (no LLM)
    substantive: bool
    explanation: str
    needs_review: bool = False  # quote check failed or low confidence
    evidence: str = ""          # verbatim quote from the executed text (template text if
                                # the executed text is empty), checked by the classifier
    review_reason: str = ""     # why needs_review is set
