"""
Tier 4 / Phase 5 — boot guard tests.

Covers Fix 5.H (MAX_OPEN_POSITIONS env override) and Fix 5.I (pre-boot
API-key sanity check). The settings module's boot guards are
side-effects at import; we test via subprocess invocations so each
case starts with a clean environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"


def _run_settings(print_expr="s.MAX_OPEN_POSITIONS", **env_overrides):
    """Spawn a fresh Python that imports ictbot.settings and prints
    a marker. Returns (returncode, stdout, stderr).

    `print_expr` is evaluated in a context with `s` aliased to
    `ictbot.settings` — defaults to MAX_OPEN_POSITIONS for the
    Phase 5.H tests below; Phase 13 tests pass `s.DAILY_LOSS_LIMIT_R`
    / `s.MAX_DRAWDOWN_FRAC` so the assertion can target the new fields.

    Runs from a temp cwd to avoid picking up the real .env file —
    pydantic-settings reads it by default and would otherwise mask
    the env overrides this test is setting."""
    env = os.environ.copy()
    # Strip any locally-set live-trading overrides so the test starts clean.
    for k in (
        "ENABLE_LIVE_TRADING",
        "EXCHANGE",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "DELTA_API_KEY",
        "DELTA_API_SECRET",
        "MAX_OPEN_POSITIONS",
        "DAILY_LOSS_LIMIT_R",
        "MAX_DRAWDOWN_FRAC",
        "MAX_LIVE_TRADES_PER_DAY",
        "TWAK_MODE",
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TW_ACCESS_ID",
        "TW_HMAC_SECRET",
        "NODEREAL_API_KEY",
        "AGENT_HEARTBEAT_ENABLED",
        "AGENT_USE_PAYMASTER",
        "CMC_DAILY_CREDIT_BUDGET",
        "CMC_MONTHLY_CREDIT_BUDGET",
        "CMC_RATE_LIMIT_RPM",
        "TG_CONFIRM_MODE",
        "TG_COMMANDS_MODE",
    ):
        env.pop(k, None)
    # Phase 15 test isolation: tell pydantic-settings to skip the
    # operator's real .env so values like MAX_OPEN_POSITIONS=9999 in
    # the local dev .env don't leak into default-value assertions.
    env["ICTBOT_SKIP_DOTENV"] = "1"
    env.update(env_overrides)
    # PYTHONPATH so the subprocess can find the ictbot module even
    # when running from a temp directory.
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [py, "-c", f"import ictbot.settings as s; print('OK', {print_expr})"],
            cwd=tmpdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    return result.returncode, result.stdout, result.stderr


# ---- Fix 5.H: MAX_OPEN_POSITIONS env override ----------------------------


def test_max_open_positions_default_is_three():
    """Fix 9.B (plan: Phase 9 per-token completeness): default raised
    1 → 3 so 3 of 5 pairs can hold positions simultaneously. The prior
    default starved 4 of 5 pairs whenever one position was open."""
    rc, out, err = _run_settings()
    assert rc == 0
    assert "OK 3" in out


def test_max_open_positions_env_override():
    rc, out, err = _run_settings(MAX_OPEN_POSITIONS="5")
    assert rc == 0
    assert "OK 5" in out


# ---- Fix 5.I: API-key boot guard -----------------------------------------


def test_boot_guard_refuses_live_binance_without_keys():
    """ENABLE_LIVE_TRADING=true with EXCHANGE=binance and no keys
    must refuse to boot, NOT lazily fail on first ccxt call.

    Note: pydantic-settings reads .env if present, so we explicitly
    set empty values to override whatever the developer's .env
    contains."""
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="true",
        EXCHANGE="binance",
        BINANCE_API_KEY="",
        BINANCE_API_SECRET="",
    )
    assert rc != 0
    assert "BINANCE_API_KEY" in err or "BINANCE_API_SECRET" in err


def test_boot_guard_refuses_live_delta_without_keys():
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="true",
        EXCHANGE="delta",
        DELTA_API_KEY="",
        DELTA_API_SECRET="",
    )
    assert rc != 0
    assert "DELTA_API_KEY" in err or "DELTA_API_SECRET" in err


def test_boot_guard_allows_live_when_keys_present():
    """Live mode with both key + secret is fine; boot should succeed.
    (We don't ACTUALLY hit the exchange — just confirm the import
    doesn't raise.)"""
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="true",
        EXCHANGE="binance",
        BINANCE_API_KEY="testkey",
        BINANCE_API_SECRET="testsecret",
        BINANCE_TESTNET="true",
    )
    assert rc == 0, f"unexpected boot failure: {err}"


def test_boot_guard_skips_check_when_live_off():
    """ENABLE_LIVE_TRADING=false means no API keys are required —
    paper / backtest / staging shouldn't need creds."""
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="false",
        EXCHANGE="binance",
        # NO keys set
    )
    assert rc == 0, f"unexpected boot failure: {err}"


def test_boot_guard_refuses_live_when_only_secret_missing():
    """Both key AND secret are required. Half a credential pair is
    still a misconfiguration."""
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="true",
        EXCHANGE="binance",
        BINANCE_API_KEY="present",
        BINANCE_API_SECRET="",
    )
    assert rc != 0
    assert "BINANCE_API_SECRET" in err


# ---- Fix 13.A: DAILY_LOSS_LIMIT_R env override + boot guard ----


def test_daily_loss_limit_r_default_is_one():
    """Fix 13.A: historical default `DailyLossLimit(limit_R=1.0)` is
    preserved when no env override."""
    rc, out, err = _run_settings(print_expr="s.DAILY_LOSS_LIMIT_R")
    assert rc == 0
    assert "OK 1.0" in out


def test_daily_loss_limit_r_env_override():
    rc, out, err = _run_settings(print_expr="s.DAILY_LOSS_LIMIT_R", DAILY_LOSS_LIMIT_R="2.5")
    assert rc == 0
    assert "OK 2.5" in out


def test_daily_loss_limit_r_boot_refuses_zero():
    """Zero or negative = no cap. Boot guard catches this."""
    rc, out, err = _run_settings(print_expr="s.DAILY_LOSS_LIMIT_R", DAILY_LOSS_LIMIT_R="0")
    assert rc != 0
    assert "DAILY_LOSS_LIMIT_R" in err


def test_daily_loss_limit_r_boot_refuses_negative():
    rc, out, err = _run_settings(print_expr="s.DAILY_LOSS_LIMIT_R", DAILY_LOSS_LIMIT_R="-1")
    assert rc != 0
    assert "DAILY_LOSS_LIMIT_R" in err


# ---- Fix 13.B: MAX_DRAWDOWN_FRAC env override + boot guard ----


def test_max_drawdown_frac_default_is_five_percent():
    rc, out, err = _run_settings(print_expr="s.MAX_DRAWDOWN_FRAC")
    assert rc == 0
    assert "OK 0.05" in out


def test_max_drawdown_frac_env_override():
    rc, out, err = _run_settings(print_expr="s.MAX_DRAWDOWN_FRAC", MAX_DRAWDOWN_FRAC="0.10")
    assert rc == 0
    assert "OK 0.1" in out


def test_max_drawdown_frac_boot_refuses_zero():
    """Zero = no cap; ≥ 1.0 = nonsensical. Both refuse to boot."""
    rc, out, err = _run_settings(print_expr="s.MAX_DRAWDOWN_FRAC", MAX_DRAWDOWN_FRAC="0")
    assert rc != 0
    assert "MAX_DRAWDOWN_FRAC" in err


def test_max_drawdown_frac_boot_refuses_one_or_more():
    rc, out, err = _run_settings(print_expr="s.MAX_DRAWDOWN_FRAC", MAX_DRAWDOWN_FRAC="1.0")
    assert rc != 0
    assert "MAX_DRAWDOWN_FRAC" in err


# ---- BNB contest: TWAK live-mode credential guard ------------------------


def test_boot_guard_refuses_twak_live_without_creds():
    """TWAK_MODE=live signs real BSC swaps — refuse to boot without TWAK creds."""
    rc, out, err = _run_settings(
        print_expr="s.settings.twak_mode",
        TWAK_MODE="live",
        TWAK_ACCESS_ID="",
        TWAK_HMAC_SECRET="",
    )
    assert rc != 0
    assert "TWAK_ACCESS_ID" in err or "TWAK_HMAC_SECRET" in err


def test_boot_guard_allows_twak_live_with_creds():
    rc, out, err = _run_settings(
        print_expr="s.settings.twak_mode",
        TWAK_MODE="live",
        TWAK_ACCESS_ID="aid",
        TWAK_HMAC_SECRET="hs",
    )
    assert rc == 0, f"unexpected boot failure: {err}"


def test_boot_guard_twak_sim_needs_no_creds():
    rc, out, err = _run_settings(print_expr="s.settings.twak_mode", TWAK_MODE="sim")
    assert rc == 0, f"unexpected boot failure: {err}"


# ---- E1: scope the CEX-creds guard off the TWAK-live contest path ---------


def test_boot_guard_twak_live_path_skips_cex_creds():
    """E1: ENABLE_LIVE_TRADING=true on the TWAK-live contest path must NOT demand a
    CEX key (the contest agent trades via TWAK, uses no CEX) — so a submission-clean
    .env (legacy CEX keys removed) still boots. The TWAK guard covers this path."""
    rc, out, err = _run_settings(
        print_expr="s.settings.twak_mode",
        ENABLE_LIVE_TRADING="true",
        TWAK_MODE="live",
        TWAK_ACCESS_ID="aid",
        TWAK_HMAC_SECRET="hs",
        EXCHANGE="delta",
        DELTA_API_KEY="",
        DELTA_API_SECRET="",  # no CEX creds present
    )
    assert rc == 0, f"TWAK-live path should not require CEX creds: {err}"


def test_boot_guard_twak_live_still_requires_twak_creds_under_live_trading():
    """E1: scoping the CEX guard must NOT let the TWAK-live path boot without TWAK creds
    — the dedicated TWAK guard must still fire."""
    rc, out, err = _run_settings(
        print_expr="s.settings.twak_mode",
        ENABLE_LIVE_TRADING="true",
        TWAK_MODE="live",
        TWAK_ACCESS_ID="",
        TWAK_HMAC_SECRET="",
    )
    assert rc != 0
    assert "TWAK_ACCESS_ID" in err or "TWAK_HMAC_SECRET" in err


def test_boot_guard_cex_live_path_still_requires_cex_creds():
    """E1: the legacy CEX live path (twak_mode=sim) STILL demands CEX creds — the
    scoping only exempts the TWAK-live path, not all live trading."""
    rc, out, err = _run_settings(
        ENABLE_LIVE_TRADING="true",
        TWAK_MODE="sim",
        EXCHANGE="delta",
        DELTA_API_KEY="",
        DELTA_API_SECRET="",
    )
    assert rc != 0
    assert "DELTA_API_KEY" in err or "DELTA_API_SECRET" in err


# ---- BNB pillar 3: gasless-heartbeat sponsor guard -----------------------


def test_boot_guard_refuses_heartbeat_without_nodereal_key():
    """AGENT_HEARTBEAT_ENABLED=true with the paymaster on needs NODEREAL_API_KEY —
    otherwise gasless writes hit the PUBLIC MegaFuel endpoint and the user's keyed
    sponsor app records nothing (the original zero-requests bug)."""
    rc, out, err = _run_settings(
        print_expr="s.settings.agent_heartbeat_enabled",
        AGENT_HEARTBEAT_ENABLED="true",
        AGENT_USE_PAYMASTER="true",
        NODEREAL_API_KEY="",
    )
    assert rc != 0
    assert "NODEREAL_API_KEY" in err


def test_boot_guard_allows_heartbeat_with_nodereal_key():
    rc, out, err = _run_settings(
        print_expr="s.settings.agent_heartbeat_enabled",
        AGENT_HEARTBEAT_ENABLED="true",
        AGENT_USE_PAYMASTER="true",
        NODEREAL_API_KEY="key123",
    )
    assert rc == 0, f"unexpected boot failure: {err}"


def test_boot_guard_heartbeat_off_needs_no_nodereal_key():
    rc, out, err = _run_settings(
        print_expr="s.settings.agent_heartbeat_enabled",
        AGENT_HEARTBEAT_ENABLED="false",
        NODEREAL_API_KEY="",
    )
    assert rc == 0, f"unexpected boot failure: {err}"


# ---- CMC commercial-tier budget guard ------------------------------------


def test_cmc_budget_defaults_boot_ok():
    """Default soft budgets sit under the Startup hard caps — boots fine."""
    rc, out, err = _run_settings(print_expr="s.settings.cmc_daily_credit_budget")
    assert rc == 0, f"unexpected boot failure: {err}"
    assert "OK 9000" in out


def test_boot_guard_refuses_daily_budget_over_cap():
    """A SOFT daily budget above the ~10k/day hard cap would risk overage billing."""
    rc, out, err = _run_settings(
        print_expr="s.settings.cmc_daily_credit_budget",
        CMC_DAILY_CREDIT_BUDGET="20000",
    )
    assert rc != 0
    assert "CMC_DAILY_CREDIT_BUDGET" in err


def test_boot_guard_refuses_monthly_budget_over_cap():
    rc, out, err = _run_settings(
        print_expr="s.settings.cmc_monthly_credit_budget",
        CMC_MONTHLY_CREDIT_BUDGET="500000",
    )
    assert rc != 0
    assert "CMC_MONTHLY_CREDIT_BUDGET" in err


def test_boot_guard_refuses_rpm_over_cap():
    rc, out, err = _run_settings(
        print_expr="s.settings.cmc_rate_limit_rpm",
        CMC_RATE_LIMIT_RPM="120",
    )
    assert rc != 0
    assert "CMC_RATE_LIMIT_RPM" in err
