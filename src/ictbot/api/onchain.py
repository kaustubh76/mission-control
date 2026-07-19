"""
Live on-chain wallet read for the dashboard's "Real Funds" card.

This is the COUNTERPART to the SIM journal NAV: the journal/PnL cards show a paper
$1,000 book (`DASHBOARD_JOURNAL=sim`), while THIS reads the agent's *actual* BSC
holdings on-chain so judges see real money next to the paper strategy. The two are
deliberately separate ledgers and must never be conflated.

Design (keyless, cloud-safe):
  - Balances: ONE Multicall3 (`aggregate3`) round-trip on a PUBLIC BSC RPC — native
    BNB (via Multicall3.getEthBalance) + ERC-20 balanceOf for USDT/USDC + the contest
    tokens. allowFailure=true, so a single dead token never fails the batch.
  - Pricing: stables = $1; everything else is **CMC-first** (pillar 1 —
    `cmc.cmc_price`, only attempted when a CMC key is present) with an on-chain
    **Chainlink** USD feed as the keyless fallback. Each asset carries which source
    priced it, so the card can label it honestly.
  - No private key, no secret: only PUBLIC addresses + a public RPC. Safe to run on
    the zero-secret Render deploy.

Everything here is best-effort and NEVER raises: any failure degrades to `ok: false`
(or a cached value) so the 4s snapshot poll never blocks on a cold endpoint.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ictbot.settings import settings

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Chain constants (all mainnet BSC unless noted)
# --------------------------------------------------------------------------- #
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"  # same address on every chain
# Built-in PUBLIC RPCs reachable from a datacenter IP (Render) — NOT api.binance.com.
_PUBLIC_RPCS = [
    "https://bsc-rpc.publicnode.com",
    "https://binance.llamarpc.com",
    "https://bsc-dataseed1.defibit.io",
]
_BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base (6dp) — x402 budget

# Contest tokens + the two stables. (address, decimals, cmc_symbol). Native BNB is read
# separately via Multicall3.getEthBalance. WBNB is priced as BNB.
_TOKENS: dict[str, tuple[str, int, str]] = {
    "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18, "USDT"),
    "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18, "USDC"),
    "WBNB": ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18, "BNB"),
    "ETH": ("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18, "ETH"),
    "CAKE": ("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 18, "CAKE"),
    "LINK": ("0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD", 18, "LINK"),
    "UNI": ("0xBf5140A22578168FD562DCcF235E5D43A02ce9B1", 18, "UNI"),
    "AVAX": ("0x1CE0c2827e2eF14D5C4f29a091d735A204794041", 18, "AVAX"),
    "DOT": ("0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402", 18, "DOT"),
    "DOGE": ("0xbA2aE424d960c26247Dd6c32edC70B295c744C43", 8, "DOGE"),
}
_STABLES = {"USDT", "USDC"}
# Chainlink USD price feeds on BSC (all 8 decimals — verified on-chain). Keyed by the
# CMC symbol so WBNB/BNB share the BNB feed. These are the keyless pricing fallback.
_FEEDS: dict[str, str] = {
    "BNB": "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE",
    "ETH": "0x9ef1B8c0E4F7dc8bF5719Ea496883DC6401d5b2e",
    "CAKE": "0xB6064eD41d4f67e353768aA239cA86f4F73665a1",
    "LINK": "0xca236E327F629f9Fc2c30A4E95775EbF0B89fac8",
    "UNI": "0xb57f259E7C24e56a1dA00F66b55A5640d9f9E7e4",
    "AVAX": "0x5974855ce31EE8E1fff2e76591CbF83D7110F151",
    "DOT": "0xC333eb0086309a16aa7c8308DfD32c8BBA0a2592",
    "DOGE": "0x3AB0A0d137D4F946fBB19eecc6e92E64660231C8",
}
_GAS_LOW_BNB = 0.005  # below this, flag the trade-gas buffer as thin
_TTL_S = 45.0  # cache the on-chain read; the poll is every 4s
_cache: dict = {"value": None, "ts": 0.0}

_MC3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]


def _rpc_candidates() -> list[str]:
    """Preferred RPC order: explicit override -> keyed local RPC -> public fallbacks."""
    out: list[str] = []
    if settings.onchain_bsc_rpc_url:
        out.append(settings.onchain_bsc_rpc_url)
    if settings.bsc_rpc_https_url:  # keyed NodeReal (present locally)
        out.append(settings.bsc_rpc_https_url)
    out += _PUBLIC_RPCS
    return out


def _connect():
    """First RPC that answers a block number, or None."""
    from web3 import Web3

    for rpc in _rpc_candidates():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 12}))
            if w3.is_connected() and w3.eth.block_number > 0:
                return w3
        except Exception:
            continue
    return None


def _cmc_price(symbol: str) -> float | None:
    """CMC quote, but ONLY when a key is configured (skips a doomed HTTP on the
    keyless cloud deploy so the Chainlink fallback isn't delayed)."""
    if not settings.cmc_api_key:
        return None
    try:
        from ictbot.data.cmc import cmc_price

        return cmc_price(symbol)
    except Exception:
        return None


def _explorer(address: str) -> str:
    sub = "testnet." if settings.agent_network == "bsc-testnet" else ""
    return f"https://{sub}bscscan.com/address/{address}"


def _empty(note: str) -> dict:
    return {
        "ok": False,
        "address": settings.agent_trading_address or None,
        "assets": [],
        "total_usd": None,
        "note": note,
        "served_at": datetime.now(timezone.utc).isoformat(),
    }


def _base_usdc(address: str | None) -> float | None:
    """Identity wallet's Base USDC = the x402 budget. Reuses the x402 reader."""
    try:
        from ictbot.data.x402_cmc import base_usdc_balance

        return base_usdc_balance(address or None)
    except Exception:
        return None


def wallet_card() -> dict:
    """Live on-chain holdings of the TRADING wallet, priced CMC-first / Chainlink-fallback.
    TTL-cached, never raises. Shape mirrors schemas.WalletOut."""
    if not settings.onchain_reads_enabled:
        return _empty("on-chain reads disabled")
    addr_raw = settings.agent_trading_address
    if not addr_raw:
        return _empty("no AGENT_TRADING_ADDRESS configured")

    now = time.time()
    cached = _cache["value"]
    if cached is not None and now - _cache["ts"] < _TTL_S:
        return cached

    try:
        from eth_abi import decode as abi_decode
        from eth_abi import encode as abi_encode
        from web3 import Web3
    except Exception:
        return cached or _empty("web3 not installed")

    w3 = _connect()
    if w3 is None:
        # Degrade to the last good read if we have one, else a clean "unavailable".
        return cached or _empty("no reachable BSC RPC")

    addr = Web3.to_checksum_address(addr_raw)
    sel_bal = Web3.keccak(text="balanceOf(address)")[:4]
    sel_eth = Web3.keccak(text="getEthBalance(address)")[:4]
    sel_ans = Web3.keccak(text="latestAnswer()")[:4]
    arg = abi_encode(["address"], [addr])

    feed_syms = list(_FEEDS)
    tok_syms = list(_TOKENS)
    calls = [(Web3.to_checksum_address(MULTICALL3), True, sel_eth + arg)]  # [0] native BNB
    for s in feed_syms:  # chainlink prices
        calls.append((Web3.to_checksum_address(_FEEDS[s]), True, sel_ans))
    for s in tok_syms:  # token balances
        calls.append((Web3.to_checksum_address(_TOKENS[s][0]), True, sel_bal + arg))

    try:
        mc = w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=_MC3_ABI)
        res = mc.functions.aggregate3(calls).call()
        block = w3.eth.block_number
    except Exception as e:  # noqa: BLE001
        log.debug("wallet_card multicall failed: %s", type(e).__name__)
        return cached or _empty("on-chain read failed")

    def _u256(rd: bytes) -> int | None:
        try:
            return abi_decode(["uint256"], rd)[0]
        except Exception:
            return None

    def _i256(rd: bytes) -> int | None:
        try:
            return abi_decode(["int256"], rd)[0]
        except Exception:
            return None

    # Chainlink prices (8dp) keyed by CMC symbol — the keyless fallback set.
    chainlink: dict[str, float] = {}
    for i, s in enumerate(feed_syms, start=1):
        ok, rd = res[i]
        v = _i256(rd) if ok else None
        if v is not None and v > 0:
            chainlink[s] = v / 1e8

    def _price(cmc_symbol: str) -> tuple[float | None, str | None]:
        if cmc_symbol in _STABLES:
            return 1.0, "stable"
        p = _cmc_price(cmc_symbol)
        if p is not None:
            return p, "cmc"
        if cmc_symbol in chainlink:
            return chainlink[cmc_symbol], "chainlink"
        return None, None

    assets: list[dict] = []
    total = 0.0
    used_cmc = used_chain = False

    # Native BNB — always shown (it's the trade-gas buffer), even when tiny.
    ok0, rd0 = res[0]
    bnb_raw = _u256(rd0) if ok0 else None
    bnb = (bnb_raw or 0) / 1e18
    bnb_px, bnb_src = _price("BNB")
    bnb_usd = bnb * bnb_px if bnb_px is not None else None
    if bnb_usd is not None:
        total += bnb_usd
    used_cmc |= bnb_src == "cmc"
    used_chain |= bnb_src == "chainlink"
    assets.append(
        {
            "symbol": "BNB",
            "amount": bnb,
            "usd": bnb_usd,
            "price": bnb_px,
            "source": bnb_src,
            "is_gas": True,
        }
    )

    # ERC-20s — include only non-zero balances.
    base = 1 + len(feed_syms)
    held: list[dict] = []
    for j, s in enumerate(tok_syms):
        ok, rd = res[base + j]
        raw = _u256(rd) if ok else None
        if not raw:
            continue
        _, dec, cmc_symbol = _TOKENS[s]
        amt = raw / 10**dec
        if amt <= 0:
            continue
        px, src = _price(cmc_symbol)
        usd = amt * px if px is not None else None
        if usd is not None:
            total += usd
        used_cmc |= src == "cmc"
        used_chain |= src == "chainlink"
        # Show WBNB as WBNB (distinct from native), but it priced via the BNB feed.
        held.append(
            {"symbol": s, "amount": amt, "usd": usd, "price": px, "source": src, "is_gas": False}
        )

    held.sort(key=lambda a: a["usd"] or 0, reverse=True)
    assets.extend(held)

    priced_source = "cmc" if used_cmc else ("chainlink" if used_chain else "stable")
    out = {
        "ok": True,
        "address": addr,
        "explorer_url": _explorer(addr),
        "block": int(block),
        "network": settings.agent_network,
        "assets": assets,
        "total_usd": round(total, 2),
        "priced_source": priced_source,
        "gas_bnb": bnb,
        "gas_low": bnb < _GAS_LOW_BNB,
        "x402_budget_usdc": _base_usdc(settings.agent_identity_address or None),
        "served_at": datetime.now(timezone.utc).isoformat(),
        "note": None,
    }
    _cache.update(value=out, ts=now)
    return out


def read_onchain_balances(address: str | None = None) -> dict[str, float]:
    """Raw on-chain balances of `address` (default: the trading wallet) via ONE keyless Multicall3
    round-trip — native BNB (getEthBalance) + ERC-20 balanceOf for each contest token, normalized by
    decimals. Returns ``{symbol: amount}`` over exactly the trading universe (native -> ``"BNB"``,
    plus USDT + the contest ERC-20s; WBNB and USDC are skipped — the book holds NATIVE BNB and the
    USDT quote).

    This is the trading-path counterpart to :func:`wallet_card`: it does **no pricing** (pure
    balanceOf/getEthBalance — DEX/RPC only, so it is CEX-free and safe under ``CMC_ONLY``) and keeps
    **no cache** (a rebalance needs fresh post-swap balances). Crucially, unlike ``wallet_card`` it
    **RAISES** ``RuntimeError`` on a hard failure (no reachable RPC / the multicall throws) so the
    caller fails fast and skips the tick — rather than the live client wedging for hours retrying a
    flaky ``twak balance`` RPC, or silently trading on an empty/stale snapshot.
    """
    addr_raw = address if address is not None else settings.agent_trading_address
    if not addr_raw:
        raise RuntimeError("read_onchain_balances: no address (pass one or set AGENT_TRADING_ADDRESS)")
    try:
        from eth_abi import decode as abi_decode
        from eth_abi import encode as abi_encode
        from web3 import Web3
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"read_onchain_balances: web3 not installed: {e}") from e

    w3 = _connect()
    if w3 is None:
        raise RuntimeError("read_onchain_balances: no reachable BSC RPC")

    addr = Web3.to_checksum_address(addr_raw)
    sel_bal = Web3.keccak(text="balanceOf(address)")[:4]
    sel_eth = Web3.keccak(text="getEthBalance(address)")[:4]
    arg = abi_encode(["address"], [addr])

    # The trading universe only: native BNB + USDT + the contest ERC-20s. WBNB (the book holds
    # native BNB) and USDC are excluded so this mirrors the legacy `twak balance` symbol set exactly.
    tok_syms = [s for s in _TOKENS if s not in ("WBNB", "USDC")]
    calls = [(Web3.to_checksum_address(MULTICALL3), True, sel_eth + arg)]  # [0] native BNB
    for s in tok_syms:
        calls.append((Web3.to_checksum_address(_TOKENS[s][0]), True, sel_bal + arg))

    try:
        mc = w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=_MC3_ABI)
        res = mc.functions.aggregate3(calls).call()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"read_onchain_balances: multicall failed: {type(e).__name__}") from e

    def _u256(rd: bytes) -> int | None:
        try:
            return abi_decode(["uint256"], rd)[0]
        except Exception:
            return None

    out: dict[str, float] = {}
    ok0, rd0 = res[0]
    out["BNB"] = ((_u256(rd0) or 0) / 1e18) if ok0 else 0.0
    for j, s in enumerate(tok_syms, start=1):
        ok, rd = res[j]
        raw = _u256(rd) if ok else None
        dec = _TOKENS[s][1]
        out[s] = (raw or 0) / 10**dec
    return out
