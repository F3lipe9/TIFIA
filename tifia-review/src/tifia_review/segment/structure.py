"""StructureCheck: compare the top-level sections found with the expected list.

    section coverage   found / expected (expected = TOC entries, or the template's)
    order              each recovered section's number lies between its numbered neighbours
    text coverage      non-space characters in the top-level sections / in the body after
                       the TOC (target min_text_coverage)
    duplicates         no top-level section id twice

Status: "complete" when the normal segmenter found everything, "recovered" when recovery
found the rest, "degraded" when anything is still missing or a check fails. A missing
section's text stays in the section before it, so nothing is lost.
"""

from tifia_review.models import Section, StructureReport, TocEntry


def _chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


class StructureCheck:
    def __init__(self, min_text_coverage: float = 0.98):
        self.min_text_coverage = min_text_coverage

    def check(self, sections: list[Section], expected: list[TocEntry], expected_from: str,
              body_text: str, recovered: list[str] = (), warnings: list[str] = ()
              ) -> StructureReport:
        top = [s for s in sections if s.level == 1]
        numbered = [s for s in top if s.section_id.isdigit()]
        present = {s.section_id for s in top}
        expected_ids = [e.section_id for e in expected]
        recovered = [r for r in recovered if r in present]

        problems = []
        seen, dupes = set(), []
        for s in top:
            if s.section_id in seen:
                dupes.append(s.section_id)
            seen.add(s.section_id)
        if dupes:
            problems.append(f"duplicate sections: {', '.join(dupes)}")
        for i, s in enumerate(numbered):
            if s.section_id not in recovered:
                continue
            n = int(s.section_id)
            before = int(numbered[i - 1].section_id) if i else None
            after = int(numbered[i + 1].section_id) if i + 1 < len(numbered) else None
            if (before is not None and before >= n) or (after is not None and after <= n):
                problems.append(f"recovered section {n} is out of order "
                                f"(between {before} and {after})")
        body = _chars(body_text)
        coverage = sum(_chars(s.text) for s in top) / body if body else 1.0
        if coverage < self.min_text_coverage:
            problems.append(f"text coverage {coverage:.1%} is below "
                            f"{self.min_text_coverage:.0%}")

        if expected_ids:
            missing = [i for i in expected_ids if i not in present]
            found = len(expected_ids) - len(missing)
            total = len(expected_ids)
        else:
            expected_from, missing, found, total = "none", [], len(numbered), len(numbered)
        if missing or problems:
            status = "degraded"
        elif recovered:
            status = "recovered"
        else:
            status = "complete"
        return StructureReport(
            expected=total, found=found, recovered=list(recovered), missing=missing,
            text_coverage=coverage, status=status, expected_from=expected_from,
            numbered=sum(1 for s in numbered if s.section_id not in recovered),
            problems=problems, warnings=list(warnings))
