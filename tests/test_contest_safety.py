"""Contest-safety (the standing gate). The campaign + stability harnesses are SIM-only /
read-only: the campaign reads the journal but never writes it, neither writes the SIM selector,
and stability persists NO verdicts. The locked default stays bit-for-bit, and a LIVE dispatch
ignores the SIM selector — a dashboard click can never reach the contest strategy."""

from __future__ import annotations

import numpy as np

import scripts.strategy_campaign as sc
import scripts.strategy_stability as ss
from ictbot.engine.acceptance import evaluate_portfolio
from ictbot.runtime import strategy_select, verdicts
from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import regime_score as _rs
from ictbot.strategy import registry


def _close(n: int = 400, k: int = 8) -> np.ndarray:
    rng = np.arange(n)
    return np.column_stack(
        [100 * (1 + 0.0005 * (j + 1)) ** rng * (1 + 0.02 * np.sin(rng / 15 + j)) for j in range(k)]
    )


def _fake_survival(strategy, *, limit=2500, static_cap=False, frames=None):
    stats = {"worst_week_dd": 0.12, "trades_per_week": 9.0, "mean_ret": 0.0, "median_ret": 0.0}
    return {
        "summary": strategy,
        "ok": True,
        "error": None,
        "n_loaded": 8,
        "n_bars": 2000,
        "n_tokens": 8,
        "cols": ["BTC"],
        "cap_note": "x",
        "stats_30": stats,
        "stats_70": stats,
        "gate": evaluate_portfolio(stats),
    }


def test_campaign_is_read_only_on_journal_and_selector(tmp_path, monkeypatch):
    from ictbot.api import reads

    sel = tmp_path / "strategy_select.json"
    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", sel, raising=False)
    monkeypatch.setattr(verdicts, "VERDICTS_FILE", tmp_path / "gates.json")
    monkeypatch.setattr(sc.vs, "survival_for", _fake_survival)
    jrnl = tmp_path / "allocator_journal.jsonl"
    jrnl.write_text(
        '{"event":"REBALANCE","strategy":"momentum_adaptive",'
        '"ts":"2026-05-01T00:00:00+00:00","nav_after":1000,"n_swaps":2}\n'
    )
    monkeypatch.setattr(reads, "JOURNAL", jrnl)
    before = jrnl.read_text()
    doc = tmp_path / "c.md"
    doc.write_text(f"{sc.GUARDIAN_START}\nx\n{sc.GUARDIAN_END}\n")

    sc.run_campaign(
        save=True,
        frames={},
        now_iso="t",
        doc_path=doc,
        report_path=tmp_path / "r.md",
        forward_min_days=5.0,
    )

    assert jrnl.read_text() == before  # the journal it READS is never modified
    assert not sel.exists()  # the SIM selector is never written


def test_stability_persists_no_verdicts_and_no_selector(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("stability must never persist verdicts")

    monkeypatch.setattr(verdicts, "record", _boom)
    sel = tmp_path / "strategy_select.json"
    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", sel, raising=False)

    # if run_stability called verdicts.record this would raise; it must complete cleanly.
    ss.run_stability(
        _close(),
        arms=["momentum_adaptive", "breakout"],
        save=True,
        report_path=tmp_path / "stab.md",
        grades_path=tmp_path / "stab.json",
        now_iso="t",
    )

    assert (tmp_path / "stab.md").exists()  # markdown report
    assert (tmp_path / "stab.json").exists()  # JSON grade sidecar (tmp, not the real file)
    assert not sel.exists()


def test_locked_default_weight_path_is_bit_for_bit():
    close = _close()
    adaptive = registry.get("momentum_adaptive")
    p = adaptive.default_params()
    caps = _rs.cap_series(close, floor=0.40, ceiling=0.90, ma_window=50)
    assert np.array_equal(
        adaptive.weight_path(close, p=p, cap_series=caps), _ma.weight_path(close, p, caps)
    )


def test_live_dispatch_ignores_sim_selector(tmp_path, monkeypatch):
    monkeypatch.setattr(
        strategy_select, "STRATEGY_SELECT_FILE", tmp_path / "sel.json", raising=False
    )
    import scripts.run_allocator as ra

    monkeypatch.setattr(ra.settings, "strategy_name", "", raising=False)
    monkeypatch.setattr(ra.settings, "alloc_adaptive", True, raising=False)

    strategy_select.save("dual_momentum")  # the SIM selector picks a challenger
    assert ra._resolve_strategy_name("sim") == "dual_momentum"  # SIM honors it
    assert ra._resolve_strategy_name("live") == "momentum_adaptive"  # LIVE ignores it
