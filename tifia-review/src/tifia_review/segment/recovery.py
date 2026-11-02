"""HeadingRecovery: find the top-level sections the segmenter missed, by title.

Some PDFs lose their "Section N." numbers in extraction, and the heading is glued to the
end of the previous paragraph:

    ...signed by a duly authorized representative of such party.TIFIA Loan Amount. The ...

For each expected section (TOC order) that the first pass did not find, the title is
searched for forward from the previous section's start and before the next section the
first pass did find, so a recovered section can never jump past one known to come after
it. Only additions: every heading already found is kept.

A match must look like a heading:
  * the title followed by a period, then a space, the end, or the glued next sentence
    (a capital letter, quote, bracket or parenthesis): not "the TIFIA Loan Amount shall";
  * not inside a parenthesis or mid-word: not "Section 36 (Notices; Payment Instructions)";
  * starting a sentence: after ".", ";", ":", "?" or "!" (maybe with closing quotes or
    brackets), or as one of the first lines of a page (after the running header), or
    with its "Section N." number at the start of a line. That rejects a title that ends a
    sentence in the middle of a paragraph ("... at a Variable Interest Rate. The ...").
If the title is not found, a "Section N." line in the window counts (its caption may
differ from the TOC's, which is why the first pass rejected it).
"""

import bisect
import logging
import re
from dataclasses import dataclass, field

from tifia_review.models import BoundarySource, TocEntry
from tifia_review.segment.titles import NormalizedText, TitleNormalizer

log = logging.getLogger(__name__)

_TERMINATORS = ".;:!?"
_CLOSERS = "\"')]"
_NUMBER_BEFORE = re.compile(r"Section\s\d{1,3}\.\s?$", re.IGNORECASE)
_PAGE_TOP_LINES = 3  # a heading among a page's first lines needs no sentence end before it


@dataclass
class Boundary:
    section_id: str
    pos: int                 # offset in the original text where the section starts
    title: str               # heading for the new section
    source: BoundarySource


@dataclass
class RecoveryResult:
    boundaries: list[Boundary] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _title_pattern(title: str) -> str:
    """The normalized title as a regex: one space may be a line break that normalization
    already turned into a space; a hyphen may be followed by one (a line-end hyphen)."""
    words = [re.escape(w) for w in title.split(" ") if w]
    pattern = r"\s".join(words)
    return pattern.replace(r"\-", r"-\s?")


class HeadingRecovery:
    def __init__(self, normalizer: TitleNormalizer | None = None):
        self.normalizer = normalizer or TitleNormalizer()

    # --- matching ---------------------------------------------------------------------
    def _page_top(self, text: str, pos: int, page_starts: list[int]) -> bool:
        """Whether pos starts one of the first _PAGE_TOP_LINES non-blank lines of its page."""
        line_start = text.rfind("\n", 0, pos) + 1
        if text[line_start:pos].strip():
            return False
        k = bisect.bisect_right(page_starts, pos) - 1
        page_start = page_starts[k] if k >= 0 else 0
        before = [ln for ln in text[page_start:line_start].split("\n") if ln.strip()]
        return len(before) < _PAGE_TOP_LINES

    def _starts_sentence(self, norm: NormalizedText, s: int, numbered: bool, text: str,
                         page_starts: list[int]) -> bool:
        if s == 0:
            return True
        p, line_start = s - 1, False
        if norm.text[p] == " ":
            line_start = norm.newline[p]
            p -= 1
        if p < 0:
            return True
        if norm.text[s - 1] != " " and (norm.text[s - 1] == "(" or norm.text[s - 1].isalnum()):
            return False  # mid-word or inside a parenthesis
        if numbered and line_start:
            return True
        q = p
        while q >= 0 and norm.text[q] in _CLOSERS:
            q -= 1
        if q >= 0 and norm.text[q] in _TERMINATORS:
            return True
        if line_start and norm.text[p] in ")]":  # "... [Reserved]" on the line before
            return True
        return line_start and self._page_top(text, norm.original(s), page_starts)

    def find_heading(self, norm: NormalizedText, title: str, start: int, end: int,
                     text: str = "", page_starts: list[int] | None = None
                     ) -> list[tuple[int, bool]]:
        """Every heading-form match of title in norm.text[start:end], as (normalized
        start, whether it includes a "Section N." prefix), first first."""
        title = self.normalizer.title(title)
        if not title:
            return []
        pattern = re.compile(rf"(Section\s\d{{1,3}}\.\s?)?({_title_pattern(title)})\.",
                             re.IGNORECASE)
        hits, pos = [], start
        while True:
            m = pattern.search(norm.text, pos, end)
            if not m:
                return hits
            pos = m.start() + 1
            numbered = m.group(1) is not None
            if numbered and not m.group(1)[0].isupper():
                continue
            s = m.start() if numbered else m.start(2)
            if not numbered and _NUMBER_BEFORE.search(norm.text, max(0, s - 16), s):
                continue  # its "Section N." prefix was rejected above: so is the title
            if not norm.text[m.start(2)].isupper():
                continue  # "the interest rate." is a phrase, not a heading
            after = norm.text[m.end():m.end() + 1]
            if after and not (after == " " or after.isupper() or after in "\"'([“"):
                continue
            if not self._starts_sentence(norm, s, numbered, text, page_starts or [0]):
                continue
            hits.append((s, numbered))
            if numbered:  # the prefixed match also covers the bare-title match after it
                pos = m.end()

    @staticmethod
    def find_number_line(text: str, section_id: str, start: int, end: int) -> int | None:
        """A "Section N." heading at a line start in text[start:end]."""
        m = re.compile(rf"(?m)^[ \t]*(?:Section|SECTION)[ \t]+{section_id}\.(?=\s)").search(
            text, start, end)
        return m.start() + len(m.group(0)) - len(m.group(0).lstrip()) if m else None

    # --- the walk ---------------------------------------------------------------------
    def recover(self, text: str, expected: list[TocEntry], found: dict[str, int],
                body_start: int, body_end: int, source: BoundarySource,
                page_starts: list[int] | None = None) -> RecoveryResult:
        """Boundaries for the expected sections missing from found (section_id -> start
        offset in text), searched for in text[body_start:body_end] (never in the TOC)."""
        norm = self.normalizer.normalize(text)
        to_norm = lambda orig: bisect.bisect_left(norm.index, orig, 0, len(norm.text))
        result, cursor = RecoveryResult(), body_start
        for k, entry in enumerate(expected):
            if entry.section_id in found:
                cursor = max(cursor, found[entry.section_id] + 1)
                continue
            limit = next((found[e.section_id] for e in expected[k + 1:]
                          if e.section_id in found), body_end)
            limit = min(limit, body_end)
            if limit <= cursor:
                result.missing.append(entry.section_id)
                continue
            hits = self.find_heading(norm, entry.title, to_norm(cursor), to_norm(limit), text,
                                     page_starts)
            if hits:
                s, numbered = hits[0]
                pos = norm.original(s)
                if len(hits) > 1:
                    where = ", ".join(str(norm.original(h)) for h, _ in hits)
                    warning = (f"{entry.section_id} {entry.title!r}: {len(hits)} matches "
                               f"(offsets {where}); took the first")
                    log.warning("%s", warning)
                    result.warnings.append(warning)
                how = BoundarySource.SECTION_NUMBER if numbered else source
            else:
                pos = self.find_number_line(text, entry.section_id, cursor, limit)
                if pos is None:
                    result.missing.append(entry.section_id)
                    continue
                how = BoundarySource.SECTION_NUMBER
            result.boundaries.append(Boundary(entry.section_id, pos, entry.title, how))
            cursor = pos + 1
        return result
