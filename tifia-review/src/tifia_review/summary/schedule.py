"""ScheduleParser: a digest of the TIFIA Debt Service exhibit (first and final payment,
number of payments and frequency, total principal, largest year), checked against the
loan amount. No LLM.

The table is read with PyMuPDF's find_tables(). Columns are found by their header text:
a date column, a principal column (both required), and interest and total columns if
present. Every row with a payment date must parse completely, or there is no digest at
all: a partly parsed schedule is never shown. The principal column must add up to the
loan amount (to the dollar); if it does not, the summary shows a warning instead of the
digest, which catches table-reading errors. A page that only says "[to be inserted]" or
"[Attached.]" is not inserted; no pages, or no table, is not found.
"""

import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import median

from tifia_review.models import ScheduleDigest, ValueStatus
from tifia_review.summary.exhibits import ExhibitPages
from tifia_review.summary.text import has_blank, not_inserted

_DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%b. %d, %Y",
                 "%d-%b-%y", "%d-%b-%Y"]
_AMOUNT = re.compile(r"^\(?\$?\s*-?[\d,]+(?:\.\d+)?\)?$")
_DASH = {"-", "–", "—", "$-", "$ -", ""}
# "$[310,194,800]" or "$310,194,800" in the loan amount section
LOAN_AMOUNT = re.compile(r"\$\s*\[?\s*([\d,]+(?:\.\d{2})?)\s*\]?")


def loan_amount(text: str) -> float | None:
    """The first dollar amount in the loan amount section."""
    m = LOAN_AMOUNT.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def parse_date(cell: str) -> date | None:
    cell = " ".join(cell.split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cell, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(cell: str) -> float | None:
    """'$1,234.56' -> 1234.56; '(5.00)' -> -5.0; '-' or '' -> 0.0; else None."""
    cell = " ".join(cell.split())
    if cell in _DASH:
        return 0.0
    if not _AMOUNT.match(cell):
        return None
    negative = cell.startswith("(") or "-" in cell
    value = float(re.sub(r"[^\d.]", "", cell))
    return -value if negative else value


def frequency(dates: list[date]) -> str | None:
    if len(dates) < 2:
        return None
    gaps = [(b.year - a.year) * 12 + b.month - a.month for a, b in zip(dates, dates[1:])]
    months = median(gaps)
    return {1: "monthly", 3: "quarterly", 6: "semi-annual", 12: "annual"}.get(
        round(months), f"about every {months:g} months")


class ScheduleParser:
    def parse(self, pdf: Path | None, exhibit: ExhibitPages,
              loan: float | None) -> ScheduleDigest:
        if pdf is None or not exhibit.pages:
            return ScheduleDigest(ValueStatus.NOT_FOUND, [], exhibit.letter,
                                  problem="the debt service exhibit is not in this PDF")
        rows = self._rows(pdf, exhibit.pages)
        if not rows:
            status = (ValueStatus.NOT_INSERTED if not_inserted(exhibit.text)
                      or (has_blank(exhibit.text) and len(exhibit.text.split()) < 80)
                      else ValueStatus.NOT_FOUND)
            return ScheduleDigest(status, exhibit.pages, exhibit.letter,
                                  problem="the exhibit has no table to read")
        try:
            payments = self._payments(rows)
        except ValueError as e:
            return ScheduleDigest(ValueStatus.NOT_FOUND, exhibit.pages, exhibit.letter,
                                  problem=f"the table could not be read: {e}")
        dates = [p[0] for p in payments]
        principal = round(sum(p[1] for p in payments), 2)
        by_year: dict[str, float] = defaultdict(float)
        for when, prin, interest, total in payments:
            by_year[str(when.year)] += total if total is not None else prin + (interest or 0.0)
        largest = max(by_year.items(), key=lambda kv: kv[1]) if by_year else None
        matches = None if loan is None else abs(principal - loan) < 1.0
        return ScheduleDigest(
            ValueStatus.FOUND, exhibit.pages, exhibit.letter, dates[0].isoformat(),
            dates[-1].isoformat(), len(payments), frequency(dates), principal,
            (largest[0], round(largest[1], 2)) if largest else None, matches,
            "" if matches is not False else
            f"principal adds up to ${principal:,.2f}, not the loan amount ${loan:,.2f}")

    def _rows(self, pdf: Path, pages: list[int]) -> list[list[str]]:
        import pymupdf
        rows: list[list[str]] = []
        with pymupdf.open(pdf) as doc:
            for p in pages:
                page = doc[p - 1]
                for table in page.find_tables().tables:
                    # each cell's words read from its own area: table.extract() can move
                    # the commas and decimal points of small amounts out of place
                    rows.extend([" ".join(w[4] for w in page.get_text("words", clip=cell))
                                 if cell else "" for cell in row.cells]
                                for row in table.rows)
        return rows

    def _payments(self, rows: list[list[str]]
                  ) -> list[tuple[date, float, float | None, float | None]]:
        columns = None
        payments = []
        for row in rows:
            cells = [" ".join(c.split()) for c in row]
            found = _columns(cells)
            if found is not None:
                columns = found          # a header row, maybe repeated on each page
                continue
            if columns is None:
                continue
            when = parse_date(cells[columns["date"]]) if columns["date"] < len(cells) else None
            if when is None:
                continue                 # a total row, a note, a blank line
            values = {}
            for name in ("principal", "interest", "total"):
                if name in columns:
                    value = parse_amount(cells[columns[name]]) if columns[name] < len(cells) \
                        else None
                    if value is None:
                        raise ValueError(f"{name} for {when.isoformat()} is "
                                         f"{cells[columns[name]]!r}")
                    values[name] = value
            payments.append((when, values["principal"], values.get("interest"),
                             values.get("total")))
        if columns is None:
            raise ValueError("no header row with a date column and a principal column")
        if len(payments) < 2:
            raise ValueError(f"only {len(payments)} payment row(s)")
        payments.sort(key=lambda p: p[0])
        return payments


def _columns(cells: list[str]) -> dict[str, int] | None:
    """Column positions if cells is a header row (a date and a principal column)."""
    found: dict[str, int] = {}
    for i, cell in enumerate(cells):
        low = cell.casefold()
        if "date" in low and "date" not in found:
            found["date"] = i
        elif "principal" in low and "principal" not in found:
            found["principal"] = i
        elif "interest" in low and "interest" not in found:
            found["interest"] = i
        elif ("total" in low or "debt service" in low) and "total" not in found:
            found["total"] = i
    return found if {"date", "principal"} <= found.keys() else None
