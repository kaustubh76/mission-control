"""
Regression tests for the pre-contest LIVE-path robustness hardening (the bug class the
heartbeat-AttributeDict crash belonged to — defects that only fire on the REAL live mainnet
execution path, invisible to sim and --quote-only).

Covers:
  Fix 1 — CliTwakClient parser rejects non-finite (NaN/Inf) amounts/fees so a malformed CLI
          response can never pass the ok-gate (`inf > 0` is True) or poison NAV.
  Fix 3 — commerce.journal_commerce coerces non-JSON-serializable SDK fields (HexBytes/AttributeDict)
          instead of silently dropping the on-chain row via its except-pass.
  Fix 2 — TwakSpotBroker._settle settlement slack is OFF by default and never runs in sim/quote-only.
"""

from __future__ import annotations

import json
import math

import pytest

from ictbot.exec.bsc_spot_live import TwakSpotBroker
from ictbot.exec.twak_client import CliTwakClient, SimTwakClient

TOKENS = ("BNB", "ETH")


def _sim_client():
    return SimTwakClient(lambda t: 1.0, start_usdt=100.0)


# --------------------------- Fix 1: non-finite parser guard --------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.5 BNB", 1.5),
        ("2", 2.0),
        ("0.1667 BNB", 0.1667),
        ("inf BNB", 0.0),
        ("Infinity BNB", 0.0),
        ("-inf ETH", 0.0),
        ("nan", 0.0),
        ("NaN CAKE", 0.0),
        ("", 0.0),
        (None, 0.0),
    ],
)
def test_amount_rejects_non_finite(raw, expected):
    assert CliTwakClient._amount(raw) == expected


def test_live_execute_with_infinity_amount_is_not_ok():
    """A real execute whose 'output' is a non-finite value must be recorded ok=False (re-queue),
    NOT a phantom fill — otherwise the journal claims an impossible amount_to and NAV is poisoned."""
    c = CliTwakClient(price_fn=lambda t: 1.0)
    c._run = lambda *a, **k: {"output": "Infinity BNB", "txHash": "0xabc"}  # malformed CLI response
    res = c.swap("USDT", "BNB", 10.0, execute=True)
    assert res.amount_to == 0.0
    assert res.ok is False  # inf bypassed the gate before the fix


def test_live_execute_with_nan_fee_zeroes_fee():
    c = CliTwakClient(price_fn=lambda t: 1.0)
    c._run = lambda *a, **k: {"output": "5 BNB", "txHash": "0xdef", "feeUsd": "NaN"}
    res = c.swap("USDT", "BNB", 10.0, execute=True)
    assert res.ok is True and res.amount_to == 5.0
    assert math.isfinite(res.fee_paid) and res.fee_paid == 0.0


# --------------------------- Fix 3: commerce serialization --------------------------- #
class _FakeHexBytes:
    """Stand-in for a web3 HexBytes/AttributeDict return — NOT JSON-serializable on its own."""

    def __init__(self, b: bytes) -> None:
        self._b = b

    def __repr__(self) -> str:
        return "0x" + self._b.hex()


def test_journal_commerce_coerces_nonserializable(tmp_path, monkeypatch):
    """A bytes-like transactionHash must round-trip to a STRING and write a valid row — not raise
    inside the except-pass and silently drop the SUBMITTED_ONCHAIN record."""
    import ictbot.agent.commerce as commerce

    jf = tmp_path / "commerce_jobs.jsonl"
    monkeypatch.setattr(commerce, "COMMERCE_JOURNAL", jf)
    commerce.journal_commerce("SUBMITTED_ONCHAIN", job_id=42, tx=_FakeHexBytes(b"\xab\xcd"))
    line = jf.read_text(encoding="utf-8").strip()
    assert line, "row was silently dropped"
    row = json.loads(line)  # must parse — i.e. default=str coerced the HexBytes
    assert row["event"] == "SUBMITTED_ONCHAIN" and row["job_id"] == 42
    assert isinstance(row["tx"], str) and "abcd" in row["tx"]


# --------------------------- Fix 2: settlement slack is off-by-default + sim-safe --------------------------- #
def test_settle_noop_by_default_even_on_live(monkeypatch):
    import ictbot.exec.bsc_spot_live as m

    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    b = TwakSpotBroker(_sim_client(), tokens=TOKENS, live=True, live_enabled=True)
    b._settle()
    assert slept == []  # settle_seconds defaults to 0 → no behavior change


def test_settle_sleeps_only_when_enabled_and_live(monkeypatch):
    import ictbot.exec.bsc_spot_live as m

    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    live = TwakSpotBroker(_sim_client(), tokens=TOKENS, live=True, live_enabled=True, settle_seconds=3.0)
    live._settle()
    assert slept == [3.0]
    # A sim broker (live defaults False) never sleeps, even if settle_seconds is set.
    slept.clear()
    TwakSpotBroker(_sim_client(), tokens=TOKENS, settle_seconds=3.0)._settle()
    assert slept == []
