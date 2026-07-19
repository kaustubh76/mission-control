"""
Thin wrapper over the Trust Wallet Agent Kit (TWAK) swap surface.

TWAK is the official, self-custody agent kit: in autonomous "agent wallet" mode it
signs spot SWAPS / transfers / DCA / limit-orders on BSC with no per-tx approval.
It is the ONLY signer in this design (the "Best Use of TWAK" thesis). TWAK does
NOT expose perps or arbitrary contract calls to agents, which is fine — the
momentum allocator only ever spot-swaps USDT<->token.

This module isolates that surface behind one small interface so the broker/runtime
never shell out directly and stay unit-testable:

    swap(from_token, to_token, amount_from) -> SwapResult
    balance(token) -> float
    price(token)   -> float

Two implementations:
  - SimTwakClient : paper execution against a price function (fee+slippage modelled).
                    Used for dry-runs, backtests and tests. DEFAULT — never touches a key.
  - CliTwakClient : shells out to the real `twak` CLI (npm `@trustwallet/cli`,
                    `npm i -g @trustwallet/cli`). Auth via TWAK_ACCESS_ID /
                    TWAK_HMAC_SECRET env; wallet via `twak setup` + TWAK_WALLET_PASSWORD.
                    Guarded behind ENABLE_LIVE_TRADING; parses the CLI's `--json` output.

Verb surface pinned against `twak --help` + live `--json` calls:
  price <token> --chain bsc            -> {"priceUsd": ...}
  swap <amt> <from> <to> --chain bsc   -> {"output":"<amt> SYM","minReceived":...,"provider":...}
    (--quote-only to quote; --password to execute)
  balance --chain bsc --token <addr>   (or --coin 714 for native BNB)
  compete register|status              (competition, BSC)
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

QUOTE = "USDT"

# Substrings that mark a TRANSIENT twak/CLI failure worth retrying (vs a permanent
# one like a bad password or unknown token, which must fail fast).
_TRANSIENT_ERR = (
    "timeout",
    "timed out",
    "temporarily",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "econn",
    "network",
    "unavailable",
    # twak's balance/router RPC hiccup ("Could not fetch balance. Please try again
    # later.") — an explicit retry hint, transient under load; retry instead of aborting
    # the whole tick (the NAV/weights read is BEFORE any swap, so this never risks a fill).
    "try again",
    "could not fetch",
)

# The READ path (balance / price) runs BEFORE any swap, so it is idempotent and safe to retry
# generously — a transient twak RPC hiccup ("Could not fetch balance") must NOT abort the whole
# tick (a skipped rebalance = fewer trades = a contest-floor risk). Swaps keep the conservative
# default (2) since a retry there could risk a double-fill. 4 retries → 5 attempts, ~15s of
# exponential backoff (1+2+4+8s) — comfortably absorbs the brief hiccups seen under load.
_READ_RETRIES = 4


@dataclass
class SwapResult:
    from_token: str
    to_token: str
    amount_from: float  # units of from_token sent
    amount_to: float  # units of to_token received (after fee+slippage)
    price: float  # effective token/USDT price of the swap
    fee_paid: float  # in USDT terms
    tx: str  # tx hash (or sim id)
    ok: bool = True
    error: str | None = None


class SimTwakClient:
    """Paper TWAK: maintains an in-memory balance ledger and fills swaps at the
    supplied price minus fee+slippage. Deterministic — safe for tests/dry-runs."""

    def __init__(
        self,
        price_fn: Callable[[str], float],
        *,
        start_usdt: float = 1000.0,
        fee_per_side: float = 0.0005,
        slippage_per_side: float = 0.0010,
    ) -> None:
        self._price_fn = price_fn
        self.fee = fee_per_side
        self.slip = slippage_per_side
        self._bal: dict[str, float] = {QUOTE: float(start_usdt)}
        self._n = 0

    def price(self, token: str) -> float:
        return float(self._price_fn(token)) if token != QUOTE else 1.0

    def balance(self, token: str) -> float:
        return float(self._bal.get(token, 0.0))

    def balances(self) -> dict[str, float]:
        return {k: v for k, v in self._bal.items() if abs(v) > 1e-12}

    def swap(
        self, from_token: str, to_token: str, amount_from: float, *, execute: bool = True
    ) -> SwapResult:
        # `execute` is accepted for interface parity with CliTwakClient (quote-only vs
        # execute); the paper ledger always "fills", so it is a no-op here.
        self._n += 1
        if amount_from <= 0 or from_token == to_token:
            return SwapResult(
                from_token,
                to_token,
                0,
                0,
                self.price(to_token),
                0,
                tx=f"sim-noop-{self._n}",
                ok=False,
                error="bad swap args",
            )
        have = self.balance(from_token)
        amount_from = min(amount_from, have)
        if amount_from <= 0:
            return SwapResult(
                from_token,
                to_token,
                0,
                0,
                self.price(to_token),
                0,
                tx=f"sim-insuff-{self._n}",
                ok=False,
                error="insufficient balance",
            )
        # Convert from->USDT->to, charging one side of cost on each leg present.
        px_from = self.price(from_token)
        px_to = self.price(to_token)
        usdt_value = amount_from * px_from
        cost_frac = (self.fee + self.slip) * (1 if from_token != QUOTE else 0) + (
            self.fee + self.slip
        ) * (1 if to_token != QUOTE else 0)
        fee_paid = usdt_value * cost_frac
        net_usdt = usdt_value - fee_paid
        amount_to = net_usdt / px_to
        self._bal[from_token] = have - amount_from
        self._bal[to_token] = self.balance(to_token) + amount_to
        eff_price = px_to
        return SwapResult(
            from_token, to_token, amount_from, amount_to, eff_price, fee_paid, tx=f"sim-{self._n}"
        )


# Binance-Peg BEP-20 contract addresses for the contest tokens (BSC). `twak swap`
# resolves symbols itself, but `twak balance` wants an ERC-20 contract (or --coin
# for native BNB). Verify against `twak`'s registry on the first live wallet call.
BSC_TOKENS = {
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    "LINK": "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
    "UNI": "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "AVAX": "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
    "DOT": "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "DOGE": "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
}


class CliTwakClient:
    """Live TWAK via the real `twak` CLI (npm @trustwallet/cli). Each method shells
    out and parses JSON. Auth is by env (TWAK_ACCESS_ID / TWAK_HMAC_SECRET); the
    agent wallet is unlocked by TWAK_WALLET_PASSWORD for execution. No perps / no
    arbitrary contract calls — spot swaps only.

    Pinned against `twak --help` + live `--json` (price, swap --quote-only). Swap
    EXECUTE and balance shapes are confirmed on the first wallet-backed call.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        chain: str | None = None,
        access_id: str | None = None,
        hmac_secret: str | None = None,
        wallet_password: str | None = None,
        address: str | None = None,
        price_fn: Callable[[str], float] | None = None,
    ) -> None:
        from ictbot.settings import settings  # lazy — avoids import cycle

        # Resolve from settings (TWAK_BINARY) when not passed — lets a cron/daemon point
        # at the absolute nvm path without threading it through make_client/build_broker.
        self.binary = binary or settings.twak_binary
        self.chain = chain or settings.twak_chain
        # twak balance requires an explicit --address (the agent's trading wallet).
        self._address = address if address is not None else settings.agent_trading_address
        self._access_id = settings.twak_access_id if access_id is None else access_id
        self._hmac = settings.twak_hmac_secret if hmac_secret is None else hmac_secret
        # Wallet password unlocks signing; fall back to AGENT_WALLET_PASSWORD so the
        # subprocess never hangs on an interactive OS-keychain prompt.
        self._wallet_pw = (
            wallet_password
            if wallet_password is not None
            else (settings.twak_wallet_password or settings.agent_wallet_password)
        )
        self._price_fn = price_fn
        # Gasless execution via MegaFuel, with TWAK still the sole signer. Off by
        # default: only append the sponsored flag the CLI actually supports (verify
        # `twak swap --help`). A wrong flag would error the swap, so this stays inert
        # until an operator deliberately enables it.
        self._gasless = bool(settings.twak_gasless)
        self._gasless_flag = settings.twak_gasless_flag
        # Explicit slippage tolerance on a live execute. Default mirrors the CLI's
        # implicit "1" (no behavior change); set TWAK_SLIPPAGE_FLAG="" to suppress.
        self._slippage_pct = settings.twak_slippage_pct
        self._slippage_flag = settings.twak_slippage_flag
        # Balance READS: route through the keyless Multicall3 path (ictbot.api.onchain) instead of
        # the `twak balance` CLI, whose hosted RPC backend is flaky (NETWORK_ERROR) and makes a tick
        # wedge for hours while it retries through the outage. twak still signs/executes SWAPS — only
        # the read SOURCE changes. A short TTL coalesces the many per-token reads in one rebalance
        # pass into a single batched RPC; an execute swap busts it so the next read is fresh. Set
        # ALLOC_BALANCE_VIA_MULTICALL3=false to revert to the legacy twak-CLI read path.
        self._balance_via_mc3 = bool(settings.alloc_balance_via_multicall3)
        self._mc3_cache: tuple[float, dict[str, float]] | None = None
        self._mc3_ttl = 2.0

    def _env(self) -> dict:
        """Subprocess env with the canonical twak credential names injected.

        Also prepends the binary's own directory to PATH: twak ships with a
        `#!/usr/bin/env node` shebang, so when `twak_binary` is an absolute path (e.g. an
        nvm install) the co-located `node` must be discoverable even under a cron's
        minimal PATH. The bin/ dir holds both `twak` and `node`, so one prepend fixes it.
        """
        e = dict(os.environ)
        if self._access_id:
            e["TWAK_ACCESS_ID"] = self._access_id
        if self._hmac:
            e["TWAK_HMAC_SECRET"] = self._hmac
        if self._wallet_pw:
            e["TWAK_WALLET_PASSWORD"] = self._wallet_pw
        bin_dir = os.path.dirname(self.binary)
        if bin_dir and os.path.isabs(self.binary):
            e["PATH"] = bin_dir + os.pathsep + e.get("PATH", "")
        return e

    def _run(self, *args: str, retries: int = 2, backoff: float = 1.0) -> dict:
        """Shell out to twak, parse --json. Retries TRANSIENT failures (timeout /
        5xx / connection) with exponential backoff; raises immediately on a
        PERMANENT failure (bad password, unknown token) so it fails fast."""
        last = ""
        for attempt in range(retries + 1):
            try:
                out = subprocess.run(
                    [self.binary, *args],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env=self._env(),
                )
            except subprocess.TimeoutExpired as e:
                last = "timeout after 180s"
                if attempt < retries:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise RuntimeError(f"twak {args[0]} {last}") from e
            data = json.loads(out.stdout or "{}") if out.stdout.strip() else {}
            if out.returncode != 0 or (isinstance(data, dict) and data.get("error")):
                msg = (data.get("error") if isinstance(data, dict) else None) or out.stderr.strip()
                last = msg or "unknown error"
                if attempt < retries and any(s in last.lower() for s in _TRANSIENT_ERR):
                    time.sleep(backoff * (2**attempt))
                    continue
                raise RuntimeError(f"twak {args[0]} failed: {msg}")
            return data
        raise RuntimeError(f"twak {args[0]} failed after {retries} retries: {last}")

    @staticmethod
    def _amount(s) -> float:
        """Parse twak's '<amount> <SYMBOL>' strings (e.g. '0.1667 BNB') -> float.
        Tolerates a bare number too. Non-finite (NaN/Inf) -> 0.0: a malformed CLI value must
        never pass the ok-gate (`inf > 0` is True) or poison NAV/sizing — treat it as no fill."""
        try:
            v = float(str(s).split()[0])
        except (ValueError, IndexError, AttributeError):
            return 0.0
        return v if math.isfinite(v) else 0.0

    @staticmethod
    def _swap_sym(token: str) -> str:
        """Resolve a token to a form `twak swap` accepts: BNB (native) and USDT are
        recognised symbols; every other contest token needs its BEP-20 contract
        address (twak: 'Unknown token: CAKE — use a contract address')."""
        if token in ("BNB", "USDT"):
            return token
        return BSC_TOKENS.get(token, token)

    @staticmethod
    def _first(data: dict, keys: tuple[str, ...], default=None):
        """First present (non-None) value among `keys` — robustness to twak's
        execute-vs-quote field naming, which we can only fully pin on a live swap."""
        for k in keys:
            if data.get(k) is not None:
                return data[k]
        return default

    def price(self, token: str) -> float:
        if token == QUOTE:
            return 1.0
        if self._price_fn is not None:
            return float(self._price_fn(token))
        return float(self._run("price", token, "--chain", self.chain, "--json",
                               retries=_READ_RETRIES)["priceUsd"])

    def _mc3_balances(self) -> dict[str, float]:
        """Fresh-ish on-chain balances via the keyless Multicall3 reader, coalesced under a short TTL
        so the many per-token reads in ONE rebalance pass cost a single batched RPC. An execute swap
        busts the cache (see `swap`) so the post-swap buy-loop/reconcile reads see fresh balances.
        Raises on a hard RPC failure (fail-fast) — the caller skips the tick instead of wedging."""
        now = time.time()
        if self._mc3_cache is not None and now - self._mc3_cache[0] < self._mc3_ttl:
            return self._mc3_cache[1]
        from ictbot.api.onchain import read_onchain_balances  # lazy — avoids import cycle

        bals = read_onchain_balances(self._address)
        self._mc3_cache = (now, bals)
        return bals

    def balance(self, token: str) -> float:
        if self._balance_via_mc3:
            return float(self._mc3_balances().get(token, 0.0))
        args = ["balance", "--chain", self.chain, "--json"]
        if self._address:
            args += ["--address", self._address]
        if token in BSC_TOKENS:  # ERC-20 -> contract address
            args += ["--token", BSC_TOKENS[token]]
        # else (BNB / native): no --token/--coin -> the chain's native balance
        data = self._run(*args, retries=_READ_RETRIES)  # pre-swap read -> generous retry budget
        # twak returns the balance under "available"/"total" (string); tolerate the
        # other names too. (Confirmed against a live `twak balance --json`.)
        return self._amount(
            self._first(data, ("available", "total", "amount", "balance", "value"), 0.0)
        )

    def balances(self) -> dict[str, float]:
        """Live on-chain balances of USDT + BNB + the contest tokens (non-zero only).
        Mirrors SimTwakClient.balances() so run_allocator can snapshot the portfolio."""
        if self._balance_via_mc3:
            return {k: v for k, v in self._mc3_balances().items() if v > 0}
        out: dict[str, float] = {}
        for t in ("USDT", "BNB", *[k for k in BSC_TOKENS if k != "USDT"]):
            try:
                b = self.balance(t)
            except Exception:
                b = 0.0
            if b > 0:
                out[t] = b
        return out

    def swap(
        self, from_token: str, to_token: str, amount_from: float, *, execute: bool = True
    ) -> SwapResult:
        args = [
            "swap",
            f"{amount_from:.10f}",
            self._swap_sym(from_token),
            self._swap_sym(to_token),
            "--chain",
            self.chain,
            "--json",
        ]
        if execute:
            if self._wallet_pw:
                args += ["--password", self._wallet_pw]
            # Explicit slippage tolerance — only on an EXECUTE (a quote has no fill).
            # Appended only when the flag string is non-empty so it's trivially
            # disableable; default reproduces the CLI's implicit 1% (no behavior change).
            if self._slippage_flag:
                args += [self._slippage_flag, str(self._slippage_pct)]
            # Sponsored/gasless execution via MegaFuel (TWAK still signs). Only on an
            # EXECUTE — a quote pays nothing. Inert unless twak_gasless is enabled.
            if self._gasless and self._gasless_flag:
                args += [self._gasless_flag]
        else:
            args += ["--quote-only"]
        if execute:
            # A live swap changes on-chain balances -> drop the Multicall3 read cache so the next
            # balance read (the buy-loop USDT after sells, or post-rebalance reconcile) is fresh.
            self._mc3_cache = None
        # A failed live swap must NOT crash the rebalance — return an ok=False result
        # the broker/runtime can journal and carry on (the book stays consistent).
        try:
            data = self._run(*args)
        except Exception as e:  # noqa: BLE001 — surface as a failed swap, not a crash
            return SwapResult(
                from_token=from_token,
                to_token=to_token,
                amount_from=amount_from,
                amount_to=0.0,
                price=0.0,
                fee_paid=0.0,
                tx="",
                ok=False,
                error=f"twak swap {from_token}->{to_token} failed: {e}",
            )
        # Field names differ between quote and execute responses (and across twak
        # versions) — accept the known variants for amount-out / tx hash / fee.
        out_amt = self._amount(self._first(data, ("output", "amountOut", "toAmount", "received")))
        tx = self._first(data, ("txHash", "transactionHash", "hash", "tx"), "")
        fee = self._first(data, ("feeUsd", "feeUSD", "fee", "gasUsd"), 0.0)
        try:
            px = self.price(to_token) if to_token != QUOTE else 1.0
        except Exception:  # noqa: BLE001 — price read must not sink the swap result
            px = 0.0
        try:
            fee = float(fee)
        except (TypeError, ValueError):
            fee = 0.0
        if not math.isfinite(fee):  # a NaN/Inf fee would corrupt RebalanceReport.fees_usd
            fee = 0.0
        # A live EXECUTE is only "ok" with BOTH an amount-out AND a tx hash — a tx
        # with zero parsed amount is a silent-divergence trap (journal says filled,
        # on-chain balance unchanged). A quote needs only the amount.
        ok = bool(out_amt > 0 and tx) if execute else bool(out_amt > 0)
        return SwapResult(
            from_token=from_token,
            to_token=to_token,
            amount_from=amount_from,
            amount_to=out_amt,
            price=px,
            fee_paid=fee,
            tx=str(tx or self._first(data, ("provider",), "")),
            ok=ok,
            error=(data.get("error") or "swap response missing amount/txHash") if not ok else None,
        )


def make_client(mode: str, price_fn: Callable[[str], float], **kw):
    """Factory: 'sim' (default, safe) or 'live'/'dryrun' (CliTwakClient over the real CLI).

    'dryrun' uses the SAME live CLI client as 'live' (real on-chain balances + real router
    quotes); the broker runs it quote-only (execute=False) so nothing is signed or spent."""
    if mode in ("live", "dryrun"):
        return CliTwakClient(
            price_fn=price_fn,
            **{
                k: v
                for k, v in kw.items()
                if k in ("binary", "chain", "access_id", "hmac_secret", "wallet_password")
            },
        )
    return SimTwakClient(
        price_fn,
        **{k: v for k, v in kw.items() if k in ("start_usdt", "fee_per_side", "slippage_per_side")},
    )
