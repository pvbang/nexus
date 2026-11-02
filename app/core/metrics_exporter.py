"""
Nexus - Prometheus metrics exposition.

Renders the Prometheus text exposition format directly (audit C4). We deliberately
avoid a `prometheus_client` dependency: the format is small and stable, the data
is read-only snapshots the circuit breaker / fallback chain already expose, and
keeping it dependency-free means the /metrics endpoint can't break the app on an
unsynced env (cf. pytest-cov declared-but-not-installed in C3).

Spec: https://prometheus.io/docs/instrumenting/exposition_formats/
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

# (provider, model) circuit states the breaker can report — one gauge sample per
# state per circuit, value 1 for the active state and 0 otherwise.
_CIRCUIT_STATES = ("closed", "open", "half_open")


def _escape_label_value(value: str) -> str:
    """Escape a label value per the exposition format: backslash, quote, newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(
        f'{k}="{_escape_label_value(str(v))}"' for k, v in labels.items()
    )
    return "{" + inner + "}"


def render_metric(
    name: str,
    metric_type: str,
    help_text: str,
    samples: Iterable[tuple[dict[str, str], float]],
) -> str:
    """Render one metric family (HELP + TYPE + samples) as exposition text.

    `samples` is an iterable of (labels, value) pairs. A family with no samples
    still emits its HELP/TYPE header (valid and useful for discovery).
    """
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
    ]
    for labels, value in samples:
        rendered = value if isinstance(value, int) else round(float(value), 4)
        lines.append(f"{name}{_format_labels(labels)} {rendered}")
    return "\n".join(lines)


def render_circuit_breaker_metrics(status: dict[str, dict]) -> str:
    """Render circuit-breaker status (from CircuitBreaker.get_status()) as metrics.

    Emits, per circuit (keyed "provider" or "provider:model"):
      - nexus_circuit_breaker_state{circuit,state}  gauge (1 for active state)
      - nexus_circuit_breaker_calls_total{circuit}  counter
      - nexus_circuit_breaker_failures_total{circuit} counter
      - nexus_circuit_breaker_success_rate{circuit}  gauge
      - nexus_circuit_breaker_avg_latency_ms{circuit} gauge
    """
    state_samples: list[tuple[dict[str, str], float]] = []
    calls_samples: list[tuple[dict[str, str], float]] = []
    failures_samples: list[tuple[dict[str, str], float]] = []
    success_rate_samples: list[tuple[dict[str, str], float]] = []
    latency_samples: list[tuple[dict[str, str], float]] = []

    for circuit, info in sorted(status.items()):
        active = info.get("state", "closed")
        for state in _CIRCUIT_STATES:
            state_samples.append(
                ({"circuit": circuit, "state": state}, 1 if state == active else 0)
            )
        m = info.get("metrics", {})
        calls_samples.append(({"circuit": circuit}, m.get("total_calls", 0)))
        failures_samples.append(({"circuit": circuit}, m.get("failures", 0)))
        success_rate_samples.append(({"circuit": circuit}, m.get("success_rate", 0)))
        latency_samples.append(({"circuit": circuit}, m.get("avg_latency_ms", 0)))

    return "\n".join([
        render_metric(
            "nexus_circuit_breaker_state", "gauge",
            "Circuit breaker state per LLM circuit (1=active)", state_samples,
        ),
        render_metric(
            "nexus_circuit_breaker_calls_total", "counter",
            "Total LLM calls observed per circuit", calls_samples,
        ),
        render_metric(
            "nexus_circuit_breaker_failures_total", "counter",
            "Total failed LLM calls per circuit", failures_samples,
        ),
        render_metric(
            "nexus_circuit_breaker_success_rate", "gauge",
            "Success rate per circuit in [0,1]", success_rate_samples,
        ),
        render_metric(
            "nexus_circuit_breaker_avg_latency_ms", "gauge",
            "Average successful-call latency per circuit (ms)", latency_samples,
        ),
    ])


def render_prometheus(
    circuit_status: Optional[dict[str, dict]] = None,
    extra_gauges: Optional[dict[str, float]] = None,
) -> str:
    """Render the full /metrics document.

    Args:
        circuit_status: CircuitBreaker.get_status() snapshot (per-circuit health).
        extra_gauges: flat {metric_name: value} simple gauges (e.g. active tasks).

    Returns the exposition text, terminated by a trailing newline as required.
    """
    blocks: list[str] = []
    # Always emit the circuit-breaker family (HELP/TYPE headers), even with no
    # circuits yet — useful for scrape-target discovery and stable dashboards.
    blocks.append(render_circuit_breaker_metrics(circuit_status or {}))
    for name, value in (extra_gauges or {}).items():
        blocks.append(render_metric(name, "gauge", name, [({}, value)]))
    return "\n".join(blocks) + "\n"
