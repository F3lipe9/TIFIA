import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.helpers import FIXTURES, TEMPLATE, no_embeddings, segment_pdf
from tifia_review.aligner import Aligner
from tifia_review.classifier import FILL_IN_CATEGORY, RENUMBERED_CATEGORY, Classifier
from tifia_review.diff_engine import DiffEngine
from tifia_review.llm.client import (
    AnthropicClient, LLMClient, LLMError, Usage, make_client, model_id,
    parse_json_object,
)
from tifia_review.llm.prompts import UNCLEAR
from tifia_review.models import ChangeType, Deviation, Section, SegmentedDocument
from tifia_review.store import ResultCache

TAXONOMY = {"Covenants": "What the borrower must do.", "Other": "Anything else."}
SUBSTANTIVE = "Changes an obligation."
MODIFIED, ADDED, REMOVED = ChangeType.MODIFIED, ChangeType.ADDED, ChangeType.REMOVED


class FakeClient(LLMClient):
    """Answers each request with answer(items), items being the deviations sent."""
    model_id = "fake"

    def __init__(self, answer):
        super().__init__()
        self.answer = answer
        self.requests: list[list[dict]] = []

    def complete_json(self, system, user, schema=None):
        items = json.loads(user[user.index("\n["):])
        self.requests.append(items)
        self.usage.add(SimpleNamespace(input_tokens=100, output_tokens=10))
        return {"labels": self.answer(items)}


def good(item, **changes):
    """A label that passes: evidence is the first words of the text it must quote."""
    text = item.get("agreement_text") or item["template_text"]
    label = {"id": item["id"], "category": "Covenants", "substantive": True,
             "explanation": "The notice period changed.",
             "evidence": " ".join(text.split()[:6])}
    return {**label, **changes}


def dev(n: int, change=MODIFIED, template="The Borrower shall give notice within thirty "
        "(30) days.", executed="The Borrower shall give notice within ten (10) days.",
        section="15(g)") -> Deviation:
    return Deviation(f"d{n:04d}", change, None if change is ADDED else section,
                     None if change is REMOVED else section,
                     "" if change is ADDED else template, "" if change is REMOVED else executed,
                     10, 10)


def classifier(client, cache=None, **kwargs) -> Classifier:
    return Classifier(client, TAXONOMY, SUBSTANTIVE, cache, **kwargs)


# --- labels and checks -----------------------------------------------------------------

def test_valid_labels_pass_and_keep_order():
    client = FakeClient(lambda items: [good(i) for i in reversed(items)])
    deviations = [dev(1), dev(2, ADDED), dev(3, REMOVED)]
    labeled = classifier(client).classify(deviations)
    assert [ld.deviation for ld in labeled] == deviations
    assert all(not ld.needs_review and ld.category == "Covenants" for ld in labeled)
    assert labeled[0].evidence == "The Borrower shall give notice within"
    assert len(client.requests) == 1


def test_fill_ins_and_renumberings_are_labeled_without_the_llm():
    client = FakeClient(lambda items: pytest.fail("the LLM was called"))
    fill = Deviation("d0001", ChangeType.FILL_IN, "1", "1", "[Borrower]", "City", 1, 1)
    moved = Deviation("d0002", ChangeType.RENUMBERED, "13(dd)", "13(aa)", "13(dd)", "13(aa)",
                      60, 58)
    labeled = classifier(client).classify([fill, moved])
    assert [(ld.category, ld.substantive) for ld in labeled] == [
        (FILL_IN_CATEGORY, False), (RENUMBERED_CATEGORY, False)]
    assert "13(dd) is numbered 13(aa)" in labeled[1].explanation


def test_evidence_matches_after_normalization():
    executed = "the “Borrower’s knowledge” shall mean   actual knowledge"
    client = FakeClient(lambda items: [good(i, evidence='"the "Borrower\'s knowledge" '
                                                        'shall mean actual"')
                                       for i in items])
    (ld,) = classifier(client).classify([dev(1, executed=executed)])
    assert not ld.needs_review
    assert ld.evidence == 'the "Borrower\'s knowledge" shall mean actual'


def test_removal_evidence_is_checked_against_the_template_text():
    client = FakeClient(lambda items: [good(i, evidence="within thirty (30) days")
                                       for i in items])
    (ld,) = classifier(client).classify([dev(1, REMOVED)])
    assert not ld.needs_review


def test_bad_evidence_is_retried_with_the_problem():
    def answer(items):
        return [good(i, evidence="within twenty (20) days") if "previous_problem" not in i
                else good(i) for i in items]
    client = FakeClient(answer)
    c = classifier(client)
    (ld,) = c.classify([dev(1)])
    assert not ld.needs_review and len(client.requests) == 2
    assert "not copied word for word from the agreement text" in (
        client.requests[1][0]["previous_problem"])
    assert c.stats["retried"] == 1


@pytest.mark.parametrize("label, reason", [
    ({"category": "Payment terms"}, "is not one of the categories"),
    ({"substantive": "yes"}, "true or false"),
    ({"explanation": " "}, "explanation is empty"),
    ({"evidence": "notice"}, "2 to 25 words"),
    ({"evidence": "The Borrower shall give notice within twenty (20) days"}, "word for word"),
])
def test_label_failing_twice_needs_review(label, reason):
    client = FakeClient(lambda items: [good(i, **label) for i in items])
    (ld,) = classifier(client).classify([dev(1)])
    assert ld.needs_review and reason in ld.review_reason
    assert len(client.requests) == 2  # one retry


def test_unusable_reply_and_missing_ids_need_review():
    def broken(items):
        raise LLMError("reply is not valid JSON")
    (ld,) = classifier(FakeClient(broken)).classify([dev(1)])
    assert ld.needs_review and ld.category == UNCLEAR and ld.substantive
    assert "no usable reply" in ld.review_reason

    client = FakeClient(lambda items: [good(i) for i in items if i["id"] != "d0002"])
    labeled = classifier(client).classify([dev(1), dev(2)])
    assert [ld.needs_review for ld in labeled] == [False, True]
    assert [i["id"] for i in client.requests[1]] == ["d0002"]  # only the missing one again


def test_unclear_category_needs_review():
    client = FakeClient(lambda items: [good(i, category=UNCLEAR) for i in items])
    (ld,) = classifier(client).classify([dev(1)])
    assert ld.needs_review and "unclear" in ld.review_reason
    assert len(client.requests) == 1  # a valid answer: not retried


# --- batching, context, cache -----------------------------------------------------------

def test_batches_by_count_and_size():
    client = FakeClient(lambda items: [good(i) for i in items])
    classifier(client, batch_size=2).classify([dev(n) for n in range(5)])
    assert [len(r) for r in client.requests] == [2, 2, 1]
    client = FakeClient(lambda items: [good(i) for i in items])
    classifier(client, max_batch_chars=300).classify([dev(n) for n in range(3)])
    assert [len(r) for r in client.requests] == [1, 1, 1]


def test_items_carry_section_captions():
    doc = SegmentedDocument("t", [
        Section("15", "Affirmative Covenants", "", 1, 1),
        Section("15(g)", "Notice", "", 1, 1, level=2, parent_id="15"),
    ], {})
    client = FakeClient(lambda items: [good(i) for i in items])
    classifier(client).classify([dev(1)], doc, doc)
    item = client.requests[0][0]
    assert item["template_section"] == "15(g) (Affirmative Covenants > Notice)"
    assert item["agreement_section"] == item["template_section"]
    assert "template_text" in item and "agreement_text" in item


def test_cache_skips_the_llm_until_prompt_or_model_changes(tmp_path):
    cache = ResultCache(tmp_path / "cache.sqlite")
    client = FakeClient(lambda items: [good(i) for i in items])
    first = classifier(client, cache).classify([dev(1), dev(2)])
    again = classifier(client, cache).classify([dev(1), dev(2)])
    assert again == first and len(client.requests) == 1
    renamed = Deviation(**{**vars(dev(1)), "deviation_id": "d0099"})
    classifier(client, cache).classify([renamed])  # same text under another id: cached
    assert len(client.requests) == 1

    other_prompt = Classifier(client, {**TAXONOMY, "Fees": "Fees."}, SUBSTANTIVE, cache)
    other_prompt.classify([dev(1)])
    assert len(client.requests) == 2
    other_model = FakeClient(lambda items: [good(i) for i in items])
    other_model.model_id = "fake-2"
    classifier(other_model, cache).classify([dev(1)])
    assert len(other_model.requests) == 1


def test_failures_are_not_cached(tmp_path):
    cache = ResultCache(tmp_path / "cache.sqlite")
    client = FakeClient(lambda items: [good(i, category="Fees") for i in items])
    classifier(client, cache).classify([dev(1)])
    classifier(client, cache).classify([dev(1)])
    assert len(client.requests) == 4 and len(cache) == 0


# --- parallel requests -----------------------------------------------------------------

def test_parallel_run_matches_a_sequential_one(tmp_path):
    """Replies finish out of order (later batches first) and one label is retried; the
    labels, order, stats and cache are as in a one-at-a-time run. The cache's SQLite
    connection refuses use from another thread, so this also shows that only the
    calling thread writes to it."""
    def answer(items):
        time.sleep(0.05 / int(items[0]["id"][1:]))  # the first batch finishes last
        return [good(i, evidence="not in the text at all")
                if i["id"] == "d0005" and "previous_problem" not in i else good(i)
                for i in items]

    deviations = [dev(n, executed=f"The Borrower shall give notice within {n} days.")
                  for n in range(1, 13)]
    runs = []
    for workers in (1, 4):
        cache = ResultCache(tmp_path / f"cache{workers}.sqlite")
        client = FakeClient(answer)
        c = classifier(client, cache, batch_size=2, max_workers=workers)
        runs.append((c.classify(deviations), dict(c.stats), len(cache), client.usage))
        cache.close()
    (seq, *seq_rest), (par, *par_rest) = runs
    assert [ld.deviation for ld in par] == deviations
    assert par == seq and par_rest == seq_rest
    stats, cached, usage = par_rest
    assert stats["retried"] == 1 and stats["llm"] == 12
    assert cached == 12 and usage == Usage(7, 700, 70)  # 6 batches + 1 retry


def test_usage_counts_are_safe_across_threads():
    usage = Usage()
    one = SimpleNamespace(input_tokens=1, output_tokens=2)

    def add_many():
        for _ in range(2000):
            usage.add(one)

    threads = [threading.Thread(target=add_many) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert usage == Usage(16000, 16000, 32000)


def test_an_api_error_stops_further_requests():
    """An error that is not a bad reply (auth, rate limits past the SDK's retries)
    propagates, and batches not yet started are not sent."""
    sent = []

    class Failing(FakeClient):
        def complete_json(self, system, user, schema=None):
            sent.append(user)
            time.sleep(0.02)
            raise RuntimeError("401 invalid x-api-key")

    c = classifier(Failing(lambda items: []), batch_size=1, max_workers=2)
    with pytest.raises(RuntimeError, match="invalid x-api-key"):
        c.classify([dev(n) for n in range(1, 21)])
    assert len(sent) < 20


def test_plan_counts_calls_without_calling():
    client = FakeClient(lambda items: pytest.fail("the LLM was called"))
    calls = classifier(client, batch_size=2).plan(
        [dev(1), dev(2), dev(3), Deviation("d9", ChangeType.FILL_IN, "1", "1", "a", "b", 1, 1)])
    assert [n for _, n in calls] == [2, 1]
    assert calls[0][0].startswith("Label these 2 differences.")


def test_prompt_and_schema_carry_the_taxonomy():
    c = classifier(FakeClient(lambda items: []))
    assert all(f"- {name}: {text}" in c.system for name, text in TAXONOMY.items())
    assert SUBSTANTIVE in c.system
    enum = c.schema["properties"]["labels"]["items"]["properties"]["category"]["enum"]
    assert enum == ["Covenants", "Other", UNCLEAR]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template not present")
def test_quotes_from_a_real_fixture_pass_the_evidence_check():
    """Evidence copied from the raw deviation text (curly quotes and all) is verified."""
    _, template, _ = segment_pdf(TEMPLATE)
    _, executed, _ = segment_pdf(FIXTURES / "fx_05_definition.pdf")
    deviations = DiffEngine().diff(template, executed,
                                   Aligner(embed=no_embeddings).align(template, executed))
    client = FakeClient(lambda items: [good(i) for i in items])
    labeled = classifier(client).classify(deviations, template, executed)
    sent = [ld for ld in labeled if ld.deviation.change_type is MODIFIED]
    assert sent and not any(ld.needs_review for ld in labeled)
    assert client.requests[0][0]["defined_term"] == "Acceptable Credit Rating"


# --- LLM client ------------------------------------------------------------------------

def _response(text, stop="end_turn", category=None):
    return SimpleNamespace(
        stop_reason=stop, stop_details=SimpleNamespace(category=category),
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                              cache_creation_input_tokens=None, cache_read_input_tokens=500))


class FakeMessages:
    def __init__(self, response):
        self.response, self.requests = response, []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


def test_anthropic_request_uses_schema_effort_and_cached_system():
    messages = FakeMessages(_response('{"labels": []}'))
    client = AnthropicClient("claude-sonnet-5-5", max_tokens=8000, effort="medium",
                             client=SimpleNamespace(messages=messages))
    schema = {"type": "object"}
    assert client.complete_json("system text", "user text", schema) == {"labels": []}
    (request,) = messages.requests
    assert request["model"] == "claude-sonnet-5-5" and request["max_tokens"] == 8000
    assert request["output_config"] == {"effort": "medium",
                                        "format": {"type": "json_schema", "schema": schema}}
    assert request["system"] == [{"type": "text", "text": "system text",
                                  "cache_control": {"type": "ephemeral"}}]
    assert request["messages"] == [{"role": "user", "content": "user text"}]
    assert "temperature" not in request and "thinking" not in request
    assert client.usage == Usage(1, 1000, 200, 0, 500)
    prices = {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2}
    assert client.usage.cost(prices) == pytest.approx(0.0041)
    assert client.model_id == "anthropic:claude-sonnet-5-5:medium"


@pytest.mark.parametrize("response, message", [
    (_response("", stop="refusal", category="cyber"), r"refused \(cyber\)"),
    (_response('{"labels": [', stop="max_tokens"), "cut off"),
    (_response("not json"), "not valid JSON"),
    (_response("[1, 2]"), "not an object"),
])
def test_anthropic_unusable_replies_raise(response, message):
    client = AnthropicClient("m", client=SimpleNamespace(messages=FakeMessages(response)))
    with pytest.raises(LLMError, match=message):
        client.complete_json("s", "u")
    assert client.usage.calls == 1  # tokens are counted even when the reply is unusable


def test_json_fences_and_provider_lookup():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    config = {"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "low"}
    assert model_id(config) == "anthropic:claude-sonnet-5-5:low"
    with pytest.raises(ValueError, match="unknown llm.provider"):
        make_client({**config, "provider": "other"})

