"""CliTwakClient command-construction tests (subprocess mocked — no twak needed)."""

from __future__ import annotations

import json
import subprocess

import pytest

from ictbot.exec.twak_client import _READ_RETRIES, BSC_TOKENS, CliTwakClient


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _patch(monkeypatch, payload, rc=0):
    calls: dict = {}

    def fake_run(argv, **kw):
        calls["argv"] = argv
        calls["env"] = kw.get("env")
        return _FakeProc(json.dumps(payload), rc)

    monkeypatch.setattr("ictbot.exec.twak_client.subprocess.run", fake_run)
    return calls


def _client(price_fn=None):
    # Pin an explicit address + binary so the argv is deterministic regardless of the ambient
    # .env (AGENT_TRADING_ADDRESS / TWAK_BINARY — a cron .env sets the absolute nvm path).
    return CliTwakClient(
        binary="twak",
        access_id="aid",
        hmac_secret="hs",
        wallet_password="pw",
        address="0x000000000000000000000000000000000000dEaD",
        price_fn=price_fn,
    )


def test_price_builds_argv_and_injects_creds(monkeypatch):
    calls = _patch(monkeypatch, {"token": "BNB", "chain": "bsc", "priceUsd": 596.3})
    px = _client().price("BNB")
    assert px == 596.3
    assert calls["argv"] == ["twak", "price", "BNB", "--chain", "bsc", "--json"]
    assert calls["env"]["TWAK_ACCESS_ID"] == "aid"
    assert calls["env"]["TWAK_HMAC_SECRET"] == "hs"
    assert calls["env"]["TWAK_WALLET_PASSWORD"] == "pw"


def test_usdt_price_is_one_no_call(monkeypatch):
    _patch(monkeypatch, {})
    assert _client().price("USDT") == 1.0


def test_swap_quote_uses_quote_only_and_parses_output(monkeypatch):
    calls = _patch(
        monkeypatch, {"output": "0.16675 BNB", "minReceived": "0.165 BNB", "provider": "LiquidMesh"}
    )
    res = _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=False)
    assert res.amount_to == pytest.approx(0.16675, abs=1e-5)
    assert "--quote-only" in calls["argv"]
    assert "--password" not in calls["argv"]
    assert calls["argv"][:5] == ["twak", "swap", "100.0000000000", "USDT", "BNB"]


def test_swap_execute_passes_wallet_password(monkeypatch):
    calls = _patch(monkeypatch, {"output": "0.16 BNB", "txHash": "0xabc"})
    res = _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    assert "--password" in calls["argv"] and "pw" in calls["argv"]
    assert "--quote-only" not in calls["argv"]
    assert res.tx == "0xabc" and res.ok


def test_swap_execute_appends_slippage_flag(monkeypatch):
    # A1: a live execute passes --slippage explicitly (default 1.0); a quote does not.
    calls = _patch(monkeypatch, {"output": "0.16 BNB", "txHash": "0xabc"})
    _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    argv = calls["argv"]
    assert "--slippage" in argv
    assert argv[argv.index("--slippage") + 1] == "1.0"

    calls = _patch(monkeypatch, {"output": "0.16 BNB"})
    _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=False)
    assert "--slippage" not in calls["argv"]  # quote never appends it


def test_swap_slippage_flag_suppressed_when_empty(monkeypatch):
    # A1: an empty TWAK_SLIPPAGE_FLAG disables the flag entirely (trivially disableable).
    from ictbot.settings import settings

    monkeypatch.setattr(settings, "twak_slippage_flag", "")
    calls = _patch(monkeypatch, {"output": "0.16 BNB", "txHash": "0xabc"})
    _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    assert "--slippage" not in calls["argv"]


def test_swap_parses_execute_field_name_variants(monkeypatch):
    # execute responses may use amountOut/transactionHash/feeUSD instead of output/txHash/feeUsd
    _patch(monkeypatch, {"amountOut": "0.16 BNB", "transactionHash": "0xdef", "feeUSD": 0.01})
    res = _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    assert res.amount_to == pytest.approx(0.16, abs=1e-6)
    assert res.tx == "0xdef"
    assert res.fee_paid == pytest.approx(0.01)
    assert res.ok


def _legacy_off(monkeypatch):
    """Force the legacy twak-CLI balance path (the Multicall3 read flag now defaults True)."""
    from ictbot.settings import settings

    monkeypatch.setattr(settings, "alloc_balance_via_multicall3", False)


def test_balance_native_has_no_token_or_coin(monkeypatch):
    # native BNB balance = --chain/--address only (no --token/--coin); value under "available"
    _legacy_off(monkeypatch)
    calls = _patch(monkeypatch, {"available": "1.5", "symbol": "BNB"})
    bal = _client().balance("BNB")
    assert bal == pytest.approx(1.5)
    assert "--token" not in calls["argv"] and "--coin" not in calls["argv"]
    assert "--address" in calls["argv"]


def test_balance_erc20_uses_token_address(monkeypatch):
    _legacy_off(monkeypatch)
    calls = _patch(monkeypatch, {"available": "12.0"})
    bal = _client().balance("ETH")
    assert bal == pytest.approx(12.0)
    assert "--token" in calls["argv"] and BSC_TOKENS["ETH"] in calls["argv"]


def test_error_payload_raises(monkeypatch):
    _legacy_off(monkeypatch)  # this asserts the twak-CLI read path's error handling
    _patch(monkeypatch, {"error": "No wallet password found", "errorCode": "PASSWORD_MISSING"})
    with pytest.raises(RuntimeError, match="wallet password"):
        _client().balance("ETH")


# --------------------------- _run() retry / backoff / classification ------------------------- #
def _seq_run(monkeypatch, actions):
    """fake subprocess.run consuming `actions` ('timeout' | (returncode, payload_dict|stderr_str)).
    Records (mocked) sleeps so backoff is asserted without real waits."""
    sleeps: list[float] = []
    monkeypatch.setattr("ictbot.exec.twak_client.time.sleep", lambda s: sleeps.append(s))
    it = iter(actions)

    def fake_run(argv, **kw):
        a = next(it)
        if a == "timeout":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=180)
        rc, body = a
        return (
            _FakeProc(json.dumps(body), rc)
            if isinstance(body, dict)
            else _FakeProc("", rc, stderr=body)
        )

    monkeypatch.setattr("ictbot.exec.twak_client.subprocess.run", fake_run)
    return sleeps


def test_run_retries_timeout_then_succeeds(monkeypatch):
    sleeps = _seq_run(monkeypatch, ["timeout", (0, {"priceUsd": 596.0})])
    assert _client().price("BNB") == 596.0  # price() -> _run("price") (no price_fn)
    assert len(sleeps) == 1  # one backoff slept, then success


def test_run_retries_transient_error_then_succeeds(monkeypatch):
    sleeps = _seq_run(
        monkeypatch, [(0, {"error": "rpc 503 unavailable"}), (0, {"priceUsd": 596.0})]
    )
    assert _client().price("BNB") == 596.0  # transient error payload -> retry -> ok
    assert len(sleeps) == 1


def test_run_retries_balance_fetch_hiccup_then_succeeds(monkeypatch):
    # twak's transient balance/router hiccup ("Could not fetch balance. Please try again
    # later.") must RETRY, not abort the whole tick — the NAV/weights read happens BEFORE
    # any swap, so a momentary RPC miss should not cost a run.
    sleeps = _seq_run(
        monkeypatch,
        [(0, {"error": "Could not fetch balance. Please try again later."}),
         (0, {"priceUsd": 596.0})],
    )
    assert _client().price("BNB") == 596.0  # transient balance hiccup -> retry -> ok
    assert len(sleeps) == 1


def test_run_permanent_error_raises_without_retry(monkeypatch):
    sleeps = _seq_run(monkeypatch, [(0, {"error": "No wallet password found"})])
    with pytest.raises(RuntimeError, match="wallet password"):
        _client().price("BNB")
    assert sleeps == []  # permanent -> NO backoff/retry (fail fast)


def test_run_exhausts_transient_retries_and_raises(monkeypatch):
    # price() is a READ -> _READ_RETRIES budget; exhaust it with all-transient results.
    sleeps = _seq_run(monkeypatch, [(0, {"error": "503"})] * (_READ_RETRIES + 1))
    with pytest.raises(RuntimeError, match="failed"):
        _client().price("BNB")  # _READ_RETRIES retries -> _READ_RETRIES+1 attempts, then raise
    assert len(sleeps) == _READ_RETRIES


def test_run_exhausts_timeout_retries_and_raises(monkeypatch):
    sleeps = _seq_run(monkeypatch, ["timeout"] * (_READ_RETRIES + 1))
    with pytest.raises(RuntimeError, match="timeout"):
        _client().price("BNB")  # _READ_RETRIES consecutive timeouts -> raise
    assert len(sleeps) == _READ_RETRIES


def test_read_path_retries_beyond_default_swap_budget(monkeypatch):
    # The READ path (price/balance) gets the generous _READ_RETRIES budget (> the swap default of
    # 2): a transient that fails 3x then succeeds must RESOLVE on a read — it would EXHAUST the
    # 2-retry swap budget. Locks in the read-vs-swap retry split (the live-tick balance hardening).
    assert _READ_RETRIES >= 3
    sleeps = _seq_run(monkeypatch, [(0, {"error": "503"})] * 3 + [(0, {"priceUsd": 591.0})])
    assert _client().price("BNB") == 591.0  # survived 3 transient hiccups on the read path
    assert len(sleeps) == 3


# --------------------------- swap() silent-degradation branches ------------------------------ #
def test_swap_ok_but_price_raises_yields_zero_price(monkeypatch):
    # a fully-valid swap (amount + tx, ok=True) where the price read raises -> price=0.0, NOT a crash.
    _patch(monkeypatch, {"output": "0.16 BNB", "txHash": "0xabc"})

    def boom(_t):
        raise RuntimeError("price feed down")

    res = _client(price_fn=boom).swap("USDT", "BNB", 100.0, execute=True)
    assert res.ok is True and res.tx == "0xabc" and res.price == 0.0


def test_swap_non_numeric_fee_degrades_to_zero(monkeypatch):
    _patch(monkeypatch, {"output": "0.16 BNB", "txHash": "0xabc", "feeUsd": "n/a"})
    res = _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    assert res.ok is True and res.fee_paid == 0.0  # float("n/a") -> ValueError -> 0.0


def test_swap_run_failure_returns_ok_false_not_raise(monkeypatch):
    _patch(monkeypatch, {"error": "No wallet password found"})  # _run raises -> swap catches
    res = _client(price_fn=lambda t: 596.0).swap("USDT", "BNB", 100.0, execute=True)
    assert res.ok is False and "failed" in res.error


# ----------------- Multicall3 balance reads (decouple from the flaky twak RPC) ---------------- #
def _mc3_on(monkeypatch):
    from ictbot.settings import settings

    monkeypatch.setattr(settings, "alloc_balance_via_multicall3", True)


def _stub_onchain(monkeypatch, bals):
    """Stub the Multicall3 reader (raise it if `bals` is an Exception). Returns the call-arg list."""
    calls: list = []

    def fake(addr=None):
        calls.append(addr)
        if isinstance(bals, Exception):
            raise bals
        return dict(bals)

    monkeypatch.setattr("ictbot.api.onchain.read_onchain_balances", fake)
    return calls


def test_balance_via_mc3_routes_to_onchain_no_subprocess(monkeypatch):
    # With the flag on, balance()/balances() read via Multicall3 and NEVER shell out to twak balance.
    _mc3_on(monkeypatch)
    _stub_onchain(monkeypatch, {"USDT": 5.0, "BNB": 0.003, "UNI": 1.8, "ETH": 0.0})

    def _boom(*a, **k):
        raise AssertionError("twak balance must NOT shell out under the Multicall3 read path")

    monkeypatch.setattr("ictbot.exec.twak_client.subprocess.run", _boom)
    c = _client()
    assert c._balance_via_mc3 is True
    assert c.balance("USDT") == pytest.approx(5.0)
    assert c.balance("UNI") == pytest.approx(1.8)
    assert c.balance("ETH") == 0.0
    assert c.balance("DOGE") == 0.0  # symbol absent from the read -> 0.0
    assert c.balances() == {"USDT": 5.0, "BNB": 0.003, "UNI": 1.8}  # non-zero only


def test_mc3_short_ttl_coalesces_reads(monkeypatch):
    # The many per-token reads in one rebalance pass collapse to a single batched RPC.
    _mc3_on(monkeypatch)
    calls = _stub_onchain(monkeypatch, {"USDT": 9.0, "BNB": 0.004})
    c = _client()
    c.balance("USDT")
    c.balance("BNB")
    c.balances()
    assert len(calls) == 1  # coalesced under the short TTL


def test_mc3_cache_busted_by_execute_swap_only(monkeypatch):
    # An EXECUTE swap busts the cache (post-swap balances changed); a quote-only swap does not.
    _mc3_on(monkeypatch)
    calls = _stub_onchain(monkeypatch, {"USDT": 9.0, "BNB": 0.004})
    _patch(monkeypatch, {"output": "1.0 USDT", "txHash": "0xabc"})  # the swap's CLI fill
    c = _client(price_fn=lambda t: 1.0)

    c.balance("USDT")
    assert len(calls) == 1
    c.swap("UNI", "USDT", 1.0, execute=True)  # -> busts the read cache
    c.balance("USDT")
    assert len(calls) == 2  # re-read after the execute swap

    c.swap("UNI", "USDT", 1.0, execute=False)  # quote-only -> must NOT bust
    c.balance("USDT")
    assert len(calls) == 2  # still served from cache


def test_mc3_hard_failure_propagates_fail_fast(monkeypatch):
    # A sustained RPC outage RAISES (so the tick skips fast) instead of returning an empty book.
    _mc3_on(monkeypatch)
    _stub_onchain(monkeypatch, RuntimeError("no reachable BSC RPC"))
    c = _client()
    with pytest.raises(RuntimeError, match="no reachable BSC RPC"):
        c.balance("USDT")
    with pytest.raises(RuntimeError, match="no reachable BSC RPC"):
        c.balances()
