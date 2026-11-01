"""Report: the deviation report for one agreement, as HTML (Jinja2) and PDF.

Layout (PHASE0 week 7): cover, summary, defined-term changes, substantive deviations by
category, added and removed sections, non-substantive table, needs review, method note.
Every deviation appears once, in the first of these that fits it: needs review, then a
whole section added or removed, then non-substantive, then defined term, then its
category. Each shows both sections with their printed pages (PDF page in brackets when it
differs), and modified text side by side with the changed words marked <del>/<ins>
(difflib on words compared after diff_engine.normalize, so quote styles don't show).

PDF: WeasyPrint as planned. It needs Pango (GTK) on Windows; when it can't load, a
Chromium browser (Edge or Chrome) prints the same HTML headless. Both honor the print CSS:
a page break before each part, page numbers in the footer, cards kept on one page where
they fit.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from tifia_review.classifier import FILL_IN_CATEGORY, NOT_CLASSIFIED, RENUMBERED_CATEGORY
from tifia_review.diff_engine import normalize
from tifia_review.llm.prompts import UNCLEAR
from tifia_review.models import (
    Alignment, ChangeType, Document, LabeledDeviation, Section, SegmentedDocument,
)

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent
WHOLE_SECTION_WORDS = 250   # text shown for a section added or removed in full
SNIPPET_CONTEXT = 4         # words around a change in the non-substantive table
SNIPPET_CHANGES = 2         # changes shown per row there
_BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
_BROWSER_NAMES = ["msedge", "google-chrome", "chrome", "chromium", "chromium-browser"]


# --- word diff -----------------------------------------------------------------------

def word_diff(template: str, executed: str) -> tuple[Markup, Markup]:
    """Both texts as HTML, with words only in the template in <del> and words only in
    the executed text in <ins>."""
    a, b = template.split(), executed.split()
    out_a, out_b = [], []
    for op, i1, i2, j1, j2 in _opcodes(a, b):
        part_a, part_b = escape(" ".join(a[i1:i2])), escape(" ".join(b[j1:j2]))
        if op == "equal":
            out_a.append(part_a)
            out_b.append(part_b)
            continue
        if i2 > i1:
            out_a.append(Markup("<del>%s</del>") % part_a)
        if j2 > j1:
            out_b.append(Markup("<ins>%s</ins>") % part_b)
    return Markup(" ").join(out_a), Markup(" ").join(out_b)


def change_snippet(template: str, executed: str) -> Markup:
    """The first changes inline, with a few words around each: '... within
    <del>thirty (30)</del> <ins>ten (10)</ins> days ...'."""
    a, b = template.split(), executed.split()
    ops = _opcodes(a, b)
    changes = [k for k, op in enumerate(ops) if op[0] != "equal"]
    if not changes:
        return Markup("")
    pieces = []
    for k in changes[:SNIPPET_CHANGES]:
        _, i1, i2, j1, j2 = ops[k]
        before = a[max(0, i1 - SNIPPET_CONTEXT):i1]
        after = a[i2:i2 + SNIPPET_CONTEXT] if i2 < len(a) else b[j2:j2 + SNIPPET_CONTEXT]
        piece = [escape(" ".join(before))] if before else []
        if i2 > i1:
            piece.append(Markup("<del>%s</del>") % " ".join(a[i1:i2]))
        if j2 > j1:
            piece.append(Markup("<ins>%s</ins>") % " ".join(b[j1:j2]))
        if after:
            piece.append(escape(" ".join(after)))
        pieces.append(Markup(" ").join(piece))
    more = len(changes) - SNIPPET_CHANGES
    return (Markup("… ") + Markup(" … ").join(pieces) + Markup(" …")
            + (Markup(" (%d more changes)") % more if more > 0 else Markup("")))


def _opcodes(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    keys_a, keys_b = [normalize(w) for w in a], [normalize(w) for w in b]
    return SequenceMatcher(None, keys_a, keys_b, autojunk=False).get_opcodes()


# --- report content ------------------------------------------------------------------

@dataclass
class Side:
    """Where a deviation is in one document."""
    section_id: str | None
    caption: str  # the section's caption, or "in <parent> <caption>" for an uncaptioned clause
    page: str     # "p. 45 (PDF p. 49)", "PDF p. 49" or ""

    def label(self) -> str:
        """'Section 12(a)(xxiv) (in 12(a) Conditions Precedent to Effectiveness)'."""
        if not self.section_id:
            return "not present"
        sid = self.section_id
        where = sid[0].upper() + sid[1:] if sid[0].isalpha() else f"Section {sid}"
        same = self.caption.casefold() == sid.casefold()
        return f"{where} {self.caption}" if self.caption and not same else where

    def cite(self, with_caption: bool = True) -> str:
        """The label (or just the section number) and the page."""
        if not self.section_id:
            return "not present"
        sid = self.section_id
        text = self.label() if with_caption else (
            sid[0].upper() + sid[1:] if sid[0].isalpha() else f"Section {sid}")
        return text + (f", {self.page}" if self.page else "")

    def __str__(self) -> str:
        return self.cite()


@dataclass
class Entry:
    ld: LabeledDeviation
    template: Side
    executed: Side
    template_html: Markup = Markup("")
    executed_html: Markup = Markup("")
    snippet: Markup = Markup("")
    whole_section: str = ""           # "added" or "removed" for a section new or gone in full
    truncated_words: int = 0          # words of a whole section not shown

    @property
    def d(self):
        return self.ld.deviation

    @property
    def title(self) -> str:
        if self.d.affects_defined_term:
            return f"\u201c{self.d.affects_defined_term}\u201d"
        return (self.executed if self.executed.section_id else self.template).label()

    def cites(self) -> tuple[str, str]:
        """Template and agreement citations for a card; a caption already in the title is
        not repeated."""
        if self.d.affects_defined_term:
            return self.template.cite(), self.executed.cite()
        shown = self.executed if self.executed.section_id else self.template
        return (self.template.cite(self.template.label() != shown.label()),
                self.executed.cite(self.executed.label() != shown.label()))


@dataclass
class Group:
    name: str
    entries: list[Entry]
    slug: str = ""


@dataclass
class ReportData:
    meta: dict                         # cover and method-note facts
    counts: dict
    category_counts: list[tuple[str, int, int, int]]  # name, substantive, other, review
    definitions: list[Entry]
    groups: list[Group]
    sections_added: list[Entry]
    sections_removed: list[Entry]
    non_substantive: list[Entry]
    needs_review: list[Entry]
    unreadable_pages: list[tuple[str, int, str]]  # document, PDF page, printed label
    classified: bool
    alignment: dict = field(default_factory=dict)


def build_report_data(labeled: list[LabeledDeviation], template_doc: Document,
                      executed_doc: Document, template: SegmentedDocument,
                      executed: SegmentedDocument, alignments: list[Alignment],
                      categories: list[str], meta: dict) -> ReportData:
    """Sort the labeled deviations into the report's parts. categories: the taxonomy in
    the order to show it."""
    t_side = _SideMaker(template_doc, template)
    e_side = _SideMaker(executed_doc, executed)
    parts: dict[str, list[Entry]] = {k: [] for k in
                                     ("review", "added", "removed", "non", "terms", "main")}
    for ld in labeled:
        d = ld.deviation
        entry = Entry(ld, t_side(d.template_section_id, d.template_page),
                      e_side(d.executed_section_id, d.executed_page))
        if d.template_section_id is None or d.executed_section_id is None:
            entry.whole_section = "added" if d.template_section_id is None else "removed"
        part = ("review" if ld.needs_review else entry.whole_section
                or ("non" if not ld.substantive else "terms" if d.affects_defined_term
                    else "main"))
        if part == "non":
            entry.snippet = _snippet(ld)
        elif entry.whole_section:
            words = (d.executed_text or d.template_text).split()
            shown = escape(" ".join(words[:WHOLE_SECTION_WORDS]))
            entry.truncated_words = max(0, len(words) - WHOLE_SECTION_WORDS)
            html = Markup("<ins>%s</ins>" if d.template_section_id is None
                          else "<del>%s</del>") % shown
            entry.template_html, entry.executed_html = (
                (Markup(""), html) if d.template_section_id is None else (html, Markup("")))
        else:
            entry.template_html, entry.executed_html = word_diff(d.template_text,
                                                                 d.executed_text)
        parts[part].append(entry)

    classified = any(ld.category != NOT_CLASSIFIED for ld in labeled
                     if ld.category not in (FILL_IN_CATEGORY, RENUMBERED_CATEGORY))
    groups = _groups(parts["main"], categories, e_side, t_side)
    count = Counter(ld.category for ld in labeled)
    review = Counter(ld.category for ld in labeled if ld.needs_review)
    substantive = Counter(ld.category for ld in labeled if ld.substantive and not ld.needs_review)
    fixed = [FILL_IN_CATEGORY, RENUMBERED_CATEGORY]
    order = [c for c in [*categories, UNCLEAR, NOT_CLASSIFIED] if count[c]] + sorted(
        c for c in count if c not in [*categories, UNCLEAR, NOT_CLASSIFIED, *fixed])
    category_counts = [(c, substantive[c], count[c] - substantive[c] - review[c], review[c])
                       for c in order]
    counts = {
        "total": len(labeled),
        "substantive": sum(1 for ld in labeled if ld.substantive and not ld.needs_review),
        "non_substantive": sum(1 for ld in labeled if not ld.substantive
                               and not ld.needs_review),
        "fill_in": count[FILL_IN_CATEGORY], "renumbered": count[RENUMBERED_CATEGORY],
        "needs_review": len(parts["review"]),
        "definitions": sum(1 for ld in labeled if ld.deviation.affects_defined_term
                           and ld.category != FILL_IN_CATEGORY),
        "sections_added": len(parts["added"]), "sections_removed": len(parts["removed"]),
    }
    unreadable = [(name, p.pdf_index, p.printed_label or "")
                  for name, doc in (("Template", template_doc), ("Agreement", executed_doc))
                  for p in doc.pages if p.needs_review]
    methods = Counter(a.method.value if a.method else
                      ("removed" if a.executed_section is None else "added") for a in alignments)
    return ReportData(meta, counts, category_counts, parts["terms"], groups, parts["added"],
                      parts["removed"], parts["non"], parts["review"], unreadable, classified,
                      dict(methods))


def _snippet(ld: LabeledDeviation) -> Markup:
    d = ld.deviation
    if d.change_type is ChangeType.RENUMBERED:
        return Markup("%s → %s") % (d.template_section_id, d.executed_section_id)
    if not d.template_text or not d.executed_text:
        text = d.executed_text or d.template_text
        words = text.split()
        tag = "<ins>%s</ins>" if d.executed_text else "<del>%s</del>"
        return Markup(tag) % (" ".join(words[:25]) + (" …" if len(words) > 25 else ""))
    return change_snippet(d.template_text, d.executed_text)


def _groups(entries: list[Entry], categories: list[str], e_side, t_side) -> list[Group]:
    """By category in taxonomy order; unclassified deviations by top-level section, in
    the order of the agreement (the template for a section the agreement lacks)."""
    by_name: dict[str, list[Entry]] = {}
    position: dict[str, tuple[int, int]] = {}
    for e in entries:
        name = e.ld.category
        if name == NOT_CLASSIFIED:
            side, maker = (e.executed, e_side) if e.executed.section_id else (e.template, t_side)
            top = _top(side.section_id or "")
            name = maker.top_caption(top)
            position.setdefault(name, (maker is t_side, maker.position(top)))
        by_name.setdefault(name, []).append(e)
    known = [c for c in categories if c in by_name]
    rest = sorted((c for c in by_name if c not in categories),
                  key=lambda c: position.get(c, (True, 10**6)))
    return [Group(name, by_name[name], f"group-{n}")
            for n, name in enumerate(known + rest, 1)]


def _top(section_id: str) -> str:
    return section_id.split("(")[0] if section_id[:1].isdigit() else section_id


class _SideMaker:
    """Side (section caption and printed page) for one document."""

    def __init__(self, doc: Document, segmented: SegmentedDocument):
        self.labels = {p.pdf_index: p.printed_label for p in doc.pages}
        self.sections: dict[str, Section] = {s.section_id: s for s in segmented.sections}
        self.order = {s.section_id: n for n, s in enumerate(segmented.sections)}

    def __call__(self, section_id: str | None, pdf_page: int | None) -> Side:
        if section_id is None:
            return Side(None, "", "")
        return Side(section_id, self._caption(section_id), self._page(pdf_page))

    def _caption(self, section_id: str) -> str:
        section = self.sections.get(section_id)
        if section is None:
            return ""
        if section.heading:
            return section.heading
        parent = self.sections.get(section.parent_id) if section.parent_id else None
        while parent is not None and not parent.heading:
            parent = self.sections.get(parent.parent_id) if parent.parent_id else None
        return f"(in {parent.section_id} {parent.heading})" if parent else ""

    def _page(self, pdf_page: int | None) -> str:
        if pdf_page is None:
            return ""
        label = self.labels.get(pdf_page)
        if not label:
            return f"PDF p. {pdf_page}"
        return f"p. {label}" + ("" if label == str(pdf_page) else f" (PDF p. {pdf_page})")

    def position(self, section_id: str) -> int:
        return self.order.get(section_id, 10**6)

    def top_caption(self, top: str) -> str:
        section = self.sections.get(top)
        if section is None:
            return top or "Other"
        return Side(top, section.heading, "").label()


# --- rendering -----------------------------------------------------------------------

def render_html(data: ReportData) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                      autoescape=select_autoescape(["html"]), trim_blocks=True,
                      lstrip_blocks=True)
    return env.get_template("template.html").render(r=data)


def render_pdf(html_path: Path, pdf_path: Path, browser: str | None = None) -> str:
    """Print html_path to pdf_path; returns the renderer used. browser: path to a
    Chromium browser, for the fallback (default: TIFIA_BROWSER, then a usual location)."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # OSError: the Pango/GTK libraries are missing
        log.info("WeasyPrint not available (%s); printing with a headless browser",
                 str(e).splitlines()[0] if str(e) else type(e).__name__)
    else:
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return "WeasyPrint"
    browser = browser or find_browser()
    if browser is None:
        raise RuntimeError("no PDF renderer: install WeasyPrint with its Pango libraries, or "
                           "Microsoft Edge or Google Chrome (or set TIFIA_BROWSER)")
    pdf_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        # a throwaway profile, so a browser window the user has open is not touched
        result = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--disable-extensions", f"--user-data-dir={profile}",
             "--no-pdf-header-footer", "--print-to-pdf-no-header",
             f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
            capture_output=True, text=True, timeout=600)
    if not pdf_path.exists():
        raise RuntimeError(f"{browser} did not write the PDF: {result.stderr.strip()[-500:]}")
    return f"{Path(browser).stem} (headless)"


def find_browser() -> str | None:
    """TIFIA_BROWSER, else Edge or Chrome where they usually are, else on PATH."""
    if os.environ.get("TIFIA_BROWSER"):
        return os.environ["TIFIA_BROWSER"]
    for path in _BROWSERS:
        if Path(path).exists():
            return path
    return next((found for name in _BROWSER_NAMES if (found := shutil.which(name))), None)


def write_report(data: ReportData, out_dir: Path, stem: str = "report") -> tuple[Path, Path, str]:
    """report.html and report.pdf in out_dir; returns their paths and the renderer."""
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path = out_dir / f"{stem}.html", out_dir / f"{stem}.pdf"
    data.meta.setdefault("generated", datetime.now().strftime("%B %d, %Y %H:%M"))
    html_path.write_text(render_html(data), encoding="utf-8")
    renderer = render_pdf(html_path, pdf_path)
    return html_path, pdf_path, renderer
