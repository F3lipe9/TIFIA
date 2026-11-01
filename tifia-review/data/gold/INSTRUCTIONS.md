# Answer key: instructions for the advisor (Phase 0, week 8)

Please review each agreement against the template and record **every real deviation** in a
copy of `gold_template.csv`, one file per agreement, named after the agreement (for example
`tla_2021_drle.csv`). Please do this **before looking at any report from the tool**, so
your answers are independent of it. In Excel, save as "CSV UTF-8" if you can.

Record wording changes too, marked `substantive` = `no`. The tool reports them, and
leaving them out makes it look less precise than it is.

One row per deviation:

| Column | What to enter |
|---|---|
| template_section | The template section, e.g. `12(a)(xii)` or `Exhibit D`. Leave empty if the text is only in the agreement. |
| agreement_section | The agreement section. Leave empty if the text was removed. |
| agreement_page | The printed page number in the agreement (the template page for a removal). |
| change | `added`, `removed` or `modified`. |
| excerpt | A short quote, up to 25 words, of the changed text: from the agreement, or from the template for a removal. |
| category | One of the agreed categories (see `config.yaml`, `taxonomy`), or `unclear`. |
| substantive | `yes` or `no`, using the agreed definition of a substantive change. |
| notes | Anything worth saying, e.g. why it matters or whom it favors. |

Not deviations, so please leave them out: blanks the template leaves to be filled in
(names, dates, amounts in brackets), optional bracketed passages kept or dropped as the
template allows, and sections that are only renumbered.

The tool counts a match when its deviation is in the same section and shares at least half
of its text with your excerpt, so quote the changed words rather than the surrounding
paragraph.
