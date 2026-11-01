"""SummaryBuilder: the summary as HTML and PDF (the deviation report's renderer), at most
max_pages pages.

Page limit, enforced by code: render, count the PDF's pages, and if there are too many,
shorten and render again: (1) each summary to its first sentence and each "other change"
to 25 words, (2) two "other changes" and two deviation IDs per row, (3) smaller type. A
summary that is still too long is an error (SummaryTooLong), never a third page.
"""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tifia_review.diff_engine import normalize
from tifia_review.models import LabeledDeviation, ScheduleDigest, TopicRow, ValueStatus
from tifia_review.report.builder import render_pdf
from tifia_review.store import to_jsonable
from tifia_review.summary.text import section_cite

TEMPLATE_DIR = Path(__file__).parent
DISCLAIMER = "Prototype — prepared for professional review. Not legal or financial advice."
_SMALL_WORDS = {"of", "and", "the", "for", "a", "an", "in", "on", "to", "at", "by"}


class SummaryTooLong(RuntimeError):
    pass


@dataclass
class OtherChange:
    deviation_id: str
    where: str          # "§15(k)" or "§1 “Toll Revenues”"
    category: str
    explanation: str


@dataclass
class SummaryData:
    meta: dict                       # header facts and file names
    bottom_line: str
    bottom_line_by_llm: bool
    rows: list[TopicRow]
    other_changes: list[OtherChange]
    other_total: int                 # substantive deviations outside the topics
    schedule: ScheduleDigest | None
    totals: dict                     # total, substantive, needs_review (as the full report)
    classified: bool
    level: int = 0                   # how much was shortened to fit
    disclaimer: str = DISCLAIMER
    notes: list[str] = field(default_factory=list)

    # --- what each cell says ---------------------------------------------------------

    def summary_text(self, row: TopicRow) -> str:
        if row.status is ValueStatus.NOT_INSERTED:
            return "Not yet inserted (draft)."
        if row.status is ValueStatus.NOT_FOUND or not row.summary:
            return f"See agreement {', '.join(row.sections[:3])}."
        text = row.summary
        if self.level >= 1:
            text = _first_sentence(text)
        if row.status is ValueStatus.DRAFT_BLANK and "not final" not in text.casefold():
            text += " (draft, not final)"
        return text

    def source_text(self, row: TopicRow) -> str:
        """'§1, PDF pp. 15, 17, 30; §9, p. 44': one entry per section, pages in order."""
        if not row.sources:
            return ", ".join(row.sections[:3])
        pages: dict[str, list[str]] = {}
        for source in row.sources:
            where, _, page = source.partition(", ")
            pages.setdefault(where, [])
            if page and page not in pages[where]:
                pages[where].append(page)
        out = []
        for where, found in pages.items():
            pdf = [p.removeprefix("PDF p. ") for p in found if p.startswith("PDF p. ")]
            printed = [p.removeprefix("p. ") for p in found if not p.startswith("PDF p. ")]
            parts = [_pages(prefix, nums) for prefix, nums in (("p.", printed), ("PDF p.", pdf))
                     if nums]
            out.append(where + (", " + ", ".join(parts) if parts else ""))
        return "; ".join(out)

    def changed_text(self, row: TopicRow) -> str:
        ids = row.change_ids[:2 if self.level >= 2 else 3]
        if not self.classified:
            n = row.unclassified + row.substantive_changes + row.other_changes
            return f"{n} change{'s' * (n != 1)}, not classified" if n else "Standard"
        if row.substantive_changes:
            n = row.substantive_changes
            text = (f"{n} substantive change{'s' * (n != 1)} — {', '.join(ids)}"
                    + (" …" if n > len(ids) else ""))
        else:
            text = "Standard" + (f" ({row.other_changes} minor)" if row.other_changes else "")
        if row.needs_review:
            text += f"; {row.needs_review} need{'s' * (row.needs_review == 1)} review"
        return text

    def schedule_lines(self) -> list[str]:
        s = self.schedule
        if s is None:
            return []
        exhibit = f"Exhibit {s.exhibit or 'G'}"
        if s.status is ValueStatus.NOT_INSERTED:
            return [f"Debt service schedule ({exhibit}): not yet inserted (draft)."]
        if s.status is not ValueStatus.FOUND:
            where = f", PDF p. {s.pages[0]}" if s.pages else " (not in this PDF)"
            return [f"Full schedule: {exhibit}{where}."]
        if s.matches_loan_amount is False:
            return [f"Schedule total doesn't match loan amount — see {exhibit}, PDF p. "
                    f"{s.pages[0]}."]
        lines = [f"First payment {s.first_payment}; final payment {s.final_payment}.",
                 f"{s.payment_count} payments, {s.frequency}; principal ${s.total_principal:,.0f}."]
        if s.largest_annual:
            year, amount = s.largest_annual
            lines.append(f"Largest annual payment ${amount / 1e6:,.1f}M ({year}).")
        return lines

    def other_text(self, change: OtherChange) -> str:
        text = change.explanation
        if self.level >= 1:
            words = text.split()
            text = " ".join(words[:25]) + (" …" if len(words) > 25 else "")
        return text

    def shown_other(self) -> list[OtherChange]:
        return self.other_changes[:2] if self.level >= 2 else self.other_changes


def _pages(prefix: str, nums: list[str]) -> str:
    """('PDF p.', ['30', '15']) -> 'PDF pp. 15, 30'."""
    nums = sorted(nums, key=lambda n: (len(n), n))
    if len(nums) > 1:
        prefix = prefix[:-2] + "pp."
    return f"{prefix} {', '.join(nums)}"


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=1)
    return parts[0]


def other_changes(ranked: list[LabeledDeviation], top_n: int) -> list[OtherChange]:
    out = []
    for ld in ranked[:top_n]:
        d = ld.deviation
        where = section_cite(d.executed_section_id or d.template_section_id or "?")
        if d.affects_defined_term:
            where = f"§1 “{d.affects_defined_term}”"
        out.append(OtherChange(d.deviation_id, where, ld.category, ld.explanation))
    return out


def parties(preamble: str) -> tuple[str | None, str | None]:
    """Borrower and lender from the opening paragraph."""
    text = normalize(preamble)
    m = re.search(r"by and between (?:the )?(.+?),\s+(?:a|an)\s", text, re.I)
    borrower = _title(m.group(1)) if m else None
    lender = None
    if "build america bureau" in text.casefold():
        lender = "U.S. Department of Transportation (Build America Bureau)"
    elif "department of transportation" in text.casefold():
        lender = "U.S. Department of Transportation"
    return borrower, lender


def _title(name: str) -> str:
    name = " ".join(name.split())
    if not name.isupper():
        return name
    words = name.lower().split()
    return " ".join(w if n and w in _SMALL_WORDS else w[:1].upper() + w[1:]
                    for n, w in enumerate(words))


class SummaryBuilder:
    def __init__(self, max_pages: int = 2):
        self.max_pages = max_pages
        self.env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                               autoescape=select_autoescape(["html"]), trim_blocks=True,
                               lstrip_blocks=True)

    def render_html(self, data: SummaryData) -> str:
        return self.env.get_template("summary.html").render(s=data)

    def write(self, data: SummaryData, out_dir: Path, stem: str = "summary",
              renderer=render_pdf) -> tuple[Path, Path, str, SummaryData]:
        """summary.html, summary.pdf and summary.json in out_dir, shortened until the PDF
        fits; returns their paths (HTML, PDF), the renderer and the data as rendered."""
        out_dir.mkdir(parents=True, exist_ok=True)
        data.meta.setdefault("generated", datetime.now().strftime("%B %d, %Y %H:%M"))
        html_path, pdf_path = out_dir / f"{stem}.html", out_dir / f"{stem}.pdf"
        for level in range(4):
            shown = replace(data, level=level)
            html_path.write_text(self.render_html(shown), encoding="utf-8")
            used = renderer(html_path, pdf_path)
            pages = count_pages(pdf_path)
            if pages <= self.max_pages:
                (out_dir / f"{stem}.json").write_text(
                    json.dumps(to_jsonable(shown), indent=1, ensure_ascii=False),
                    encoding="utf-8")
                return html_path, pdf_path, used, shown
        raise SummaryTooLong(f"{pdf_path} has {pages} pages after shortening; the limit is "
                             f"{self.max_pages}")


def count_pages(pdf: Path) -> int:
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return len(doc)
