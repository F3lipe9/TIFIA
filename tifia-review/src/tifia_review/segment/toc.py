"""TocParser: find the table-of-contents pages and parse their entries.

A numbered entry ends in dot leaders and a page number; its title may wrap onto the next
line(s), which are joined until one ends in leaders:

    Section 7.    Outstanding TIFIA Loan Balance; Revisions to Exhibit G and Loan
                  Amortization Schedule.................................................34

Schedules and exhibits follow, one per line ("EXHIBIT H-1 – Opinions Required ..."),
also possibly wrapped.
"""

import math
import re
from dataclasses import dataclass

from tifia_review.models import TocEntry

TOC_FRONT_PAGES = 10  # the TOC is always searched for in at least this many front pages
_TOC_TITLE = re.compile(r"^[ \t]*table of contents[ \t]*$", re.IGNORECASE | re.MULTILINE)
_DOT_LEADER = re.compile(r"\.{4,}[ \t]*\d{1,3}[ \t]*$", re.MULTILINE)
# "Section 7. \nOutstanding TIFIA Loan Balance; ... Loan \nAmortization Schedule.....34"
_TOC_SECTION = re.compile(
    r"^[ \t]*(?:Section|SECTION)[ \t]+(\d{1,3})\."
    r"((?:(?!\n[ \t]*(?:Section|SECTION)[ \t])[\s\S])*?)"
    r"\.{4,}[ \t]*(\d{1,3})[ \t]*$",
    re.MULTILINE,
)
# "EXHIBIT H-1 – Opinions Required from Counsel ...", "[SCHEDULE III – Existing ...]"
_TOC_ATTACHMENT = re.compile(r"[ \t]*(\[)?[ \t]*(SCHEDULE|EXHIBIT)[ \t]+(\S+)[ \t]+[–—-][ \t]+(.*)")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _is_toc_page(text: str, continuing: bool) -> bool:
    if _TOC_TITLE.search(text) or len(_DOT_LEADER.findall(text)) >= 3:
        return True
    # a later TOC page may hold only the schedule and exhibit list
    lines = [line for line in text.splitlines() if line.strip()]
    entries = sum(1 for line in lines if _DOT_LEADER.search(line) or _TOC_ATTACHMENT.match(line))
    return continuing and bool(lines) and 2 * entries >= len(lines)


def find_toc_pages(texts: list[str], front_pages: int = TOC_FRONT_PAGES) -> range:
    """Indexes of the table-of-contents pages; an empty range if there is no TOC."""
    for i, text in enumerate(texts[:front_pages]):
        if _is_toc_page(text, continuing=False):
            end = i + 1
            while end < len(texts) and _is_toc_page(texts[end], continuing=True):
                end += 1
            return range(i, end)
    return range(0)


def parse_toc(texts: list[str], first_pdf_index: int | None = None) -> list[TocEntry]:
    """Numbered sections (with page numbers), then schedules and exhibits. texts are the
    TOC pages in order; first_pdf_index (the first one's Page.pdf_index) sets toc_page."""
    toc = "\n".join(texts)
    starts, pos = [], 0
    for text in texts:
        starts.append(pos)
        pos += len(text) + 1

    def page_of(offset: int) -> int | None:
        if first_pdf_index is None:
            return None
        return first_pdf_index + max(i for i, s in enumerate(starts) if s <= offset)

    entries = [TocEntry(m.group(1), _squash(m.group(2)).rstrip(". "), m.group(3),
                        page_of(m.start()))
               for m in _TOC_SECTION.finditer(toc)]
    current, bracketed, offset = None, False, 0
    for line in toc.splitlines(keepends=True):
        line_start, offset = offset, offset + len(line)
        line = line.rstrip("\r\n")
        m = _TOC_ATTACHMENT.match(line)
        if m:
            bracketed = bool(m.group(1))
            current = TocEntry(f"{m.group(2).title()} {m.group(3)}", _squash(m.group(4)), None,
                               page_of(line_start))
            entries.append(current)
        elif not line.strip():
            current = None
        elif current:  # wrapped title
            current.title = _squash(f"{current.title} {line}")
        if current and bracketed and current.title.endswith("]"):
            current.title = current.title[:-1].rstrip()
    return entries


def is_numbered(entry: TocEntry) -> bool:
    return entry.section_id.isdigit()


@dataclass
class TocResult:
    entries: list[TocEntry]   # every entry, numbered sections first
    pages: range              # indexes (into the page list) of the TOC pages
    toc_end: int              # index of the first page after the TOC (0 if none)

    @property
    def numbered(self) -> list[TocEntry]:
        return [e for e in self.entries if is_numbered(e)]


class TocParser:
    def __init__(self, search_fraction: float = 0.10):
        self.search_fraction = search_fraction

    def front_pages(self, page_count: int) -> int:
        """The first ~search_fraction of the pages, never fewer than TOC_FRONT_PAGES."""
        return max(TOC_FRONT_PAGES, math.ceil(self.search_fraction * page_count))

    def parse(self, texts: list[str], pdf_indexes: list[int] | None = None) -> TocResult:
        pages = find_toc_pages(texts, self.front_pages(len(texts)))
        first = pdf_indexes[pages.start] if pdf_indexes and len(pages) else None
        entries = parse_toc([texts[i] for i in pages], first) if len(pages) else []
        return TocResult(entries, pages, pages.stop)
