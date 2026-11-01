"""Two-page executive summary of one agreement (Summary_Report.md, v2).

One row per topic: a plain-English line written by the LLM from that topic's sections
only, with a quote checked word for word against the agreement; whether the topic was
changed from the template, counted by code from the labeled deviations; and where to look.
Code decides which deviations matter and where they belong; the LLM only writes the
lines. Anything it cannot back with a verified quote falls back to "See agreement §X".

    mapping.TopicMapper       deviations -> topics; "other significant changes"
    topics.TopicExtractor     topic sections -> summary line + verified quote (LLM)
    exhibits.ExhibitLocator   the TIFIA Debt Service exhibit's pages in a PDF
    schedule.ScheduleParser   that exhibit's table -> digest, checked against the loan amount
    bottom_line.BottomLineWriter  2-3 sentences from the change counts only (LLM)
    builder.SummaryBuilder    HTML -> PDF, at most max_pages pages
"""
