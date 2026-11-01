"""Paths, a cached load-and-segment helper and small-document builders shared by the
stage tests."""

import json
from functools import cache
from pathlib import Path

import numpy as np

from tifia_review.loader import DocumentLoader
from tifia_review.models import Section, SegmentedDocument
from tifia_review.segmenter import (
    Segmenter, find_definitions_section, find_toc_pages, parse_definitions, parse_toc,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "data" / "templates" / "TIFIA Public Borrower TLA Template.pdf"
NON_PROJECT = ROOT / "data" / "templates" / "TIFIA Non-Project Revenue TLA Template.pdf"
AGREEMENT = (ROOT / "data" / "agreements"
             / "downtown-redmond-link-extension-project-TIFA-loan-agreement-executed-9.10.21.pdf")
FIXTURES = ROOT / "tests" / "fixtures"
# per fixture: the planted change(s) and the filled-in blanks (make_fixtures.py)
EXPECTED = json.loads((FIXTURES / "expected_deviations.json").read_text(encoding="utf-8"))


@cache
def segment_pdf(path: Path):
    """(Document, SegmentedDocument, TOC entries) for a PDF, loaded once per test run."""
    doc = DocumentLoader().load(str(path), path.stem, "x")
    raw = [p.text for p in doc.pages]
    return doc, Segmenter().segment(doc), parse_toc([raw[i] for i in find_toc_pages(raw)])


def make_doc(doc_id: str, specs: list[tuple[str, str, str]]) -> SegmentedDocument:
    """Sections from (id, heading, own text). Level and parent follow from the id, a
    parent's text runs through its children's, and a section captioned "Definitions"
    provides the defined terms, as the segmenter produces them. Everything is on page 1."""
    def parent_of(section_id):
        if "(" not in section_id or section_id.startswith(("Exhibit", "Schedule")):
            return None
        return section_id[:section_id.rindex("(")]

    own = {sid: text for sid, _, text in specs}
    full = {}
    for sid, _, _ in reversed(specs):
        kids = [k for k, _, _ in specs if parent_of(k) == sid]
        full[sid] = "\n".join([own[sid]] + [full[k] for k in kids])
    sections = [Section(sid, heading, full[sid], 1, 1,
                        level=sid.count("(") + 1 if parent_of(sid) else 1,
                        parent_id=parent_of(sid))
                for sid, heading, _ in specs]
    definitions = find_definitions_section(sections)
    return SegmentedDocument(doc_id, sections,
                             parse_definitions(definitions) if definitions else {})


def no_embeddings(texts: list[str]) -> np.ndarray:
    """Embedder for tests where the aligner's rules must match everything."""
    raise AssertionError("the rules should have matched everything")
