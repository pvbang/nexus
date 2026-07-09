"""Lightweight, dependency-free tracing: trace_id correlation + spans."""

from app.core.tracing import (
    Tracer,
    new_trace_id,
    get_current_trace_id,
    set_current_trace_id,
)


def test_new_trace_id_is_unique():
    a = new_trace_id()
    b = new_trace_id()
    assert a != b
    assert isinstance(a, str) and len(a) >= 8


def test_set_and_get_current_trace_id():
    set_current_trace_id("trace-xyz")
    assert get_current_trace_id() == "trace-xyz"


def test_noop_tracer_span_records_nothing_but_works():
    tracer = Tracer()  # no backend → no-op
    with tracer.span("plan", attrs={"turn": 1}) as span:
        span.set_attribute("model", "opus")
    # No backend, no exception, no records.
    assert tracer.exported == []


def test_tracer_with_collector_backend_records_spans():
    records = []
    tracer = Tracer(exporter=records.append)
    with tracer.span("llm_call", attrs={"model": "opus"}) as span:
        span.set_attribute("tokens", 42)
    assert len(records) == 1
    rec = records[0]
    assert rec["name"] == "llm_call"
    assert rec["attributes"]["model"] == "opus"
    assert rec["attributes"]["tokens"] == 42
    assert "duration_ms" in rec
    assert rec["status"] == "ok"


def test_span_records_error_status_on_exception():
    records = []
    tracer = Tracer(exporter=records.append)
    try:
        with tracer.span("boom"):
            raise ValueError("kaboom")
    except ValueError:
        pass
    assert records[0]["status"] == "error"
    assert "kaboom" in records[0]["attributes"].get("error", "")


def test_span_inherits_trace_id():
    set_current_trace_id("root-trace")
    records = []
    tracer = Tracer(exporter=records.append)
    with tracer.span("child"):
        pass
    assert records[0]["trace_id"] == "root-trace"


def test_exporter_failure_never_breaks_span():
    def _bad_exporter(_):
        raise RuntimeError("exporter down")
    tracer = Tracer(exporter=_bad_exporter)
    # Must not raise even though the exporter throws.
    with tracer.span("safe"):
        pass
