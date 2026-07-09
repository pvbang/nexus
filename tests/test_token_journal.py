"""TokenJournal: one place to aggregate LLM token usage by caller and model."""

from app.orchestration.token_journal import TokenJournal


def test_record_and_total():
    j = TokenJournal()
    j.record({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
             caller="planner", model="opus")
    j.record({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
             caller="worker", model="sonnet")
    assert j.total()["total_tokens"] == 45
    assert j.total()["prompt_tokens"] == 30
    assert j.total()["completion_tokens"] == 15


def test_breakdown_by_model():
    j = TokenJournal()
    j.record({"total_tokens": 15, "prompt_tokens": 10, "completion_tokens": 5}, model="opus")
    j.record({"total_tokens": 7, "prompt_tokens": 4, "completion_tokens": 3}, model="opus")
    j.record({"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10}, model="sonnet")
    by_model = j.by_model()
    assert by_model["opus"]["total_tokens"] == 22
    assert by_model["sonnet"]["total_tokens"] == 30


def test_breakdown_by_caller():
    j = TokenJournal()
    j.record({"total_tokens": 15}, caller="planner")
    j.record({"total_tokens": 5}, caller="planner")
    j.record({"total_tokens": 30}, caller="worker")
    by_caller = j.by_caller()
    assert by_caller["planner"]["total_tokens"] == 20
    assert by_caller["worker"]["total_tokens"] == 30


def test_dedup_by_call_id():
    j = TokenJournal()
    usage = {"total_tokens": 15, "prompt_tokens": 10, "completion_tokens": 5}
    j.record(usage, model="opus", call_id="abc")
    j.record(usage, model="opus", call_id="abc")  # duplicate (e.g. retry/stream double-fire)
    assert j.total()["total_tokens"] == 15  # counted once


def test_distinct_call_ids_both_counted():
    j = TokenJournal()
    j.record({"total_tokens": 10}, model="opus", call_id="a")
    j.record({"total_tokens": 10}, model="opus", call_id="b")
    assert j.total()["total_tokens"] == 20


def test_empty_or_none_usage_is_safe():
    j = TokenJournal()
    j.record(None, model="opus")
    j.record({}, model="opus")
    assert j.total()["total_tokens"] == 0
    assert j.call_count == 0


def test_call_count_tracks_recorded():
    j = TokenJournal()
    j.record({"total_tokens": 5}, model="x")
    j.record({"total_tokens": 5}, model="x")
    assert j.call_count == 2


def test_unknown_caller_model_default_buckets():
    j = TokenJournal()
    j.record({"total_tokens": 5})
    assert j.by_model()["unknown"]["total_tokens"] == 5
    assert j.by_caller()["unknown"]["total_tokens"] == 5
