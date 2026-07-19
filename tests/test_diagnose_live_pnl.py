"""
Unit tests for scripts/diagnose_live_pnl.py.

Focus on the Fix 2.J truth-source classifier — the Phase 3 Layer 2
acceptance gate hinges on this being correct.

The script is in scripts/ (not src/) so we import via importlib +
explicit path; that pattern keeps the script standalone-runnable
while still being testable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "diagnose_live_pnl.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("diagnose_live_pnl", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load_module()


# ---- _classify_truth coverage --------------------------------------------


def _row(**overrides) -> dict:
    """Reasonable defaults for a closed BUY/SELL row."""
    base = {
        "entry": "BUY",
        "pair": "BTC/USDT:USDT",
        "price": 100.0,
        "sl": 95.0,
        "tp": 110.0,
        "rr": 2.0,
        "confidence": 100,
        "outcome": "WIN",
        "closed_ts": "2026-06-06T00:00:00Z",
        "closed_price": 110.0,
        "broker": "binance-live",
        "pnl_r": 2.0,
        "fees_paid": 0.5,
        "entry_fill_price": 100.05,
    }
    base.update(overrides)
    return base


def test_classify_truth_broker_truth():
    """The happy path: live broker row with drifted closed_price + pnl_r
    + fees_paid all present."""
    r = _row(closed_price=109.9876, pnl_r=1.97, fees_paid=0.5)
    assert D._classify_truth(r) == "broker-truth"


def test_classify_truth_broker_truth_no_fee():
    """Live row with broker truth but the fee fetch failed at close
    time. R is still authoritative (just gross instead of net)."""
    r = _row(closed_price=109.9876, pnl_r=1.97, fees_paid=None)
    assert D._classify_truth(r) == "broker-truth-no-fee"


def test_classify_truth_synthetic_paper_default_broker():
    """Paper row with bit-for-bit closed_price on tp. The legacy
    synthetic-settler is the only writer for paper, and that's fine."""
    r = _row(broker="paper", closed_price=110.0, pnl_r=None, fees_paid=None)
    assert D._classify_truth(r) == "synthetic-paper"


def test_classify_truth_synthetic_paper_missing_broker_field():
    """Backwards-compat: pre-Fix-2.A rows have no `broker` key. The
    classifier must treat them as paper."""
    r = _row(closed_price=110.0, pnl_r=None, fees_paid=None)
    # Remove the broker key to simulate pre-Fix-2.A schema
    del r["broker"]
    assert D._classify_truth(r) == "synthetic-paper"


def test_classify_truth_synthetic_live_bug_no_pnl_r():
    """The regression Phase 2 is designed to prevent: a live broker row
    whose closed_price is bit-for-bit equal to tp/sl AND pnl_r is
    missing. Would mean settle_open_signals closed the row before the
    broker callback fired. New live rows must NEVER land here."""
    r = _row(closed_price=110.0, pnl_r=None, fees_paid=None)
    # broker="binance-live" is on by default
    assert D._classify_truth(r) == "synthetic-live-bug"


def test_classify_truth_limit_tp_fill_at_exact_price_is_broker_truth():
    """Fix 6.B (post-XRP-close finding): a LIMIT TP at price X fills
    at EXACTLY X (or better). Bit-for-bit equality between close_price
    and tp on a row with populated pnl_r is therefore normal broker
    truth, not a synthetic-settler signature.

    Real-world example: XRP/USDT:USDT on 2026-06-06 closed at TP
    1.0586 exactly, pnl_r=+5.018 — the limit BUY at 1.0586 filled at
    1.0586. Pre-Fix-6.B classifier wrongly flagged this as
    synthetic-live-bug and held back the acceptance gate."""
    r = _row(closed_price=110.0, pnl_r=2.0, fees_paid=0.5)
    assert D._classify_truth(r) == "broker-truth"


def test_classify_truth_partial_no_closed_price():
    r = _row(closed_price=None, outcome="OPEN")
    assert D._classify_truth(r) == "partial"


# ---- acceptance gate in build_report -------------------------------------


def test_acceptance_pass_with_one_broker_truth_and_no_bug():
    """Phase 3 Layer 2 PASS: ≥1 broker-truth row AND 0
    synthetic-live-bug rows."""
    rows = [
        _row(closed_price=109.9876, pnl_r=1.97, fees_paid=0.5),
        _row(broker="paper", closed_price=110.0, pnl_r=None, fees_paid=None, entry_fill_price=None),
    ]
    report = D.build_report(rows)
    assert report["acceptance"] is True
    assert report["truth_classes"]["broker-truth"] == 1
    assert report["truth_classes"]["synthetic-paper"] == 1


def test_acceptance_fail_when_synthetic_live_bug_present():
    """Even one synthetic-live-bug row fails the gate, regardless of
    how many broker-truth rows are also present. The bug signature
    after Fix 6.B requires pnl_r to be missing — that's the only
    case where bit-for-bit equality genuinely means the synthetic
    settler beat the broker callback."""
    rows = [
        _row(closed_price=109.9876, pnl_r=1.97, fees_paid=0.5),
        _row(closed_price=110.0, pnl_r=None, fees_paid=None),  # ← bug
    ]
    report = D.build_report(rows)
    assert report["acceptance"] is False
    assert report["truth_classes"]["synthetic-live-bug"] == 1


def test_acceptance_pass_with_limit_tp_at_exact_price():
    """Fix 6.B regression check: a row representing a real LIMIT TP
    fill (pnl_r populated, closed_price == tp bit-for-bit) MUST be
    counted as broker-truth so the acceptance gate passes. This was
    the false-positive that blocked the XRP TP close from registering
    as a Phase 3 Layer 2 acceptance moment."""
    rows = [
        _row(closed_price=110.0, pnl_r=2.0, fees_paid=0.5),  # exact TP fill
    ]
    report = D.build_report(rows)
    assert report["acceptance"] is True
    assert report["truth_classes"].get("broker-truth", 0) == 1
    assert report["truth_classes"].get("synthetic-live-bug", 0) == 0


def test_acceptance_fail_with_zero_broker_truth():
    """Paper-only journal can't satisfy the live gate even with zero
    bugs — the gate also requires evidence the broker callback path
    fired at least once."""
    rows = [
        _row(broker="paper", closed_price=110.0, pnl_r=None, fees_paid=None, entry_fill_price=None),
    ]
    report = D.build_report(rows)
    assert report["acceptance"] is False


def test_acceptance_fail_when_no_closed_rows():
    """Empty / OPEN-only journal: gate is FALSE (no evidence either
    way), but the human report shows N/A."""
    report = D.build_report([])
    assert report["acceptance"] is False
    assert report["truth_classes"] == {}


def test_broker_truth_no_fee_counts_toward_acceptance():
    """A live close where the fee fetch failed is still authoritative
    R — the gross-R is the correct truth. Acceptance must allow it."""
    rows = [
        _row(closed_price=109.9876, pnl_r=1.97, fees_paid=None),
    ]
    report = D.build_report(rows)
    assert report["acceptance"] is True
    assert report["truth_classes"]["broker-truth-no-fee"] == 1


# ---- Fix 9.G: 5-pair smoke gate ----------------------------------------


PAIRS_FIXTURE = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
]


def test_smoke_gate_passes_when_every_pair_has_one_broker_truth():
    rows = [_row(pair=p, closed_price=110.0, pnl_r=2.0) for p in PAIRS_FIXTURE]
    gate = D.build_smoke_gate(rows, PAIRS_FIXTURE)
    assert gate["smoke_gate_pass"] is True
    assert sorted(gate["pairs_passed"]) == sorted(PAIRS_FIXTURE)
    assert gate["pairs_pending"] == []


def test_smoke_gate_pending_when_one_pair_missing():
    """Only 4 of 5 pairs have broker-truth closes; gate fails."""
    rows = [
        _row(pair=p, closed_price=110.0, pnl_r=2.0) for p in PAIRS_FIXTURE if p != "SOL/USDT:USDT"
    ]
    gate = D.build_smoke_gate(rows, PAIRS_FIXTURE)
    assert gate["smoke_gate_pass"] is False
    assert "SOL/USDT:USDT" in gate["pairs_pending"]
    assert gate["per_pair"]["SOL/USDT:USDT"]["truth_count"] == 0


def test_smoke_gate_skips_synthetic_closes():
    """A row classified as synthetic-paper or synthetic-live-bug must
    NOT count toward the pair's truth_count."""
    rows = [
        _row(
            pair="BTC/USDT:USDT",
            broker="paper",  # → synthetic-paper, not broker-truth
            pnl_r=None,
            closed_price=110.0,  # bit-for-bit on tp → looks like synthetic
        ),
    ]
    gate = D.build_smoke_gate(rows, ["BTC/USDT:USDT"])
    assert gate["smoke_gate_pass"] is False
    assert gate["per_pair"]["BTC/USDT:USDT"]["truth_count"] == 0


def test_smoke_gate_no_fee_truth_still_counts():
    rows = [_row(pair="BTC/USDT:USDT", closed_price=109.98, pnl_r=1.97, fees_paid=None)]
    gate = D.build_smoke_gate(rows, ["BTC/USDT:USDT"])
    assert gate["smoke_gate_pass"] is True


def test_smoke_gate_unknown_pair_in_journal_is_ignored():
    """A row for a pair not in the configured list must not crash and
    must not boost any configured pair's count."""
    rows = [
        _row(pair="DOGE/USDT:USDT", closed_price=110.0, pnl_r=2.0),
        _row(pair="BTC/USDT:USDT", closed_price=110.0, pnl_r=2.0),
    ]
    gate = D.build_smoke_gate(rows, ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    assert gate["per_pair"]["BTC/USDT:USDT"]["truth_count"] == 1
    assert gate["per_pair"]["ETH/USDT:USDT"]["truth_count"] == 0
    # DOGE not in config — silently ignored.
    assert "DOGE/USDT:USDT" not in gate["per_pair"]


def test_smoke_gate_records_first_close_ts_per_pair():
    rows = [
        _row(pair="BTC/USDT:USDT", closed_ts="2026-06-06T01:00:00Z", pnl_r=2.0),
        _row(pair="BTC/USDT:USDT", closed_ts="2026-06-06T00:30:00Z", pnl_r=2.0),
        _row(pair="ETH/USDT:USDT", closed_ts="2026-06-06T02:00:00Z", pnl_r=2.0),
    ]
    gate = D.build_smoke_gate(rows, ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    # Earliest BTC close is 00:30, not 01:00.
    assert gate["per_pair"]["BTC/USDT:USDT"]["first_close_ts"] == "2026-06-06T00:30:00Z"
    assert gate["per_pair"]["ETH/USDT:USDT"]["first_close_ts"] == "2026-06-06T02:00:00Z"
