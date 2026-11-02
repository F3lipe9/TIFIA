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


class BoundarySource(Enum):
    """How a top-level section's start was found (segment/recovery.py)."""
    NUMBERED = "numbered"              # its "Section N." heading, by the normal segmenter
    SECTION_NUMBER = "section_number"  # a "Section N." line whose caption the first pass
                                       # could not read ("Definitions.Unless ..."), by recovery
    TOC_TITLE = "toc_title"            # its title, from the document's own TOC, by recovery
    TEMPLATE_TITLE = "template_title"  # its title, from the template's sections, by recovery


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
    boundary_source: BoundarySource = BoundarySource.NUMBERED


@dataclass
class DefinedTerm:
    term: str
    definition: str
    section_id: str


@dataclass
class TocEntry:
    """One entry of a table of contents (or of the template's section list)."""
    section_id: str          # same form as Section.section_id: "7", "Exhibit H-1"
    title: str               # "TIFIA Loan Amount"
    page_label: str | None = None  # printed page number; listed for numbered sections only
    toc_page: int | None = None    # pdf_index of the TOC page it is on; None from the template


@dataclass
class StructureReport:
    """Whether the numbered sections found match the expected list (segment/structure.py)."""
    expected: int                    # numbered sections expected (TOC or template)
    found: int                       # of those, present after recovery
    recovered: list[str]             # section IDs added by recovery
    missing: list[str]               # expected but still not found
    text_coverage: float             # section chars / body chars (non-space)
    status: str                      # "complete" | "recovered" | "degraded"
    expected_from: str = "toc"       # "toc" | "template" | "none"
    numbered: int = 0                # found by the normal segmenter
    problems: list[str] = field(default_factory=list)  # order/duplicate/coverage failures
    warnings: list[str] = field(default_factory=list)  # e.g. a title found twice

    @property
    def degraded(self) -> bool:
        return self.status == "degraded"

    def summary_line(self, doc_id: str) -> str:
        """'doc: 39/39 sections (13 numbered, 26 recovered from TOC), coverage 99.9%'."""
        source = {"toc": "TOC", "template": "template titles"}.get(self.expected_from, "")
        parts = [f"{self.numbered} numbered"]
        if self.recovered:
            parts.append(f"{len(self.recovered)} recovered from {source}")
        if self.missing:
            parts.append(f"{len(self.missing)} missing: {', '.join(self.missing)}")
        line = (f"{doc_id}: {self.found}/{self.expected} sections ({', '.join(parts)}), "
                f"coverage {self.text_coverage:.1%}")
        if self.expected_from == "none":
            line = f"{doc_id}: {self.found} sections (no TOC or template list to check), "                    f"coverage {self.text_coverage:.1%}"
        return line + (" [DEGRADED]" if self.degraded else "")


@dataclass
class SegmentedDocument:
    doc_id: str
    sections: list[Section]
    defined_terms: dict[str, DefinedTerm]
    structure: StructureReport | None = None


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


# --- executive summary (summary/) ------------------------------------------------------

class ValueStatus(Enum):
    FOUND = "found"                # summarized, every quote verified
    DRAFT_BLANK = "draft_blank"    # the value is a bracketed blank in a draft
    NOT_INSERTED = "not_inserted"  # "[to be inserted]", "[Attached.]"
    NOT_FOUND = "not_found"        # not in the text sent, a failed check, or no LLM


@dataclass
class TopicRow:
    topic: str                      # config key, e.g. "interest_rate"
    label: str                      # "Interest rate"
    summary: str | None             # plain English; None unless FOUND/DRAFT_BLANK
    quotes: list[str]               # verified word for word in the agreement
    sources: list[str]              # e.g. ["§6, PDF p. 41"]
    status: ValueStatus
    substantive_changes: int        # deviations mapped to this topic (code, not LLM)
    change_ids: list[str]           # deviation IDs, most significant first, max 3
    other_changes: int = 0          # not substantive (incl. fill-ins and renumberings)
    needs_review: int = 0
    unclassified: int = 0           # a run without the LLM: changes with no label
    sections: list[str] = field(default_factory=list)  # "§6": where to look on a fallback
    problem: str = ""               # why a summary fell back to "See agreement"


@dataclass
class ScheduleDigest:
    status: ValueStatus
    pages: list[int]                # PDF pages of the debt service exhibit
    exhibit: str | None = None      # its letter as printed, e.g. "G" or "F"
    first_payment: str | None = None
    final_payment: str | None = None
    payment_count: int | None = None
    frequency: str | None = None
    total_principal: float | None = None
    largest_annual: tuple[str, float] | None = None  # (year, total paid that year)
    matches_loan_amount: bool | None = None          # None: no loan amount to check
    problem: str = ""               # why there is no digest
