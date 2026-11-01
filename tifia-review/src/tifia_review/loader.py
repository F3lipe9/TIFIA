"""DocumentLoader: page-level text extraction with PyMuPDF."""

import logging
import re
from collections import Counter

import pymupdf

from tifia_review.models import Document, Page

log = logging.getLogger(__name__)

_ROMAN = r"(?=[ivxlc])c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_SCHEDULE_ID = r"(?:[IVX]{1,5}|\d{1,2}\([a-z]{1,2}\))"  # "III", "13(f)"
_PAGE_LABEL = re.compile(
    rf"^(?:\d{{1,3}}[a-z]?|{_ROMAN}|(?:Exhibit |Schedule )?[A-Z]{{1,2}}-\d{{1,3}}"
    rf"|Schedule {_SCHEDULE_ID}-\d{{1,3}}|Sch\. {_SCHEDULE_ID})$"
)
# PyMuPDF puts the running header/footer (e.g. "AMERICAS/2019115430.23", "2",
# "ST DRLE Loan Agreement") in the first lines of each page's text. A page with no label
# there may have it as its last line instead, unless the line before is also a number
# (the end of a table column); a footnote number never ends a page, its note follows it.
_HEADER_LINES = 3
_RUNNING_MIN_SHARE = 0.3  # a header-zone line on at least this share of pages is running
_NUMBER_LINE = re.compile(r"[\d,.$%()\s-]+")


def _shape(line: str) -> str:
    """Line with digits masked, so "AMERICAS/2021320676.6" and "...678.5" match."""
    return re.sub(r"\d", "#", line.strip())


def _header_zone(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()][:_HEADER_LINES]


def _footer_label(text: str) -> str | None:
    """The page's last line, if it reads as a page label (see _HEADER_LINES)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= _HEADER_LINES or not _PAGE_LABEL.match(lines[-1]):
        return None
    return None if _NUMBER_LINE.fullmatch(lines[-2]) else lines[-1]


def find_printed_label(text: str) -> str | None:
    """Printed page label from the first few lines of the page, or else its last line."""
    for line in _header_zone(text):
        if _PAGE_LABEL.match(line):
            return line
    return _footer_label(text)


def find_running_lines(texts: list[str]) -> set[str]:
    """Shapes of header-zone lines that repeat across many pages of one document."""
    counts = Counter(shape for t in texts for shape in {_shape(ln) for ln in _header_zone(t)})
    min_pages = max(3, int(len(texts) * _RUNNING_MIN_SHARE))
    return {shape for shape, n in counts.items()
            if n >= min_pages and not _PAGE_LABEL.match(shape.replace("#", "1"))}


def split_header(text: str, running: set[str]) -> tuple[str | None, str]:
    """(printed label, text without the label and running header lines).

    Only the first _HEADER_LINES non-empty lines are touched, and the last line when it
    is the label; the rest of the page text is returned unchanged.
    """
    label, kept, seen = None, [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and seen < _HEADER_LINES:
            seen += 1
            if label is None and _PAGE_LABEL.match(stripped):
                label = stripped
                continue
            if _shape(stripped) in running:
                continue
        kept.append(line)
    if label is None and (label := _footer_label(text)):
        last = max(n for n, line in enumerate(kept) if line.strip())
        del kept[last]
    return label, "".join(kept)


class DocumentLoader:
    def __init__(self, min_chars: int = 50, max_low_text_fraction: float = 0.25):
        self.min_chars = min_chars
        self.max_low_text_fraction = max_low_text_fraction

    def load(self, path: str, doc_id: str, doc_type: str) -> Document:
        """Extract text per page (PyMuPDF default order).

        The printed page number is stored in printed_label and removed from the text,
        along with running header/footer lines that repeat across pages. Pages under
        min_chars (after that removal) are marked needs_review. The document is marked
        is_scanned when more than max_low_text_fraction of its pages are low-text.
        """
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError(f"{doc_id}: PDF is password-protected")
            texts = [pdf_page.get_text() for pdf_page in pdf]

        if not texts:
            raise ValueError(f"{doc_id}: PDF has no pages")

        running = find_running_lines(texts)
        if running:
            log.info("%s: removing running header lines %s", doc_id, sorted(running))

        pages = []
        for i, raw in enumerate(texts, start=1):
            label, text = split_header(raw, running)
            pages.append(Page(
                pdf_index=i,
                printed_label=label,
                text=text,
                needs_review=len(text.strip()) < self.min_chars,
            ))

        low = [p.pdf_index for p in pages if p.needs_review]
        if low:
            log.warning("%s: %d page(s) under %d chars (PDF pages %s)",
                        doc_id, len(low), self.min_chars, low)

        low_fraction = len(low) / len(pages)
        return Document(
            doc_id=doc_id,
            doc_type=doc_type,
            pages=pages,
            is_scanned=low_fraction > self.max_low_text_fraction,
        )
