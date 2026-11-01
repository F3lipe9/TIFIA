# TIFIA

Software that reviews **TIFIA loan agreements** against the U.S. Department of
Transportation's standard template, and tells a reviewer what was changed, where, and
whether it matters.

> **Status:** Phase 0 prototype. It runs locally on public documents and is being tested
> against an advisor's own review before anyone relies on it. Its output is for
> professional review, not legal or financial advice.

## The problem

TIFIA (the Transportation Infrastructure Finance and Innovation Act) lends billions of
dollars a year to highways, transit, ports and other transportation projects through the
DOT's Build America Bureau. Every loan starts from a published form of loan agreement,
the template, and is negotiated from there.

Each executed agreement runs to 100–150 pages. To know what a borrower actually agreed to,
someone has to compare it with the template clause by clause: what was added, removed or
reworded, whether a changed definition quietly changes every provision that uses it, and
which of hundreds of differences are real negotiated terms rather than filled-in blanks
or renumbered sections. Today that is done by hand, by expensive people.

## Who it is for

- **Municipal and project finance advisors** to public borrowers (transit agencies, toll
  authorities, states and cities). They are the Phase 0 customer: they review these
  agreements on every deal, and the tool is built to save them that time.
- **Their clients**, the agency or borrower executives who need the deal in plain English.
  The two-page summary is a deliverable the advisor can hand to them.
- Later, possibly lenders' and borrowers' counsel and the people who review many
  agreements across a portfolio.

## What it produces

For each agreement, compared with the template it was drafted from:

1. **A deviation report (PDF).** Every difference the software found, with section and
   page citations on both documents, the changed words marked side by side, a category
   (payment terms, covenants, events of default, ...), whether the change is substantive,
   a one-sentence plain-English explanation, and a quote from the agreement backing it.
   Defined-term changes come first, because they change everything that uses them.
2. **A two-page executive summary (PDF).** One row per topic (the project, borrowing
   authority, loan amount, interest rate, payment schedule, events of default, audit,
   oversight, notices), each with what the agreement says, whether that topic was changed
   from the template, and where to look. Draft blanks are reported as drafts; nothing is
   guessed.

## How it can be trusted

The software never decides on its own what changed: the differences come from comparing
the texts, and a language model (Claude) only labels and explains them. Every sentence the
model writes must be backed by a quote that is checked, by code, word for word against the
agreement; every number it writes must appear in the agreement. Anything that fails is
marked "needs review" or "see agreement §X" instead of being shown as fact.

Accuracy is measured, not assumed: an advisor labels agreements independently, before
seeing any report, and the tool is scored against that answer key, with one agreement
held out until the end.

## Phase 0

A ten-week test of whether this is worth building, at about five hours a week. It ends in
a go/no-go decision based on:

- accuracy against the advisor's answer key,
- whether advisors say manual review is a major time cost, and
- whether at least one names a price (a letter of intent or a pilot).

The plan is in [`PHASE0.md`](PHASE0.md), what is left to do in [`do.md`](do.md), and the
executive summary's design in [`Summary_Report.md`](Summary_Report.md).

## Repository

| Path | What it is |
|---|---|
| [`tifia-review/`](tifia-review/) | The software: pipeline, tests, configuration. Setup and usage are in [its README](tifia-review/README.md). |
| [`PHASE0.md`](PHASE0.md) | Week-by-week Phase 0 plan |
| [`do.md`](do.md) | What is done and what remains |
| [`Summary_Report.md`](Summary_Report.md) | Plan for the two-page executive summary |

## Team

Felipe, UMD Computer Science (Founder and engineere) 
Neil, UMD Pre-Law (Founder and Works with SIBS giving a deep understanding 
Preston, Princeton (Business)  
Advisor works professionally with TIFIA agreements and SIBS.
