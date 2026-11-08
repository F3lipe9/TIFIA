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


# Margin bands, by page position. In every document so far, running headers sit above
# y = 40pt and footers below y = 727pt of a 792pt page, while body text spans about
# 70-721pt, so a line whose vertical centre is in the top or bottom 8% is margin text.
# Position matters because extraction order does not: I-81's "ERRATA COPY/VERSION" is
# printed at the top of each page but PyMuPDF returns it as the page's last line.
_BAND = 0.08
_STAMP_FAMILY = re.compile(r"(?<![A-Za-z])[A-Z]{1,2}(?=-)|\d")


def _norm(line: str) -> str:
    """Line with spaces collapsed and none around hyphens: "Exhibit I - 1" -> "Exhibit I-1"."""
    return re.sub(r"\s*-\s*", "-", " ".join(line.split()))


def _family(line: str) -> str:
    """Stamp family: exhibit letters and digits masked, "Exhibit J-5" -> "Exhibit ?-#"."""
    return _STAMP_FAMILY.sub(lambda m: "#" if m.group().isdigit() else "?", _norm(line))


def band_lines(page: "pymupdf.Page") -> list[tuple[str, float]]:
    """(text, distance from the nearest page edge) for lines in the top or bottom band."""
    height = page.rect.height
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            centre = (line["bbox"][1] + line["bbox"][3]) / 2
            if centre < _BAND * height:
                out.append((text, centre))
            elif centre > (1 - _BAND) * height:
                out.append((text, height - centre))
    return out


class MarginRules:
    """What to do with margin-band text in one document.

    A band line is removed when it repeats on many pages: by shape (digits masked, e.g.
    "AMERICAS/2021334248.6", "ERRATA COPY/VERSION"), or, for a label-like line, by exact
    text (HRTAC's "Exhibit J-5" stamp on 101 pages is not a page number). A malformed
    variant of such a stamp ("Exhibit I - 1", which is not a valid label as printed) is a
    stamp too; a well-formed label like "Exhibit A-1" is only removed if its exact text
    repeats, so real exhibit page labels survive. Everything else in the band stays in
    the text, except the page label, which is moved to printed_label.
    """

    def __init__(self, pages: list[list[tuple[str, float]]]):
        min_pages = max(3, int(len(pages) * _RUNNING_MIN_SHARE))
        shapes = Counter(s for p in pages for s in {_shape(t) for t, _ in p})
        exact = Counter(s for p in pages for s in {_norm(t) for t, _ in p})
        self.running = {s for s, n in shapes.items() if n >= min_pages
                        and not _PAGE_LABEL.match(s.replace("#", "1"))}
        self.stamps = {s for s, n in exact.items() if n >= min_pages and _PAGE_LABEL.match(s)}
        self.stamp_families = {_family(s) for s in self.stamps}

    def is_running(self, line: str) -> bool:
        if _shape(line) in self.running or _norm(line) in self.stamps:
            return True
        return not _PAGE_LABEL.match(line.strip()) and _family(line) in self.stamp_families

    def split(self, text: str, band: list[tuple[str, float]]) -> tuple[str | None, str]:
        """(printed label, text without the label and the running band lines)."""
        drop = [t for t, _ in band if self.is_running(t)]
        labels = sorted((edge, t) for t, edge in band
                        if not self.is_running(t) and _PAGE_LABEL.match(t))
        label = labels[0][1] if labels else None  # the one nearest the page edge
        if label is not None:
            drop.append(label)
        lines = text.splitlines(keepends=True)
        for target in drop:
            hits = [n for n, ln in enumerate(lines) if ln.strip() == target]
            if hits:  # the occurrence nearest the start or end of the extracted text
                del lines[min(hits, key=lambda n: min(n, len(lines) - 1 - n))]
        return label, "".join(lines)


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
            bands = [band_lines(pdf_page) for pdf_page in pdf]

        if not texts:
            raise ValueError(f"{doc_id}: PDF has no pages")

        margins = MarginRules(bands)
        running = find_running_lines(texts)
        if margins.running or margins.stamp_families or running:
            log.info("%s: removing margin lines %s, stamps %s, header-zone lines %s", doc_id,
                     sorted(margins.running), sorted(margins.stamp_families), sorted(running))

        pages = []
        for i, (raw, band) in enumerate(zip(texts, bands), start=1):
            # Margin text found by position decides; pages with nothing in either band
            # keep the text-order rule (first lines, or the last line, see _HEADER_LINES).
            if band:
                label, text = margins.split(raw, band)
            else:
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
