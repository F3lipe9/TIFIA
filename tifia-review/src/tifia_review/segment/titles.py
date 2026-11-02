"""TitleNormalizer: one normalization for TOC titles and body text, so they compare
equally, with a map from the normalized body text back to the original offsets (splits
happen in the original text, where PageMap gives the page citations)."""

import unicodedata
from dataclasses import dataclass

_QUOTES = {0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x2032: "'",
           0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"', 0x2033: '"'}
_DASHES = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)
_INVISIBLE = (0x00AD, 0x200B, 0xFEFF)
_CHARS = ({chr(c): v for c, v in _QUOTES.items()} | {chr(c): "-" for c in _DASHES}
          | {chr(c): "" for c in _INVISIBLE})


@dataclass
class NormalizedText:
    """text: normalized; index[i]: offset in the original of text[i]; index[len(text)]
    is the original's length. newline[i]: text[i] is a space that replaced whitespace
    containing a line break."""
    text: str
    index: list[int]
    newline: list[bool]

    def original(self, pos: int) -> int:
        return self.index[pos]


class TitleNormalizer:
    @staticmethod
    def _char(ch: str) -> str:
        if ch in _CHARS:
            return _CHARS[ch]
        return unicodedata.normalize("NFKC", ch) if ord(ch) > 127 else ch

    def normalize(self, text: str) -> NormalizedText:
        """Whitespace runs (line breaks included) -> one space; curly quotes and
        apostrophes -> straight; dashes -> "-"; ligatures and no-break spaces via NFKC."""
        out, index, newline = [], [], []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch.isspace():
                j = i
                while j < n and text[j].isspace():
                    j += 1
                out.append(" ")
                index.append(i)
                newline.append("\n" in text[i:j])
                i = j
                continue
            for c in self._char(ch):
                if c.isspace():  # NFKC turned a no-break space into a space
                    if not out or out[-1] != " ":
                        out.append(" ")
                        index.append(i)
                        newline.append(False)
                    continue
                out.append(c)
                index.append(i)
                newline.append(False)
            i += 1
        index.append(n)
        return NormalizedText("".join(out), index, newline)

    def title(self, title: str) -> str:
        """A TOC or template title as searched for: normalized, without a trailing period
        (the match pattern adds the heading's period back)."""
        return self.normalize(title).text.strip().rstrip(".").rstrip()
