"""Aligner: pair each executed-agreement section with its template section.

Three passes, most reliable first:

1. NUMBER     same number under paired parents: "4(c)" <-> "4(c)", or "6(c)" when Section 5
              became Section 6. A list with a clause added or removed is relettered from
              there on (in the 2021 agreements "13(k)" is "Credit Rating", the template's
              "13(l)"), so the pair must also share most caption words or, without
              captions on both sides, read alike.
2. HEADING    the same caption after lowercasing and dropping punctuation, numbers,
              bracketed template options, small words and a plural "s"; then the closest
              caption among siblings. A caption found once in each document may also pair
              across sections at the same level (a clause moved elsewhere), or one level up
              or down within a paired section (a list nested one level deeper).
3. EMBEDDING  cosine similarity of section texts (sentence-transformers), greedily from the
              best score down, each section used once, nothing below the threshold: first
              among the children of paired parents, then up to one level apart inside a
              paired section, then anywhere. Clauses on the same topic often score above
              0.8 while saying different things, so a match must also keep its number or
              read alike (closely alike, outside paired sections).

Children are matched once their parents are paired, so a renumbered section takes its
clauses with it, and weaker evidence is only used for what stronger evidence left over.
What remains is REMOVED (template only) or ADDED (executed only). A section split in two
or merged shows up as one match plus ADDED or REMOVED sections; Phase 0 does not handle
that.
"""

import difflib
import logging
import re
from collections import defaultdict, deque
from collections.abc import Callable

import numpy as np

from tifia_review.models import AlignMethod, Alignment, SegmentedDocument

log = logging.getLogger(__name__)

Embedder = Callable[[list[str]], np.ndarray]  # texts -> unit-length row vectors

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Calibrated on the 2021 DRLE agreement against both 2023 templates: same-number pairs
# whose captions share no words (relettered clauses) never read more alike than 0.34,
# while most true pairs read above 0.8.
_HEADING_MIN = 0.5      # caption similarity that counts as the same caption
_TEXT_MIN = 0.4         # wording similarity that confirms a pair
_MOVED_TEXT_MIN = 0.6   # ... for an embedding match between unrelated sections
_BETTER_MARGIN = 0.15   # a sibling that reads this much more alike beats the same number
_TEXT_WORDS = 600       # words compared per section
_OWN_TEXT_MIN_WORDS = 8  # a parent's text before its first clause, if this long, also counts
_MINOR_WORDS = frozenset(
    "a an and as at by for from in into of on or other than the to under upon with".split())
_BRACKETED = re.compile(r"\[[^\]]*\]")
_LEADING_LABEL = re.compile(
    r"\s*(?:\([A-Za-z0-9]{1,7}\)|(?:Section|SECTION)[ \t]+\d+\.|\[?(?:EXHIBIT|SCHEDULE)[ \t]+\S+)")


def heading_words(heading: str) -> frozenset[str]:
    """Caption words without case, punctuation, numbers, bracketed options ("Opinions of
    Counsel [and each other Borrower Related Party]"), small words or a plural "s"."""
    words = (re.findall(r"[a-z]+", _BRACKETED.sub(" ", heading).lower())
             or re.findall(r"[a-z]+", heading.lower()))
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
                     for w in words if w not in _MINOR_WORDS)


def heading_similarity(a: str, b: str) -> float | None:
    """Mean of the shared-word share of the shorter caption and of both captions: "Hedging"
    vs "Hedging and Variable Rate Bonds" scores 0.62, "Optional Prepayments" vs "Mandatory
    Prepayments" 0.42. None if either caption is empty."""
    wa, wb = heading_words(a), heading_words(b)
    if not wa or not wb:
        return None
    shared = len(wa & wb)
    return (shared / min(len(wa), len(wb)) + shared / len(wa | wb)) / 2


def wording_similarity(a: str, b: str) -> float:
    """difflib ratio over the first _TEXT_WORDS words of each text."""
    words_a = re.findall(r"[a-z0-9]+", a.lower())[:_TEXT_WORDS]
    words_b = re.findall(r"[a-z0-9]+", b.lower())[:_TEXT_WORDS]
    return difflib.SequenceMatcher(None, words_a, words_b, autojunk=False).ratio()


def load_embedder(model_name: str = DEFAULT_MODEL, download: bool = True) -> Embedder:
    """sentence-transformers encoder returning unit-length vectors. Uses the local
    Hugging Face cache when the model is there; otherwise downloads it, or raises OSError
    if download is False."""
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(model_name, local_files_only=True)
    except OSError:
        if not download:
            raise
        model = SentenceTransformer(model_name)
    return lambda texts: model.encode(texts, normalize_embeddings=True, batch_size=32,
                                      show_progress_bar=False)


class _Tree:
    """One document's sections by id, with children, labels and comparable texts."""

    def __init__(self, doc: SegmentedDocument):
        self.order = [s.section_id for s in doc.sections]
        self.index = {section_id: n for n, section_id in enumerate(self.order)}
        self.by_id = {s.section_id: s for s in doc.sections}
        self.children: dict[str | None, list[str]] = defaultdict(list)
        for s in doc.sections:
            self.children[s.parent_id].append(s.section_id)

    def level(self, section_id: str) -> int:
        return self.by_id[section_id].level

    def label(self, section_id: str) -> str:
        """Id relative to the parent: "(xii)" for "12(a)(xii)"; the id itself at the top."""
        parent = self.by_id[section_id].parent_id
        return section_id[len(parent):] if parent else section_id

    def key(self, section_id: str) -> frozenset[str]:
        return heading_words(self.by_id[section_id].heading)

    def text(self, section_id: str) -> str:
        """Section text without its own number or label, whitespace collapsed."""
        return " ".join(_LEADING_LABEL.sub("", self.by_id[section_id].text, count=1).split())

    def own_text(self, section_id: str) -> str:
        """Text before the first child clause (the whole text for a leaf)."""
        text = self.by_id[section_id].text
        kids = self.children[section_id]
        cut = text.find(self.by_id[kids[0]].text) if kids else -1
        own = text[:cut] if cut > 0 else text
        return " ".join(_LEADING_LABEL.sub("", own, count=1).split())

    def descendants(self, section_id: str) -> list[str]:
        out, stack = [], list(reversed(self.children[section_id]))
        while stack:
            out.append(stack.pop())
            stack.extend(reversed(self.children[out[-1]]))
        return out


class Aligner:
    def __init__(self, threshold: float = 0.80, model_name: str = DEFAULT_MODEL,
                 embed: Embedder | None = None):
        """threshold: minimum cosine similarity for an embedding match.

        embed: optional texts -> vectors function (tests pass a stub); by default the
        sentence-transformers model loads the first time the embedding pass has work.
        """
        self.threshold = threshold
        self.model_name = model_name
        self._embed = embed
        self._embed_failed = False

    def align(self, template: SegmentedDocument,
              executed: SegmentedDocument) -> list[Alignment]:
        """One Alignment for every template section and every executed section.

        Confidence is 1.0 for the same number with the same caption or the same caption;
        otherwise the caption similarity or wording similarity that confirmed the pair, or
        the cosine similarity for an embedding match; 0.0 for ADDED and REMOVED.
        """
        self._t, self._e = _Tree(template), _Tree(executed)
        self._pairs: dict[str, tuple[str, AlignMethod, float]] = {}  # template id -> match
        self._taken: dict[str, str] = {}                             # executed id -> template id
        self._queue: deque[tuple[str | None, str | None]] = deque([(None, None)])
        self._vectors: dict[tuple[str, str], np.ndarray] = {}
        self._wordings: dict[tuple[str, str], float] = {}
        # weakest evidence last; whenever a step pairs anything, start over, so the
        # children of new pairs are matched by number and caption first
        steps = [self._match_moved_headings,
                 lambda: self._match_nested_headings(fuzzy=False),
                 self._match_children_by_embeddings,
                 self._match_nested_by_embeddings,
                 lambda: self._match_nested_headings(fuzzy=True),
                 self._match_moved_by_embeddings]
        while True:
            self._match_children_by_rules()
            if not any(step() for step in steps):
                break
        alignments = self._alignments()
        counts = defaultdict(int)
        for a in alignments:
            counts[a.method.value if a.method else
                   ("removed" if a.executed_section is None else "added")] += 1
        log.info("%s vs %s: %s", executed.doc_id, template.doc_id, dict(counts))
        return alignments

    # --- bookkeeping ---------------------------------------------------------------

    def _free_t(self, ids: list[str]) -> list[str]:
        return [i for i in ids if i not in self._pairs]

    def _free_e(self, ids: list[str]) -> list[str]:
        return [i for i in ids if i not in self._taken]

    def _pair(self, t_id: str, e_id: str, method: AlignMethod, confidence: float) -> None:
        self._pairs[t_id] = (e_id, method, round(confidence, 3))
        self._taken[e_id] = t_id
        self._queue.append((t_id, e_id))  # their children are matched next

    def _wording(self, t_id: str, e_id: str) -> float:
        """Wording similarity of the whole sections or, if better, of their own text."""
        if (t_id, e_id) not in self._wordings:
            score = wording_similarity(self._t.text(t_id), self._e.text(e_id))
            own_t, own_e = self._t.own_text(t_id), self._e.own_text(e_id)
            if min(len(own_t.split()), len(own_e.split())) >= _OWN_TEXT_MIN_WORDS:
                score = max(score, wording_similarity(own_t, own_e))
            self._wordings[(t_id, e_id)] = score
        return self._wordings[(t_id, e_id)]

    # --- passes 1 and 2 ------------------------------------------------------------

    def _match_children_by_rules(self) -> None:
        """Numbers, then captions, among the children of each newly paired parent pair."""
        while self._queue:
            t_parent, e_parent = self._queue.popleft()
            t_ids = self._free_t(self._t.children[t_parent])
            e_ids = self._free_e(self._e.children[e_parent])
            if t_ids and e_ids:
                self._match_numbers(t_ids, e_ids)
                self._match_headings(self._free_t(t_ids), self._free_e(e_ids))

    def _match_numbers(self, t_ids: list[str], e_ids: list[str]) -> None:
        e_by_label = {self._e.label(i): i for i in e_ids}
        for t_id in t_ids:
            e_id = e_by_label.get(self._t.label(t_id))
            if e_id is None:
                continue
            t_key, e_key = self._t.key(t_id), self._e.key(e_id)
            if t_key and t_key == e_key:
                self._pair(t_id, e_id, AlignMethod.NUMBER, 1.0)
                continue
            # a free sibling with exactly this caption is the better partner (pass 2)
            if (t_key and any(self._e.key(o) == t_key for o in self._free_e(e_ids))
                    or e_key and any(self._t.key(o) == e_key for o in self._free_t(t_ids))):
                continue
            caption = heading_similarity(self._t.by_id[t_id].heading,
                                         self._e.by_id[e_id].heading)
            wording = self._wording(t_id, e_id)
            if caption is not None and caption >= _HEADING_MIN:
                self._pair(t_id, e_id, AlignMethod.NUMBER, caption)
            elif wording >= _TEXT_MIN and not self._reads_more_like_sibling(
                    t_id, e_id, t_ids, e_ids, wording):
                self._pair(t_id, e_id, AlignMethod.NUMBER, wording)
            else:
                log.debug("%s / %s: same number, captions %.2f, wording %.2f", t_id, e_id,
                          caption or 0.0, wording)

    def _reads_more_like_sibling(self, t_id: str, e_id: str, t_ids: list[str],
                                 e_ids: list[str], wording: float) -> bool:
        """Whether either section reads clearly more like another free sibling, as when
        a clause was inserted ahead of it (the agreement's 12(a)(xxv) is the template's
        12(a)(xxvi)); the embedding pass then pairs it."""
        better = wording + _BETTER_MARGIN
        if better > 1.0:
            return False
        return (any(self._wording(t_id, o) >= better for o in self._free_e(e_ids) if o != e_id)
                or any(self._wording(o, e_id) >= better for o in self._free_t(t_ids)
                       if o != t_id))

    def _match_headings(self, t_ids: list[str], e_ids: list[str],
                        max_level_gap: int = 0, fuzzy: bool = True) -> None:
        """Equal captions (unique within each list), then if fuzzy the closest captions."""
        def ok(t: str, e: str) -> bool:
            return abs(self._t.level(t) - self._e.level(e)) <= max_level_gap

        t_keys = {i: self._t.key(i) for i in t_ids if self._t.key(i)}
        e_keys = {i: self._e.key(i) for i in e_ids if self._e.key(i)}
        t_count, e_count = _counts(t_keys.values()), _counts(e_keys.values())
        for t_id, key in t_keys.items():
            same = [e for e, k in e_keys.items() if k == key and e not in self._taken]
            if same and t_count[key] == 1 and e_count[key] == 1 and ok(t_id, same[0]):
                self._pair(t_id, same[0], AlignMethod.HEADING, 1.0)
        if not fuzzy:
            return
        scores = [(sim, t, e)
                  for t in self._free_t(list(t_keys)) for e in self._free_e(list(e_keys))
                  if ok(t, e) and (sim := heading_similarity(self._t.by_id[t].heading,
                                                             self._e.by_id[e].heading))
                  and sim >= _HEADING_MIN]
        self._greedy(scores, AlignMethod.HEADING)

    def _match_moved_headings(self) -> bool:
        """A caption found once among the unmatched sections of each document, at the
        same level: the clause moved to another section."""
        before = len(self._pairs)
        t_ids, e_ids = self._free_t(self._t.order), self._free_e(self._e.order)
        t_unique, e_unique = _unique_keys(self._t, t_ids), _unique_keys(self._e, e_ids)
        for key, t_id in t_unique.items():
            e_id = e_unique.get(key)
            if e_id and self._t.level(t_id) == self._e.level(e_id):
                self._pair(t_id, e_id, AlignMethod.HEADING, 1.0)
        return len(self._pairs) > before

    def _match_nested_headings(self, fuzzy: bool) -> bool:
        """Captions up to one level apart inside a paired section, deepest pairs first:
        the template's 15(g)(i)(D) "Litigation" is an agreement's 15(g)(ii) "Litigation"."""
        before = len(self._pairs)
        pairs = sorted(self._pairs.items(), key=lambda p: -self._t.level(p[0]))
        for t_id, (e_id, _, _) in pairs:
            t_ids = self._free_t(self._t.descendants(t_id))
            e_ids = self._free_e(self._e.descendants(e_id))
            if t_ids and e_ids:
                self._match_headings(t_ids, e_ids, max_level_gap=1, fuzzy=fuzzy)
        return len(self._pairs) > before

    # --- pass 3 --------------------------------------------------------------------

    def _match_children_by_embeddings(self) -> bool:
        """Among the children of each paired parent pair; the same number or similar
        wording must back up the topic match."""
        before = len(self._pairs)
        parents = [(None, None)] + [(t, e) for t, (e, _, _) in sorted(
            self._pairs.items(), key=lambda p: self._t.index[p[0]])]
        for t_parent, e_parent in parents:
            if not self._match_embeddings(
                    self._free_t(self._t.children[t_parent]),
                    self._free_e(self._e.children[e_parent]),
                    lambda t, e: (self._t.label(t) == self._e.label(e)
                                  or self._wording(t, e) >= _TEXT_MIN)):
                break
        return len(self._pairs) > before

    def _match_nested_by_embeddings(self) -> bool:
        """Up to one level apart inside a paired section, deepest pairs first, with
        similar wording: the template's 21(c)(ii) is an agreement's 21(d)."""
        before = len(self._pairs)
        for t_id, (e_id, _, _) in sorted(self._pairs.items(),
                                         key=lambda p: -self._t.level(p[0])):
            if not self._match_embeddings(
                    self._free_t(self._t.descendants(t_id)),
                    self._free_e(self._e.descendants(e_id)),
                    lambda t, e: (abs(self._t.level(t) - self._e.level(e)) <= 1
                                  and self._wording(t, e) >= _TEXT_MIN)):
                break
        return len(self._pairs) > before

    def _match_moved_by_embeddings(self) -> bool:
        """Anywhere, up to one level apart, when the wording is close: a clause moved to
        an unrelated section (the template's 15(b)(ii) is an agreement's 20(d))."""
        before = len(self._pairs)
        self._match_embeddings(
            self._free_t(self._t.order), self._free_e(self._e.order),
            lambda t, e: (abs(self._t.level(t) - self._e.level(e)) <= 1
                          and self._wording(t, e) >= _MOVED_TEXT_MIN))
        return len(self._pairs) > before

    def _match_embeddings(self, t_ids: list[str], e_ids: list[str],
                          confirmed: Callable[[str, str], bool]) -> bool:
        """Greedy cosine matching above the threshold for pairs that pass confirmed().
        False if no embedder is available."""
        if not t_ids or not e_ids:
            return True
        t_vecs, e_vecs = self._vectors_for("t", t_ids), self._vectors_for("e", e_ids)
        if t_vecs is None or e_vecs is None:
            return False
        cosine = t_vecs @ e_vecs.T
        scores = [(float(cosine[a, b]), t_ids[a], e_ids[b])
                  for a, b in zip(*np.nonzero(cosine >= self.threshold))]
        self._greedy([(c, t, e) for c, t, e in scores if confirmed(t, e)],
                     AlignMethod.EMBEDDING)
        return True

    def _greedy(self, scores: list[tuple[float, str, str]], method: AlignMethod) -> None:
        """Pair from the highest score down; each section is used once."""
        for score, t_id, e_id in sorted(scores, key=lambda s: -s[0]):
            if t_id not in self._pairs and e_id not in self._taken:
                self._pair(t_id, e_id, method, score)

    def _vectors_for(self, side: str, ids: list[str]) -> np.ndarray | None:
        tree = self._t if side == "t" else self._e
        missing = [i for i in ids if (side, i) not in self._vectors]
        if missing:
            embed = self._embedder()
            if embed is None:
                return None
            for i, vector in zip(missing, embed([tree.text(i) for i in missing])):
                self._vectors[(side, i)] = vector
        return np.stack([self._vectors[(side, i)] for i in ids])

    def _embedder(self) -> Embedder | None:
        if self._embed is None and not self._embed_failed:
            try:
                self._embed = load_embedder(self.model_name)
            except ImportError:
                self._embed_failed = True
                log.warning("sentence-transformers is not installed: skipping the embedding "
                            "pass, so more sections are reported as added or removed")
        return self._embed

    # --- output --------------------------------------------------------------------

    def _alignments(self) -> list[Alignment]:
        """Template order; an ADDED section goes after the template section paired with
        the executed section before it."""
        t_index = self._t.index
        keyed = []
        for t_id in self._t.order:
            section = self._t.by_id[t_id]
            if t_id in self._pairs:
                e_id, method, confidence = self._pairs[t_id]
                alignment = Alignment(section, self._e.by_id[e_id], method, confidence)
            else:
                alignment = Alignment(section, None, None, 0.0)
            keyed.append(((t_index[t_id], 0), alignment))
        anchor = -1
        for n, e_id in enumerate(self._e.order):
            if e_id in self._taken:
                anchor = t_index[self._taken[e_id]]
            else:
                keyed.append(((anchor, 1 + n), Alignment(None, self._e.by_id[e_id], None, 0.0)))
        return [alignment for _, alignment in sorted(keyed, key=lambda k: k[0])]


def _counts(keys) -> dict[frozenset[str], int]:
    counts: dict[frozenset[str], int] = defaultdict(int)
    for key in keys:
        counts[key] += 1
    return counts


def _unique_keys(tree: _Tree, ids: list[str]) -> dict[frozenset[str], str]:
    """Caption key -> id, for captions that occur once among ids."""
    counts = _counts(tree.key(i) for i in ids)
    return {tree.key(i): i for i in ids if tree.key(i) and counts[tree.key(i)] == 1}
