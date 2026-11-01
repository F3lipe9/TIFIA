"""Classifier prompts: the system prompt with the taxonomy filled in, the reply schema, and
the user message for one batch of deviations.

Any change here changes the classifier's prompt version, so cached labels from the old
prompt are not reused.
"""

import json

UNCLEAR = "unclear"  # always allowed in addition to the taxonomy

SYSTEM_PROMPT = """\
You label the differences between an executed TIFIA loan agreement and the form of loan \
agreement published by the U.S. Department of Transportation (the template) that it was \
drafted from. An attorney uses your labels to review the agreement, so be accurate and plain.

The differences were found by comparing the two texts. Treat each one as a real \
difference: do not decide whether something changed, and do not comment on anything \
outside the text you are given. Label every difference you receive and nothing else.

Each difference has an id; a change type ("added": text only in the agreement, \
"removed": text only in the template, "modified": template text replaced by agreement \
text); the section where it appears in each document; the defined term it belongs to, if \
it is part of a definition; and the template and agreement text.

For each difference give:
- category: the category below that fits best, or "unclear" if none fits or the text is \
too fragmentary to tell.
- substantive: true or false, as defined below.
- explanation: one plain-English sentence saying what changed and, where the text makes it \
clear, what that means for the borrower or the TIFIA lender. Do not speculate beyond the \
text.
- evidence: a passage copied exactly from the agreement text that shows the change. If \
the difference has no agreement text, copy it from the template text instead. One \
continuous passage of at most 25 words, with no ellipses, brackets or edits.

Categories:
{categories}

Substantive: {substantive}

Answer with a JSON object {{"labels": [...]}} holding one label per id, each with the \
fields id, category, substantive, explanation and evidence."""


def build_system_prompt(taxonomy: dict[str, str], substantive: str) -> str:
    categories = "\n".join(f"- {name}: {definition}" for name, definition in taxonomy.items())
    return SYSTEM_PROMPT.format(categories=categories, substantive=" ".join(substantive.split()))


def label_schema(categories: list[str]) -> dict:
    """JSON Schema of the reply: one label per deviation id."""
    label = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "category": {"type": "string", "enum": categories},
            "substantive": {"type": "boolean"},
            "explanation": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["id", "category", "substantive", "explanation", "evidence"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"labels": {"type": "array", "items": label}},
            "required": ["labels"], "additionalProperties": False}


def render_batch(items: list[dict]) -> str:
    """The user message for one batch. An item with "previous_problem" failed a check
    on the last attempt."""
    retry = any("previous_problem" in item for item in items)
    head = (f"Label these {len(items)} differences."
            + (" Some were labeled before but failed a check, given as previous_problem;"
               " fix that in the new label." if retry else ""))
    return head + "\n\n" + json.dumps(items, ensure_ascii=False, indent=1)
