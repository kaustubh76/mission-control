"""
Prometheus metrics — optional.

If `prometheus_client` is installed we expose real counters / histograms
+ a `start_metrics_server(port)` helper. If not, every name resolves to
a no-op shim so the application keeps running. This lets the scanner
ship metrics without making prometheus_client a hard dependency.
"""

from __future__ import annotations

try:
    from prometheus_client import (
        Counter as _Counter,
    )
    from prometheus_client import (
        Gauge as _Gauge,
    )
    from prometheus_client import (
        Histogram as _Histogram,
    )
    from prometheus_client import (
        start_http_server as _start_http_server,
    )

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

    class _NoopTimer:
        """Stand-in for prometheus_client.Histogram.time()'s ContextManager."""

        def __enter__(self) -> _NoopTimer:
            return self

        def __exit__(self, *exc) -> None:
            return None

    class _NoopMetric:
        def __init__(self, *a, **kw):
            pass

        def labels(self, *a, **kw):
            return self

        def inc(self, *a, **kw):
            pass

        def observe(self, *a, **kw):
            pass

        def set(self, *a, **kw):
            pass

        def time(self) -> _NoopTimer:
            # Real Histogram.time() returns a context manager that times
            # the wrapped block. The no-op variant must be safe to use
            # with `with metric.time():` regardless of install state.
            return _NoopTimer()

    _Counter = _Histogram = _Gauge = _NoopMetric

    def _start_http_server(port: int, addr: str = "") -> None:
        # Silent no-op. Caller can probe is_available().
        pass


def is_available() -> bool:
    return _PROMETHEUS_AVAILABLE


def start_metrics_server(port: int = 9100, addr: str = "0.0.0.0") -> None:
    """Start the metrics HTTP listener. Idempotent enough for tests."""
    _start_http_server(port, addr)


# --- Metrics catalogue ------------------------------------------------------
# Keep names + labels stable; dashboards depend on them.

signals_fired_total = _Counter(
    "ictbot_signals_fired_total",
    "Number of BUY/SELL signals emitted by the analyzer.",
    ["pair", "direction"],
)

evaluations_total = _Counter(
    "ictbot_evaluations_total",
    "Number of strategy evaluations performed.",
    ["pair", "outcome"],  # outcome = "signal" | "no_entry" | "error"
)

cap_rejections_total = _Counter(
    "ictbot_cap_rejections_total",
    "Number of orders rejected by portfolio caps.",
    ["cap"],
)

evaluate_latency_seconds = _Histogram(
    "ictbot_evaluate_latency_seconds",
    "How long Strategy.evaluate takes per call.",
)

# Per-step funnel — for each non-firing evaluation, the FIRST blocker in
# canonical pipeline order increments here. Lets the dashboard answer
# "of the last 1000 evals on BTC, where did we drop off?" without
# scraping log lines. `step` values are the canonical keys defined in
# scanner._blocker_to_step (htf_bias / poi_tap / mss / fvg / mfvg_retest
# / delta / gate). `direction` is the would-be side (BUY/SELL) — sourced
# from diagnostics.closest_direction so even WAITING-bias evals get one.
funnel_step_failures_total = _Counter(
    "ictbot_funnel_step_failures_total",
    "First canonical blocker encountered on each non-firing evaluation.",
    ["pair", "step", "direction"],
)

# Phase B — shadow router metrics (legacy ictbot).
# Observed fill slippage (signed bps) for live placements when the
# shadow leg also placed. Positive = worse fill than the strategy's
# signal price; sign is normalised so positive always means "worse".
shadow_fill_slippage_bps = _Histogram(
    "ictbot_shadow_fill_slippage_bps",
    "Signed live-fill slippage in basis points vs the signal price.",
    ["pair", "side"],
)
# Realised R delta (shadow_R - live_R) per pair per closed pair. Sign:
# positive = shadow performed better than live (i.e. live ate friction).
shadow_r_delta = _Histogram(
    "ictbot_shadow_r_delta",
    "Per-close R-multiple difference (shadow_R - live_R).",
    ["pair"],
)
# Divergence: one leg placed, the other rejected. `reason` is which
# side rejected — "live_rejected" or "shadow_rejected" (the OTHER leg
# placed). "shadow_exception" is reserved for swallowed shadow errors.
shadow_diverged_total = _Counter(
    "ictbot_shadow_diverged_total",
    "Routing divergence between the live and shadow legs.",
    ["pair", "reason"],
)

# Phase D — count every LIVE bracket the router successfully places.
# Incremented from SignalRouter.route() right after the live broker's
# place_order() returns, so paper/shadow legs are NOT counted (they go
# through their own routers with no .inc() call). Used by the
# `NoLiveFillsToday` alert + the weekly shadow-report context.
live_trades_total = _Counter(
    "ictbot_live_trades_total",
    "Live bracket orders successfully placed by the live router.",
    ["pair", "direction"],
)

# Phase D — kill-switch state surfaced for alerting. 1 when engaged,
# 0 when clear. Updated by the scanner main loop on each tick so the
# Prometheus scrape sees the freshest state without a separate hook.
kill_switch_engaged = _Gauge(
    "ictbot_kill_switch_engaged",
    "1 when the live-trading kill switch is engaged, 0 otherwise.",
)
