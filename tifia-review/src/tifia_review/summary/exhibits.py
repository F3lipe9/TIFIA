"""ExhibitLocator: the pages of the TIFIA Debt Service exhibit in an agreement PDF.

Found by its heading, not its letter: it is Exhibit G in the 2023 template and Exhibit F
in the 2021 Sound Transit agreements. A page counts when its first lines read
"EXHIBIT <letter> ... TIFIA DEBT SERVICE"; tables of contents (many exhibit headings, or
dot leaders) and pages that only mention the exhibit in running text do not. The exhibit
runs until a page that starts another exhibit or schedule. Body-only PDFs do not have it:
the manifest's full_path names the PDF with exhibits, and exhibit_g_pages ("120-122")
overrides the search when it fails.
"""

import re
from dataclasses import dataclass
from pathlib import Path

HEAD_WORDS = 40
_HEADING = re.compile(r"\bEXHIBIT\s+([A-Z](?:-\d+)?)\s*[-–—:]?\s*TIFIA\s+DEBT\s+SERVICE\b")
_ANY_EXHIBIT = re.compile(r"\b(EXHIBIT|SCHEDULE)\s+[A-Z0-9IVX]+(?:-\d+)?\b")
_DOT_LEADER = re.compile(r"\.{6,}")


@dataclass
class ExhibitPages:
    letter: str | None
    pages: list[int]   # 1-based PDF pages
    text: str          # their text, joined


def parse_pages(spec: str) -> list[int]:
    """'120-122, 125' -> [120, 121, 122, 125]."""
    pages = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        first, _, last = part.partition("-")
        pages.extend(range(int(first), int(last or first) + 1))
    return pages


class ExhibitLocator:
    def find(self, pdf: Path, override: str = "") -> ExhibitPages:
        import pymupdf
        with pymupdf.open(pdf) as doc:
            texts = [page.get_text() for page in doc]
        if override.strip():
            pages = [p for p in parse_pages(override) if 1 <= p <= len(texts)]
            letter = None
            if pages and (m := _HEADING.search(_head(texts[pages[0] - 1]))):
                letter = m.group(1)
            return ExhibitPages(letter, pages, "\n".join(texts[p - 1] for p in pages))
        for i, text in enumerate(texts):
            head = _head(text)
            m = _HEADING.search(head)
            if m is None or _is_toc(text):
                continue
            pages = [i + 1]
            for j in range(i + 1, len(texts)):
                nxt = _head(texts[j])
                other = _ANY_EXHIBIT.search(nxt)
                if other and other.group(0).split()[-1].split("-")[0] != m.group(1).split("-")[0]:
                    break  # the next exhibit or schedule starts
                pages.append(j + 1)
            return ExhibitPages(m.group(1), pages, "\n".join(texts[p - 1] for p in pages))
        return ExhibitPages(None, [], "")


def _head(text: str) -> str:
    return " ".join(text.split()[:HEAD_WORDS])


def _is_toc(text: str) -> bool:
    return len(_ANY_EXHIBIT.findall(text)) >= 4 or bool(_DOT_LEADER.search(text))

