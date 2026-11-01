# Taxonomy draft for the advisor meeting (Phase 0, week 6)

The classifier gives each deviation a **category**, a **substantive** flag, a one-sentence
**explanation** and an **evidence** quote. The categories and the definition of
"substantive" below are drafts in `config.yaml` (`taxonomy`, `substantive`). They need the
advisor's corrections before the labels mean anything. Changing them in `config.yaml`
changes the prompt, so cached labels are not reused.

For scale: DRLE against the 2023 project-revenue template has 730 deviations. 289 are
modified, 196 added and 154 removed; those 639 go to the LLM. The other 91 are labeled
without it: 65 fill-ins (template blanks filled in, or optional bracketed passages
kept or dropped) and 26 renumbered sections.

## Draft categories

"unclear" is always allowed, and an unclear label is flagged for review.

| Category | Draft definition |
|---|---|
| Definitions | A defined term was added, removed or redefined, in Section 1 or wherever the term is defined. A changed definition changes every provision that uses the term. |
| Payment terms | The loan amount, interest rate, disbursements, payment dates, amortization, prepayment and fees, and the security, pledged revenues and flow of funds that repay the loan. |
| Conditions precedent | What must happen before the agreement takes effect or before a disbursement, including documents, certificates and legal opinions to be delivered. |
| Covenants | What the borrower must or must not do while the loan is outstanding: affirmative and negative covenants, including reporting, notices, insurance, accounts, additional debt and compliance with federal requirements. |
| Events of default | Events of default, cure periods, remedies, acceleration and the lender's rights after a default. |
| Other | Anything else, including representations and warranties, general provisions (notices, governing law, amendments, counterparts) and forms in the exhibits. |

## Draft definition of "substantive"

> A change is substantive if it changes what a party must do, may do or will receive: an
> obligation, right, condition, deadline, amount, rate, threshold or remedy, or the scope
> of a defined term. Changes that leave the meaning the same are not substantive: wording,
> style, punctuation, cross-references and numbering.

## Ten real deviations to walk through

These come from the DRLE agreement (`outputs/diff/tla_2021_drle__vs__...txt`). Pages are
PDF page numbers. The suggested labels are Claude's starting points for discussion, not
the advisor's answers.

1. **d0050, Definitions, Section 1 "Capitalized Interest Period" (PDF p.10 / p.11), modified.**
   Template: the period ends on "the first day of the initial Payment Period". Agreement:
   it ends on "the earlier of (a) the first day of the initial Payment Period and (b) the
   occurrence of a Bankruptcy Related Event".
   *Suggested: Definitions, substantive.*
2. **d0024, Definitions, Section 1 "Anticipated TIFIA Loan Disbursement Schedule", modified.**
   The only change is "Exhibit B" becoming "Exhibit B (Anticipated TIFIA Loan Disbursement
   Schedule)". *Suggested: Definitions, not substantive (it only adds the caption to a
   cross-reference).*
3. **d0351, template 10(a)(ii) (PDF p.43), removed.** Template: mandatory prepayment "in
   the amount of any Net Loss Proceeds". The agreement has no such prepayment.
   *Suggested: Payment terms, substantive.*
4. **d0387, agreement 12(a)(xxiv) (PDF p.49), added.** "No Material Adverse Effect … shall
   have occurred since the date the Borrower submitted the Application to the TIFIA
   Lender." This is a new condition to effectiveness. *Suggested: Conditions precedent,
   substantive.*
5. **d0557, agreement 16(g) (PDF p.71), added.** "No Prohibited Business. The Borrower
   shall not at any time engage in any business or activity other than as authorized by
   the laws of the State." *Suggested: Covenants, substantive.*
6. **d0593, template 19(a)(xiv) (PDF p.82), removed.** The event of default for repeal or
   amendment of the Authorizing Legislation is gone. *Suggested: Events of default,
   substantive.*
7. **d0442, agreement 13(z) (PDF p.58), added.** A new representation, "Other Prohibited
   Transactions", about dealings with other governmental authorities. *Suggested: Other
   (representations fall under Other in this draft; see question 1).*
8. **d0608, 21(a)(i) (PDF p.85 / p.84), modified.** The Financial Plan is prepared under
   "recognized financial reporting standards, such as those in the Guide for Prospective
   Financial Information" and must meet "the FTA Project Management Oversight
   Regulations". The template says GAAP, with a bracketed FHWA requirement.
   *Suggested: Covenants, substantive.*
9. **d0302, Section 2 Interpretation, modified.** "as well as any table of contents"
   becomes "as well as its table of contents". *Suggested: Other, not substantive.*
10. **d0664, Section 36 (PDF p.95 / p.93), modified.** "Section 9(f) (Manner of Payment)"
    becomes "Section 9(e)" (the agreement renumbered Section 9), and the agreement drops
    "with" ("in accordance Section 9(e)"). *Suggested: Other, not substantive.*

## Questions for the advisor

1. **Categories.** What is missing, and what should merge? In this draft, four large
   groups fall into broad buckets:
   - Representations and warranties (Section 13, about 30 clauses) go to Other.
   - Security and flow of funds (Section 8) go to Payment terms.
   - Reporting and notices (15(g), Sections 20-22) go to Covenants.
   - Federal requirements (NEPA, Buy America, lobbying, Title VI) go to Covenants or
     Conditions precedent.

   Should any of these be its own category?
2. **Substantive edge cases.**
   - Are cross-reference and caption updates (examples 2 and 10) non-substantive?
   - DRLE adds 138 defined terms, many of them specific to the deal, such as "1999 Prior
     Bonds" for the borrower's existing debt. Are new definitions like these substantive?
   - Some template provisions don't fit the deal, such as a traffic and revenue study for
     a loan repaid from taxes. Is dropping one substantive?
   - The diff does not treat filling a blank or dropping a bracketed optional passage as a
     negotiated change. One example is the bracketed "Appreciated Value" definition. Is
     that right?
3. **Explanations.** Is one sentence the right level of detail? Should it say which side a
   change favors?
4. **Accuracy threshold** (below).

## Accuracy threshold (agree in writing before week 8)

The proposal from `PHASE0.md` and `config.yaml` (`evaluation`) is recall ≥ 90% and
precision ≥ 70% on substantive deviations.

- Agreed recall: ____  Agreed precision: ____
- Agreed by: ____  Date: ____  Where recorded (email or notes): ____

## After the meeting

1. Put the final names and definitions in `config.yaml` (`taxonomy`, `substantive`), and
   update `evaluation` if the threshold changed.
2. Run `python scripts/classify_documents.py tla_2021_drle`.
3. Read every label in `outputs/classify/tla_2021_drle__vs__...csv`, filling in
   `category_ok`, `substantive_ok`, `explanation_ok` and `notes`. This is week 6, step 5.
