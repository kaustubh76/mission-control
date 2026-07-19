"""
CoinMarketCap AI Agent Hub — x402 paid-data path (pillar 1, native x402).

This is the REAL "AI Agent Hub" tier the pro-api path in `cmc.py` does not use.
Instead of an `X-CMC_PRO_API_KEY`, x402 pays per request ($0.01 USDC on Base):

    GET https://pro-api.coinmarketcap.com/x402/v1/dex/search?q=bnb
      -> 402 Payment Required + an x402 challenge (the `accepts` payment options)
      -> sign a USDC EIP-3009 `TransferWithAuthorization` with the BNB-SDK X402Signer
      -> resend with the `PAYMENT-SIGNATURE` header (base64 payload that echoes the chosen
         `accepted` option + the `resource`) -> 200 + the data, payment settled on-chain.

The challenge shape below is CONFIRMED against the live mainnet endpoint (x402Version 2,
CAIP-2 `network` like `eip155:8453`, an `accepts[]` array; the Base USDC entry uses
`assetTransferMethod: "eip3009"` — the one X402Signer signs). Only `dex/search` is
actually payable today (other /x402 paths return an empty `accepts`), so that is the
endpoint we use.

Signing is done by `bnbagent.X402Signer`, which enforces recipient-match, a per-call
value cap, and a session budget on top of the wallet's own SigningPolicy — so a
malicious 402 cannot redirect the payment or inflate the amount. Everything here is
REAL (no mocks): real HTTP, real EIP-712 signing. It is OFF by default (`X402_ENABLED`)
and only settles when the Base USDC payment wallet is funded; on ANY failure the
caller falls back to the keyed pro-api functions in `cmc.py`. Each settled call
appends a receipt to `data/x402/receipts.json` (the TWAK-special artifact).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from ictbot.settings import DATA_DIR, settings

log = logging.getLogger(__name__)

X402_BASE = "https://pro-api.coinmarketcap.com"
DEX_SEARCH_PATH = "/x402/v1/dex/search"  # the confirmed x402-payable CMC endpoint
QUOTES_LATEST_PATH = "/x402/v3/cryptocurrency/quotes/latest"  # also payable (probe-confirmed)
RECEIPTS = DATA_DIR / "x402" / "receipts.json"
# We pay on Base with USDC via EIP-3009 — match the accept on this CAIP-2 network.
PREFERRED_NETWORK = "eip155:8453"  # Base mainnet
BASE_CHAIN_ID = 8453
PREFERRED_METHOD = "eip3009"  # the transfer method X402Signer signs
# The SDK's SigningPolicy caps the EIP-3009 validity window at 600s and future-dating
# at 900s. Backdate validAfter for clock skew and bound validBefore so signing passes.
_VALID_AFTER_SKEW = 60
_MAX_VALID_BEFORE = 480
_RAW_LOGGED = False  # log the first live challenge once


def available() -> bool:
    """True iff x402 is enabled, the SDK is importable, and a wallet password is set."""
    if not settings.x402_enabled:
        return False
    try:
        import bnbagent  # noqa: F401
    except Exception:
        return False
    return bool(settings.agent_wallet_password)


def _signing_policy(usdc: str):
    """strict_default() EXTENDED to allowlist the Base USDC domain we pay with.

    The SDK's strict default fails closed: it only allows the U-token domain, so it
    refuses to sign a Base-USDC EIP-3009 authorization until we add (chainId, USDC)
    explicitly. We keep every other guard (EIP-3009-only primary type, Permit denylist,
    validity window) — we only widen the domain allowlist to the one token we spend."""
    from bnbagent import SigningPolicy
    from web3 import Web3

    return SigningPolicy.strict_default().extend(
        domain_allowlist={(BASE_CHAIN_ID, Web3.to_checksum_address(usdc))},
    )


def _wallet():
    """The agent's payment wallet — the SAME keystore as the ERC-8004 identity wallet
    (so there is ONE agent address to fund), with the Base-USDC domain allowlisted."""
    from bnbagent import EVMWalletProvider

    return EVMWalletProvider(
        password=settings.agent_wallet_password,
        private_key=settings.agent_private_key or None,
        persist=True,
        signing_policy=_signing_policy(settings.x402_usdc_base_address),
    )


def _signer():
    """Build an X402Signer over the agent's payment wallet. Per-call + session caps
    come from settings (USDC, 6dp)."""
    from bnbagent import X402Signer

    usdc = settings.x402_usdc_base_address
    return X402Signer(
        _wallet(),
        max_value_per_call={usdc: settings.x402_max_value_per_call_units},
        session_budget={usdc: settings.x402_session_budget_units},
    )


def payment_address() -> str | None:
    """The exact address x402 pays FROM — fund THIS with USDC on Base (chain 8453).
    It's the bnbagent identity wallet (same address that mints/heartbeats); pays no BSC
    gas. NOTE: this is NOT the TWAK trading wallet.

    Uses the DISPLAY address (public AGENT_IDENTITY_ADDRESS when set) so a read-only
    dashboard can show it + read the Base USDC balance with NO key. The actual x402
    SIGNING path (_signer/_wallet) still derives the real wallet from the key locally."""
    from ictbot.agent.identity import display_address

    return display_address()


def base_usdc_balance(address: str | None = None) -> float | None:
    """USDC balance (Base, 6dp) of the x402 payment wallet — to confirm funding landed.
    Best-effort read via X402_BASE_RPC_URL; None on any failure (never raises)."""
    addr = address or payment_address()
    if not addr:
        return None
    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(settings.x402_base_rpc_url))
        usdc = Web3.to_checksum_address(settings.x402_usdc_base_address)
        erc20 = w3.eth.contract(
            address=usdc,
            abi=[
                {
                    "constant": True,
                    "inputs": [{"name": "a", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "type": "function",
                }
            ],
        )
        raw = erc20.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return raw / 1e6
    except Exception:
        return None


def _log_raw(challenge: dict) -> None:
    global _RAW_LOGGED
    if not _RAW_LOGGED:
        log.info("x402: live 402 challenge: %s", json.dumps(challenge)[:1500])
        _RAW_LOGGED = True


def _chain_id(network: str) -> int:
    """Parse a CAIP-2 network id (`eip155:8453`) to its numeric chainId."""
    try:
        return int(str(network).split(":")[-1])
    except (ValueError, AttributeError):
        return 8453


def pick_accept(challenge: dict) -> dict | None:
    """Choose the payment option to sign: prefer Base-USDC EIP-3009 (cheapest, and the
    scheme X402Signer supports), then any EIP-3009 accept, else None (nothing payable)."""
    accepts = challenge.get("accepts") or []
    if not isinstance(accepts, list) or not accepts:
        return None
    method = lambda a: str(a.get("extra", {}).get("assetTransferMethod", "")).lower()
    for a in accepts:
        if a.get("network") == PREFERRED_NETWORK and method(a) == PREFERRED_METHOD:
            return a
    for a in accepts:
        if method(a) == PREFERRED_METHOD:
            return a
    return None


def build_payment(challenge: dict, accept: dict, signer) -> tuple[dict, dict]:
    """Construct + sign the EIP-3009 TransferWithAuthorization for one x402 call.

    Returns (payment_payload, receipt_fields). Raises on any guard failure
    (X402AmountExceededError / X402BudgetExhaustedError / X402RecipientMismatchError /
    X402PolicyError) — the caller treats all of these as "fall back to pro-api"."""
    from web3 import Web3

    pay_to = accept["payTo"]
    asset = accept["asset"]
    value = int(accept.get("amount") or accept.get("maxAmountRequired"))
    extra = accept.get("extra") or {}
    chain_id = _chain_id(accept.get("network", PREFERRED_NETWORK))
    now = int(time.time())
    import os

    nonce = "0x" + os.urandom(32).hex()

    domain = {
        "name": extra.get("name", "USD Coin"),
        "version": str(extra.get("version", "2")),
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(asset),
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ]
    }
    # validAfter is backdated for clock skew and validBefore bounded so the window
    # (validBefore - validAfter) stays under the SigningPolicy's 600s cap.
    valid_before = now + min(int(accept.get("maxTimeoutSeconds", 60)), _MAX_VALID_BEFORE)
    message = {
        "from": Web3.to_checksum_address(signer.wallet_address),
        "to": Web3.to_checksum_address(pay_to),
        "value": value,
        "validAfter": now - _VALID_AFTER_SKEW,
        "validBefore": valid_before,
        "nonce": nonce,
    }
    signed = signer.sign_payment(
        domain=domain, types=types, message=message, expected_to=Web3.to_checksum_address(pay_to)
    )
    # The wallet returns the signature as HexBytes — normalize to a 0x-hex string so
    # the payment payload is JSON-serializable for the X-PAYMENT header.
    raw_sig = signed.get("signature") if isinstance(signed, dict) else signed
    sig = raw_sig if isinstance(raw_sig, str) else Web3.to_hex(raw_sig)
    authorization = {
        "from": message["from"],
        "to": message["to"],
        "value": str(value),
        "validAfter": str(message["validAfter"]),
        "validBefore": str(message["validBefore"]),
        "nonce": nonce,
    }
    payment = {
        "x402Version": int(challenge.get("x402Version", 2)),
        "scheme": accept.get("scheme", "exact"),
        "network": accept.get("network", PREFERRED_NETWORK),
        # CMC's x402 V2 facilitator requires the chosen accept option AND the challenge
        # resource echoed back in the PAYMENT-SIGNATURE payload (it answers "Missing
        # accepted" / "resource is null" otherwise) — confirmed live 2026-06-12.
        "accepted": accept,
        "resource": challenge.get("resource"),
        "payload": {"signature": sig, "authorization": authorization},
    }
    receipt = {
        "payTo": message["to"],
        "asset": asset,
        "value": value,
        "network": payment["network"],
    }
    return payment, receipt


def _payment_header(payment: dict) -> dict:
    """x402 resend header: base64(JSON) of the payment payload. CMC's gateway reads
    `PAYMENT-SIGNATURE` (its published header name), not the bare-spec `X-PAYMENT` —
    sending X-PAYMENT is silently ignored and re-challenged. Confirmed live 2026-06-12."""
    encoded = base64.b64encode(json.dumps(payment).encode()).decode()
    return {"PAYMENT-SIGNATURE": encoded, "Accept": "application/json"}


def _log_receipt(endpoint: str, fields: dict, status: str) -> None:
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if RECEIPTS.exists():
            try:
                rows = json.loads(RECEIPTS.read_text()) or []
            except Exception:
                rows = []
        rows.append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "endpoint": endpoint,
                "status": status,
                **fields,
            }
        )
        RECEIPTS.write_text(json.dumps(rows, indent=2))
    except Exception:
        pass


def fetch_challenge(path: str, params: dict | None = None, timeout: float = 20.0) -> dict | None:
    """REAL unpaid probe: GET the x402 endpoint and return the 402 challenge JSON
    (the `accepts` payment options), or None if it is already free / unreachable.
    No payment, no signing — safe to call without a funded wallet."""
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"{X402_BASE}{path}{qs}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout
        ) as r:
            return json.loads(r.read().decode())  # already free / cached — no payment needed
    except urllib.error.HTTPError as e:
        if e.code != 402:
            return None
        try:
            return json.loads(e.read().decode())
        except Exception:
            return None
    except Exception:
        return None


def fetch_x402(path: str, params: dict | None = None, timeout: float = 20.0) -> dict | None:
    """Run the full REAL 402 -> sign -> resend flow for one CMC x402 endpoint.

    Returns the decoded JSON body on success, or None on ANY failure (caller falls
    back to pro-api). Never raises."""
    if not available():
        # C2: surface the silent-off case — an operator who set X402_ENABLED=true can
        # otherwise believe pillar-1 is paying when it isn't. Name the missing piece.
        if settings.x402_enabled:
            try:
                import bnbagent  # noqa: F401

                missing = "AGENT_WALLET_PASSWORD" if not settings.agent_wallet_password else "?"
            except Exception:
                missing = 'the bnbagent SDK (pip install -e ".[bnb]")'
            log.warning(
                "x402 is ENABLED but unavailable (missing %s) — falling back to "
                "the free pro-api; no paid reads will settle.",
                missing,
            )
        return None
    challenge = fetch_challenge(path, params, timeout)
    if not challenge:
        return None
    _log_raw(challenge)
    accept = pick_accept(challenge)
    if accept is None:
        _log_receipt(path, {"error": "no_payable_accept"}, "failed")
        return None
    try:
        payment, receipt = build_payment(challenge, accept, _signer())
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req2 = urllib.request.Request(f"{X402_BASE}{path}{qs}", headers=_payment_header(payment))
        with urllib.request.urlopen(req2, timeout=timeout) as r2:
            body = json.loads(r2.read().decode())
        _log_receipt(path, receipt, "settled")
        return body
    except Exception as e:  # noqa: BLE001 — any failure => fall back, but record the miss
        _log_receipt(path, {"error": type(e).__name__}, "failed")
        log.debug("x402 %s failed (%s); falling back to pro-api", path, type(e).__name__)
        return None


def dex_search(query: str) -> dict | None:
    """DEX search. FREE_DATA (default): the free, keyless DexScreener search — CMC's paid x402 DEX
    endpoint is gone. Falls back to the CMC x402 path only if free data is off (legacy).

    Returns a `data`-shaped object (`{total, tks:[...]}`) or None."""
    if settings.free_data:
        from ictbot.data import dexscreener

        body = dexscreener.dex_search(query)
        pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
        if pairs:
            # Shape DexScreener pairs to the {total, tks:[...]} contract the caller expects.
            return {"total": len(pairs), "tks": pairs}
        return None
    body = fetch_x402(DEX_SEARCH_PATH, {"q": query})
    if not body:
        return None
    return body.get("data") if isinstance(body, dict) else None


def quotes_latest(cmc_id: str | int = 1) -> dict | None:
    """Pay-per-call CMC PRICE QUOTE via x402 (probe-confirmed $0.01 Base-USDC). A stronger
    'Best Use of CMC via x402' than DEX-search enrichment — real market quotes paid on-chain.
    Returns the `data` object on a settled call, else None. Off unless X402_ENABLED + funded."""
    body = fetch_x402(QUOTES_LATEST_PATH, {"id": str(cmc_id)})
    if not body:
        return None
    return body.get("data") if isinstance(body, dict) else None
