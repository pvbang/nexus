"""Tests for C4: Prometheus metrics exposition.

Audit §1 C4 — observability only had a basic system_metrics_emitter pushing to
the message bus; there was no scrapeable /metrics endpoint. We render the
Prometheus text exposition format directly (no prometheus_client dependency —
the format is small and stable) from the snapshots the circuit breaker and
fallback chain already expose.

Pure rendering functions: no IO.
"""

from __future__ import annotations

from app.core.metrics_exporter import (
    render_circuit_breaker_metrics,
    render_metric,
    render_prometheus,
)

# ───────────────────────── render_metric primitive ─────────────────────────


def test_render_metric_emits_help_type_and_value():
    out = render_metric(
        "nexus_up", "gauge", "Whether the app is up", [({}, 1)]
    )
    assert "# HELP nexus_up Whether the app is up" in out
    assert "# TYPE nexus_up gauge" in out
    assert "nexus_up 1" in out


def test_render_metric_emits_labels():
    out = render_metric(
        "calls_total", "counter", "Total calls",
        [({"provider": "bedrock", "state": "open"}, 7)],
    )
    assert 'calls_total{provider="bedrock",state="open"} 7' in out


def test_render_metric_escapes_label_values():
    out = render_metric(
        "x", "gauge", "h", [({"err": 'a"b\\c'}, 1)]
    )
    # Quotes and backslashes in label values must be escaped per the spec.
    assert r'x{err="a\"b\\c"} 1' in out


def test_render_metric_handples_no_samples():
    out = render_metric("empty", "gauge", "nothing", [])
    assert "# TYPE empty gauge" in out


# ───────────────────── circuit breaker → metrics ─────────────────────


def test_circuit_breaker_metrics_from_status():
    status = {
        "bedrock:claude-opus": {
            "state": "open",
            "failure_count": 5,
            "metrics": {"total_calls": 10, "successes": 5, "failures": 5,
                        "success_rate": 0.5, "avg_latency_ms": 120.0},
        },
        "openai:gpt-4o": {
            "state": "closed",
            "failure_count": 0,
            "metrics": {"total_calls": 3, "successes": 3, "failures": 0,
                        "success_rate": 1.0, "avg_latency_ms": 80.0},
        },
    }
    out = render_circuit_breaker_metrics(status)
    # State is exposed as a gauge that is 1 for the active state, per circuit.
    assert 'nexus_circuit_breaker_state{circuit="bedrock:claude-opus",state="open"} 1' in out
    assert 'nexus_circuit_breaker_state{circuit="openai:gpt-4o",state="closed"} 1' in out
    # Call counters carry through.
    assert 'nexus_circuit_breaker_calls_total{circuit="bedrock:claude-opus"} 10' in out
    assert 'nexus_circuit_breaker_failures_total{circuit="bedrock:claude-opus"} 5' in out


# ───────────────────────── full document ─────────────────────────


def test_render_prometheus_is_well_formed():
    doc = render_prometheus(
        circuit_status={
            "openai:gpt-4o": {
                "state": "closed", "failure_count": 0,
                "metrics": {"total_calls": 1, "successes": 1, "failures": 0,
                            "success_rate": 1.0, "avg_latency_ms": 10.0},
            },
        },
        extra_gauges={"nexus_active_tasks": 4},
    )
    # Ends with a trailing newline (Prometheus requires it) and includes our gauge.
    assert doc.endswith("\n")
    assert "nexus_active_tasks 4" in doc
    assert "# TYPE nexus_active_tasks gauge" in doc
    # No HELP/TYPE line should be duplicated for the same metric name.
    type_lines = [ln for ln in doc.splitlines() if ln.startswith("# TYPE nexus_active_tasks ")]
    assert len(type_lines) == 1
