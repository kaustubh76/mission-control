"""Dashboard read layer: the new safety signals + schema/graceful-degradation."""

from __future__ import annotations

import ictbot.api.reads as reads


# --------------------------- health: journal mode ------------------------- #
def test_health_card_journal_mode_and_mismatch(monkeypatch):
    monkeypatch.setattr(reads.settings, "dashboard_journal", "sim")
    monkeypatch.setattr(reads.settings, "twak_mode", "live")
    card = reads.health_card()
    assert card["journal_mode"] == "sim"
    assert card["journal_mismatch"] is True


def test_health_card_no_mismatch_when_aligned(monkeypatch):
    monkeypatch.setattr(reads.settings, "dashboard_journal", "live")
    monkeypatch.setattr(reads.settings, "twak_mode", "live")
    assert reads.health_card()["journal_mismatch"] is False


# --------------------------- state: halt + trade floor -------------------- #
def test_state_card_surfaces_halt_reason(monkeypatch):
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t1",
            "nav_after": 1000.0,
            "weights_after": {"BNB": 0.4},
            "cumulative_swaps": 5,
            "trade_floor_min": 7,
        },
        {"event": "DD_HALT", "ts": "t2", "nav": 700.0, "hwm": 1000.0, "dd": 0.30, "dd_cap": 0.05},
    ]
    monkeypatch.setattr(
        reads, "read_state", lambda: {"hwm": 1000.0, "halted": True, "balances": {"USDT": 700.0}}
    )
    card = reads.state_card(rows)
    assert card["halted"] is True
    assert card["halt_reason"] and "30.0%" in card["halt_reason"]
    assert card["halt_ts"] == "t2"
    assert card["cumulative_swaps"] == 5
    assert card["trade_floor"] == 7


def test_state_card_no_halt_reason_when_running(monkeypatch):
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t1",
            "nav_after": 1000.0,
            "weights_after": {},
            "cumulative_swaps": 3,
        }
    ]
    monkeypatch.setattr(
        reads, "read_state", lambda: {"hwm": 1000.0, "halted": False, "balances": {}}
    )
    card = reads.state_card(rows)
    assert card["halted"] is False
    assert card["halt_reason"] is None
    assert card["cumulative_swaps"] == 3


# --------------------------- rebalances: failed swaps --------------------- #
def test_rebalances_card_surfaces_failed_swaps():
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t",
            "n_swaps": 2,
            "n_swaps_total": 3,
            "n_failed": 1,
            "failed_swaps": [{"from": "USDT", "to": "BNB", "error": "slippage"}],
            "tx": ["0xabc"],
            "weights_after": {"BNB": 0.4},
        }
    ]
    item = reads.rebalances_card(10, rows)["items"][0]
    assert item["n_swaps"] == 2
    assert item["n_swaps_total"] == 3
    assert item["n_failed"] == 1
    assert item["failed_swaps"][0]["error"] == "slippage"
    assert "bscscan.com/tx/0xabc" in item["tx"][0]["url"]


def test_rebalances_card_defaults_when_fields_absent():
    # an old journal row (pre-hardening) still yields a valid item
    rows = [{"event": "REBALANCE", "ts": "t", "n_swaps": 1, "tx": []}]
    item = reads.rebalances_card(10, rows)["items"][0]
    assert item["n_failed"] == 0
    assert item["n_swaps_total"] == 1  # falls back to n_swaps
    assert item["failed_swaps"] == []


# --------------------------- robustness ----------------------------------- #
def test_read_journal_skips_corrupt_lines(tmp_path, monkeypatch):
    jf = tmp_path / "j.jsonl"
    jf.write_text(
        '{"event": "REBALANCE", "ts": "t1", "nav_after": 1000}\n'
        "this is not json\n"
        '{"event": "REBALANCE", "ts": "t2", "nav_after": 1010}\n'
        '{"event": "REBALANCE", "ts": "t3"'  # truncated final line (mid-write)
    )
    monkeypatch.setattr(reads, "JOURNAL", jf)
    rows = reads.read_journal()
    assert [r.get("ts") for r in rows] == ["t1", "t2"]  # bad + truncated skipped


def test_snapshot_degrades_gracefully(monkeypatch):
    # a card raising must degrade that card to a default, not 500 the whole snapshot
    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: [])
    monkeypatch.setattr(
        reads, "nav_card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(reads, "_pillars_net", lambda: {})  # no network in the test
    snap = reads.snapshot()
    assert snap["nav"] == {}  # failed card → {}
    assert "health" in snap and "rebalances" in snap


# --------------------------- pillars: x402 / twak / nodereal -------------- #
def test_rebalances_card_passes_through_x402_dex():
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t1",
            "n_swaps": 0,
            "tx": [],
            "weights_after": {},
            "x402_dex": {"q": "BNB", "symbol": "BNB", "price_usd": 612.5, "liquidity": 5.2e6},
        },
        {"event": "REBALANCE", "ts": "t2", "n_swaps": 0, "tx": [], "weights_after": {}},
    ]
    items = reads.rebalances_card(10, rows)["items"]  # newest-first
    assert items[0]["x402_dex"] is None  # t2: no x402 read
    assert items[1]["x402_dex"]["symbol"] == "BNB" and items[1]["x402_dex"]["price_usd"] == 612.5


def test_x402_receipts_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)
    (tmp_path / "x402").mkdir()
    (tmp_path / "x402" / "receipts.json").write_text(
        '[{"ts":"t1","status":"settled","value":10000},'
        ' {"ts":"t2","status":"failed","error":"X402AmountExceededError"},'
        ' {"ts":"t3","status":"settled","value":10000}]'
    )
    r = reads._x402_receipts()
    assert r["total"] == 3 and r["settled"] == 2
    assert r["spent_usdc"] == 0.02  # 20000 units / 1e6
    assert r["last_ts"] == "t3" and r["last_status"] == "settled"


def test_x402_receipts_zeros_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)  # no x402/receipts.json
    assert reads._x402_receipts() == {
        "total": 0,
        "settled": 0,
        "spent_usdc": 0.0,
        "last_ts": None,
        "last_status": None,
    }


def test_pillars_card_assembles_all_three(monkeypatch):
    # isolate the network boundary (the live RPC path is covered by the gated
    # tests/test_nodereal_real_integration.py); here we assemble from fixed inputs.
    monkeypatch.setattr(
        reads,
        "_pillars_net",
        lambda: {
            "pay_wallet": "0xWALLET",
            "sdk_installed": True,
            "base_usdc_balance": 0.99,
            "link": {
                "reachable": True,
                "chain_id": 56,
                "chain_ok": True,
                "sponsorable": False,
                "wallet": "0xWALLET",
                "nonce": 2,
                "registry": "0xREG",
                "note": "link OK",
            },
        },
    )
    monkeypatch.setattr(reads.settings, "x402_enabled", True)
    monkeypatch.setattr(reads.settings, "nodereal_api_key", "key")
    monkeypatch.setattr(reads.settings, "agent_network", "bsc")
    monkeypatch.setattr(reads.settings, "agent_id", 1313)
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t",
            "cumulative_swaps": 9,
            "trade_floor_min": 7,
            "x402_dex": {"symbol": "BNB", "price_usd": 612.5},
        }
    ]
    p = reads.pillars_card(rows)
    assert p["cmc"]["x402_enabled"] is True and p["cmc"]["pay_wallet"] == "0xWALLET"
    assert p["cmc"]["base_usdc_balance"] == 0.99 and p["cmc"]["last_dex"]["symbol"] == "BNB"
    assert p["twak"]["cumulative_swaps"] == 9 and p["twak"]["trade_floor"] == 7
    assert p["nodereal"]["reachable"] is True and p["nodereal"]["chain_id"] == 56
    assert p["nodereal"]["sponsorable"] is False and p["nodereal"]["agent_id"] == 1313


def test_pillars_card_degrades_when_unconfigured(monkeypatch):
    monkeypatch.setattr(reads, "_pillars_net", lambda: {})  # nothing reachable
    monkeypatch.setattr(reads.settings, "x402_enabled", False)
    monkeypatch.setattr(reads.settings, "nodereal_api_key", "")
    p = reads.pillars_card([])
    assert p["cmc"]["x402_enabled"] is False and p["cmc"]["pay_wallet"] is None
    assert p["nodereal"]["api_key_set"] is False and p["nodereal"]["reachable"] is None
    assert p["nodereal"]["note"] is None  # no link → no note, but no crash


# --------------------------- vlayer: data provenance ---------------------- #
def test_provenance_card_off_by_default(monkeypatch):
    # OFF (default) → {enabled: False}, no proof fields, and NO network read (key-free path).
    monkeypatch.setattr(reads.settings, "vlayer_enabled", False)
    monkeypatch.setattr(reads.settings, "vlayer_verifier_address", "")
    card = reads._provenance_card()
    assert card == {"enabled": False}


def test_pillars_commerce_includes_provenance(monkeypatch):
    # The commerce block always carries a provenance sub-block so the dashboard can show a
    # 'pending' vs 'off' state — present and enabled=False when vlayer is unconfigured.
    monkeypatch.setattr(reads.settings, "vlayer_enabled", False)
    monkeypatch.setattr(reads.settings, "vlayer_verifier_address", "")
    monkeypatch.setattr(reads, "_pillars_net", lambda: {})
    prov = reads.pillars_card([])["commerce"]["provenance"]
    assert prov is not None and prov["enabled"] is False
    assert "attestation" not in prov  # no proof fields until an on-chain attestation exists


# --------------------------- market data hub (free) ----------------------- #
def test_market_data_hub_card_free(monkeypatch):
    monkeypatch.setattr(reads.settings, "free_data", True)
    monkeypatch.setattr("ictbot.data.free_overview.free_market_overview",
                        lambda: {"regime": "neutral", "btc_dominance": 55.0})
    monkeypatch.setattr("ictbot.data.dexscreener.dex_signals",
                        lambda syms, **kw: {"BNB": {"price_usd": 1.0}})
    card = reads.market_data_hub_card()
    assert card["enabled"] is True
    assert card["overview"]["regime"] == "neutral" and card["overview"]["btc_dominance"] == 55.0
    assert "BNB" in card["dex"]
    assert set(card["sources"]) >= {"binance", "alternative.me", "dexscreener", "coingecko"}


def test_market_data_hub_card_off_when_not_free(monkeypatch):
    monkeypatch.setattr(reads.settings, "free_data", False)
    assert reads.market_data_hub_card() is None  # gated -> None, no network


# --------------------------- strategy: active tokens ---------------------- #
def test_strategy_card_exposes_universe_and_active(monkeypatch):
    from ictbot.runtime import active_tokens
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    monkeypatch.setattr(active_tokens, "load", lambda: ["BNB", "ETH", "CAKE"])
    card = reads.strategy_card()
    assert card is not None
    assert card["tokens"] == list(CONTEST_TOKENS)
    assert card["active"] == ["BNB", "ETH", "CAKE"]
    assert "of 3" in card["summary"]  # summary tracks the ACTIVE count


def test_rebalances_card_passes_through_active_tokens():
    # Journal order: oldest first; the card reverses to newest-first.
    rows = [
        {"event": "REBALANCE", "ts": "t0", "nav_after": 999.0},  # pre-toggle row
        {"event": "REBALANCE", "ts": "t1", "nav_after": 1000.0, "active_tokens": ["BNB", "ETH"]},
    ]
    card = reads.rebalances_card(rows=rows)
    assert card["items"][0]["active_tokens"] == ["BNB", "ETH"]
    assert card["items"][1]["active_tokens"] is None


# --------------------------- PnL campaign: profit-lock surfacing ----------- #
def test_rebalances_card_passes_through_profit_lock():
    pl = {
        "enabled": True,
        "armed": True,
        "locked": False,
        "campaign_start_nav": 1000.0,
        "cum_ret": 0.052,
        "peak_since_trigger": 1052.0,
        "lock_floor": 1030.0,
    }
    rows = [
        {"event": "REBALANCE", "ts": "t0", "nav_after": 999.0},  # campaign off → None
        {"event": "REBALANCE", "ts": "t1", "nav_after": 1052.0, "profit_lock": pl},
    ]
    items = reads.rebalances_card(rows=rows)["items"]  # newest-first
    assert items[0]["profit_lock"]["armed"] is True
    assert items[0]["profit_lock"]["cum_ret"] == 0.052
    assert items[1]["profit_lock"] is None  # old row, no campaign


def test_state_card_surfaces_profit_lock_from_state(monkeypatch):
    # The campaign anchor in the persisted state IS the signal — derived without
    # settings, so it works zero-secret on the cloud.
    rows = [
        {
            "event": "REBALANCE",
            "ts": "t1",
            "nav_after": 1052.0,
            "weights_after": {},
            "cumulative_swaps": 6,
        }
    ]
    monkeypatch.setattr(
        reads,
        "read_state",
        lambda: {
            "hwm": 1052.0,
            "halted": False,
            "balances": {"USDT": 1052.0},
            "campaign_start_nav": 1000.0,
            "profit_lock_armed": True,
            "profit_locked": False,
            "peak_since_trigger": 1052.0,
            "lock_floor": 1030.0,
        },
    )
    pl = reads.state_card(rows)["profit_lock"]
    assert pl is not None
    assert pl["armed"] is True and pl["locked"] is False
    assert pl["cum_ret"] == 0.052  # (1052/1000 - 1)
    assert pl["lock_floor"] == 1030.0


def test_state_card_profit_lock_none_without_anchor(monkeypatch):
    rows = [{"event": "REBALANCE", "ts": "t1", "nav_after": 1000.0, "weights_after": {}}]
    monkeypatch.setattr(
        reads, "read_state", lambda: {"hwm": 1000.0, "halted": False, "balances": {}}
    )
    assert reads.state_card(rows)["profit_lock"] is None


def test_state_out_schema_accepts_profit_lock():
    # the pydantic StateOut model must round-trip the new field (snapshot validation)
    from ictbot.api.schemas import StateOut

    s = StateOut(nav=1052.0, profit_lock={"armed": True, "locked": False, "cum_ret": 0.052})
    assert s.profit_lock["armed"] is True


def test_snapshot_parses_new_campaign_event_types(monkeypatch):
    # PROFIT_LOCK / PROFIT_LOCK_ARMED / CAMPAIGN_ANCHOR / FLOOR_NUDGE rows must not
    # break the snapshot parse (reads filter by event == REBALANCE; others ignored).
    rows = [
        {"event": "CAMPAIGN_ANCHOR", "ts": "t0", "campaign_start_nav": 1000.0, "source": "cli"},
        {
            "event": "REBALANCE",
            "ts": "t1",
            "nav_after": 1052.0,
            "weights_after": {},
            "profit_lock": {"enabled": True, "armed": True, "locked": False, "cum_ret": 0.052},
        },
        {"event": "PROFIT_LOCK_ARMED", "ts": "t2", "nav": 1052.0, "cum_ret": 0.052},
        {"event": "PROFIT_LOCK", "ts": "t3", "kind": "bank", "nav": 1101.0, "cum_ret": 0.101},
        {"event": "FLOOR_NUDGE", "ts": "t4", "daily": True, "banked": 2},
    ]
    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: rows)
    monkeypatch.setattr(
        reads,
        "read_state",
        lambda: {"hwm": 1101.0, "halted": False, "balances": {}, "campaign_start_nav": 1000.0},
    )
    monkeypatch.setattr(reads, "_pillars_net", lambda: {})
    snap = reads.snapshot()
    # only the REBALANCE row surfaces as a rebalance item, carrying profit_lock
    items = snap["rebalances"]["items"]
    assert len(items) == 1 and items[0]["profit_lock"]["armed"] is True
    assert snap["state"]["profit_lock"]["locked"] is False


# ----------------------- strategies: stability grade badge ---------------- #
def test_strategies_card_merges_stability_grade(monkeypatch):
    """strategies_card surfaces the stability grade per arm, with alias inheritance."""
    from ictbot.runtime import stability_grades, strategy_select, verdicts

    monkeypatch.setattr(verdicts, "load", lambda: {})
    monkeypatch.setattr(strategy_select, "load", lambda d: d)
    monkeypatch.setattr(
        stability_grades, "load", lambda: {"dual_momentum": {"grade": "ROBUST", "ts": "t"}}
    )
    by = {it["name"]: it for it in reads.strategies_card()["items"]}
    assert by["dual_momentum"]["stability"]["grade"] == "ROBUST"
    # BNB_STRATEGY_03 → dual_momentum: the alias inherits the target's grade
    assert by["BNB_STRATEGY_03"]["stability"]["grade"] == "ROBUST"
    # an arm with no grade and no aliased grade → None (no badge)
    assert by["momentum"]["stability"] is None


# --------------------------- token rotation card -------------------------- #
# Per-token "has it been traded" — momentum holdings (weights_after>0) UNION the
# contest-floor nudges (FLOOR_NUDGE "tokens"); honest source labels, never an edge claim.
def _rot(rows):
    return reads.token_rotation_card(rows)


def test_token_rotation_card_held_and_nudged_union():
    rows = [
        {"event": "REBALANCE", "ts": "t1", "weights_after": {"BNB": 0.4, "CAKE": 0.3}},
        {"event": "FLOOR_NUDGE", "ts": "t2", "tokens": ["ETH", "LINK"]},
    ]
    c = _rot(rows)
    assert c["total"] == 8
    assert c["touched_count"] == 4
    assert c["held"] == ["BNB", "CAKE"]
    assert c["nudged"] == ["ETH", "LINK"]
    by = {t["token"]: t for t in c["tokens"]}
    assert by["BNB"]["source"] == "held" and by["BNB"]["touched"]
    assert by["ETH"]["source"] == "nudged" and by["ETH"]["touched"]
    assert by["UNI"]["source"] == "none" and not by["UNI"]["touched"]


def test_token_rotation_card_source_both_when_held_and_nudged():
    rows = [
        {"event": "REBALANCE", "ts": "t1", "weights_after": {"BNB": 0.5}},
        {"event": "FLOOR_NUDGE", "ts": "t2", "tokens": ["BNB"]},
    ]
    by = {t["token"]: t for t in _rot(rows)["tokens"]}
    assert by["BNB"]["source"] == "both"
    assert by["BNB"]["count"] == 2  # one held-tick + one nudge


def test_token_rotation_card_counts_and_latest_ts():
    rows = [
        {"event": "REBALANCE", "ts": "t1", "weights_after": {"BNB": 0.4}},
        {"event": "REBALANCE", "ts": "t2", "weights_after": {"BNB": 0.4}},
        {"event": "FLOOR_NUDGE", "ts": "t3", "tokens": ["BNB"]},
    ]
    by = {t["token"]: t for t in _rot(rows)["tokens"]}
    assert by["BNB"]["count"] == 3
    assert by["BNB"]["last_ts"] == "t3"  # most recent across held + nudged


def test_token_rotation_card_ignores_zero_weight_and_legacy_rows():
    rows = [
        {"event": "REBALANCE", "ts": "t1", "weights_after": {"BNB": 0.0, "CAKE": 0.3}},
        {"event": "FLOOR_NUDGE", "ts": "t2"},  # legacy row: no "tokens" key
    ]
    c = _rot(rows)
    assert c["held"] == ["CAKE"]  # BNB at 0 weight is not "held"
    assert c["nudged"] == []  # legacy FLOOR_NUDGE w/o "tokens" contributes nothing
    assert c["touched_count"] == 1


def test_snapshot_token_rotation_survives_response_model(monkeypatch):
    # Regression (api-response-model-strips-fields): SnapshotOut must DECLARE token_rotation, else
    # FastAPI's response_model silently strips it from /api/snapshot. The static snapshot.json is
    # written from the RAW dict (bypassing Pydantic), so only a through-SnapshotOut check catches this.
    from ictbot.api.schemas import SnapshotOut

    rows = [
        {"event": "REBALANCE", "ts": "t1", "weights_after": {"BNB": 0.4}},
        {"event": "FLOOR_NUDGE", "ts": "t2", "tokens": ["ETH", "LINK"]},
    ]
    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: rows)
    monkeypatch.setattr(reads, "_pillars_net", lambda: {})  # no network
    snap = reads.snapshot()
    assert snap["token_rotation"]["touched_count"] == 3  # raw dict carries it
    out = SnapshotOut(**snap).model_dump()
    assert out["token_rotation"] is not None, "response_model stripped token_rotation!"
    assert out["token_rotation"]["touched_count"] == 3
    assert out["token_rotation"]["held"] == ["BNB"]
    assert out["token_rotation"]["nudged"] == ["ETH", "LINK"]
