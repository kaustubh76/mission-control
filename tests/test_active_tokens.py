"""UI-controlled token universe (runtime/active_tokens.py): validation,
canonical ordering, and degrade-to-full-universe on every failure mode."""

from __future__ import annotations

import json

import pytest

from ictbot.runtime import active_tokens
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    f = tmp_path / "active_tokens.json"
    monkeypatch.setattr(active_tokens, "ACTIVE_TOKENS_FILE", f)
    # Pin the active-token floor to 2 so these mechanism tests (dedup, canonical
    # order, unknown-filtering, too-few) are independent of the production top_k
    # default; the tests that vary top_k override this themselves.
    monkeypatch.setattr(active_tokens.settings, "alloc_top_k", 2)
    return f


# ------------------------------ save/load ---------------------------------- #
def test_round_trip_canonical_order(tokens_file):
    # Saved out of order; comes back in CONTEST_TOKENS order.
    saved = active_tokens.save(["DOGE", "BNB", "CAKE"])
    assert saved == ["BNB", "CAKE", "DOGE"]
    assert active_tokens.load() == ["BNB", "CAKE", "DOGE"]


def test_save_dedupes_and_uppercases(tokens_file):
    saved = active_tokens.save(["bnb", "BNB", "eth"])
    assert saved == ["BNB", "ETH"]


def test_save_unknown_token_raises(tokens_file):
    with pytest.raises(ValueError, match="unknown token"):
        active_tokens.save(["BNB", "ETH", "XRP"])
    assert not tokens_file.exists()  # nothing persisted on failure


def test_save_too_few_raises(tokens_file):
    with pytest.raises(ValueError, match="at least 2"):
        active_tokens.save(["BNB"])


def test_min_required_tracks_top_k(tokens_file, monkeypatch):
    monkeypatch.setattr(active_tokens.settings, "alloc_top_k", 3)
    assert active_tokens.min_required() == 3
    with pytest.raises(ValueError, match="at least 3"):
        active_tokens.save(["BNB", "ETH"])


def test_save_leaves_no_tmp_file(tokens_file):
    active_tokens.save(["BNB", "ETH"])
    leftovers = [p for p in tokens_file.parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# ------------------------------ degraded loads ----------------------------- #
def test_load_absent_file_returns_full_universe(tokens_file):
    assert active_tokens.load() == list(CONTEST_TOKENS)


def test_load_corrupt_json_returns_full_universe(tokens_file):
    tokens_file.write_text("{not json", encoding="utf-8")
    assert active_tokens.load() == list(CONTEST_TOKENS)


def test_load_now_invalid_returns_full_universe(tokens_file, monkeypatch):
    # Persisted 2 tokens, then ALLOC_TOP_K raised to 3 — file is stale-invalid.
    active_tokens.save(["BNB", "ETH"])
    monkeypatch.setattr(active_tokens.settings, "alloc_top_k", 3)
    assert active_tokens.load() == list(CONTEST_TOKENS)


def test_load_ignores_unknown_tokens_in_file(tokens_file):
    tokens_file.write_text(json.dumps({"active": ["BNB", "ETH", "SHIB"]}), encoding="utf-8")
    assert active_tokens.load() == ["BNB", "ETH"]


def test_universe_is_contest_tokens():
    assert active_tokens.universe() == CONTEST_TOKENS
