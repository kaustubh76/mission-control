"""economy_card — the HEADLINE PnL must be the TRADING PnL only; x402 + self-funded commerce are
separate context, NEVER summed into the headline (the honesty fix)."""

from __future__ import annotations

import json

import ictbot.api.reads as reads


def _setup(tmp_path, monkeypatch, *, nav_after, anchor, revenue_u, x402_usd):
    monkeypatch.setattr(reads, "JOURNAL_DIR", tmp_path)
    (tmp_path / "allocator_live.jsonl").write_text(
        json.dumps({"event": "REBALANCE", "ts": "2026-06-19T09:41:43+00:00", "nav_after": nav_after}) + "\n"
    )
    (tmp_path / "allocator_live_state.json").write_text(json.dumps({"campaign_start_nav": anchor}))
    monkeypatch.setattr(reads, "_commerce_jobs", lambda: {"revenue_u": revenue_u})
    monkeypatch.setattr(reads, "_x402_receipts", lambda: {"spent_usdc": x402_usd})


def test_net_is_trading_pnl_only(tmp_path, monkeypatch):
    # trading PnL = 7.93 − 7.95 = −0.02; the OLD (wrong) net would be −0.02 + 0.20 − 0.10 = +0.08.
    _setup(tmp_path, monkeypatch, nav_after=7.93, anchor=7.95, revenue_u=0.2, x402_usd=0.10)
    c = reads.economy_card()
    assert c["trading_pnl_usd"] == -0.02
    assert c["net_economic_usd"] == c["trading_pnl_usd"] == -0.02   # headline = trading PnL ONLY
    assert c["net_economic_usd"] != 0.08                            # NOT the old commerce-inflated blend


def test_context_lines_present_but_excluded(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, nav_after=8.0, anchor=8.0, revenue_u=0.2, x402_usd=0.10)
    c = reads.economy_card()
    # context still surfaced (honesty about real costs)…
    assert c["commerce_revenue_usd"] == 0.2 and c["x402_spent_usd"] == 0.1
    assert c["commerce_self_funded"] is True
    assert c["commerce_note"] and "not external revenue" in c["commerce_note"].lower()
    assert c["x402_note"] and "not a trading result" in c["x402_note"].lower()
    # …but neither is folded into the headline (trading PnL == 0.0 here).
    assert c["net_economic_usd"] == 0.0
