#!/usr/bin/env bash
# Credential-only smoke test for Binance Futures testnet.
# Prints USDT futures balance on success, or the exchange's exact
# error on failure. Does NOT run the scanner; does NOT place orders.

set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

python - <<'PY'
import sys
from importlib import reload
from ictbot import settings as s
reload(s)

key = s.settings.binance_api_key
sec = s.settings.binance_api_secret
testnet = s.settings.binance_testnet

if not key or not sec:
    print("❌ BINANCE_API_KEY / BINANCE_API_SECRET are empty in .env")
    print("   Generate them at https://testnet.binancefuture.com → Profile → API Management")
    sys.exit(1)

print(f"key:    {key[:6]}…{key[-4:]}  (len={len(key)})")
print(f"secret: {sec[:6]}…{sec[-4:]}  (len={len(sec)})")
print(f"endpoint: {'testnet (testnet.binancefuture.com)' if testnet else 'mainnet'}")
print()

import ccxt
client = ccxt.binance({
    "enableRateLimit": True,
    "apiKey": key,
    "secret": sec,
    "options": {"defaultType": "future"},
})
if testnet:
    # Mirror BinanceLiveBroker._apply_testnet_routing exactly: override
    # fapi URLs + short-circuit any SAPI URL the high-level ccxt
    # methods would try to pre-flight.
    test_urls = client.urls.get("test") or {}
    for k in ("fapiPublic", "fapiPrivate",
              "fapiPublicV2", "fapiPrivateV2",
              "fapiPublicV3", "fapiPrivateV3"):
        if k in test_urls:
            client.urls["api"][k] = test_urls[k]
    _original_fetch = client.fetch
    def _patched_fetch(url, method="GET", headers=None, body=None):
        if "/sapi/" in url:
            return []
        return _original_fetch(url, method, headers, body)
    client.fetch = _patched_fetch
    print(f"   (using testnet endpoint: {client.urls['api']['fapiPrivate']})")

try:
    # ccxt's fetch_balance() works now that SAPI is short-circuited.
    bal = client.fetch_balance()
    usdt = bal.get("USDT") or {}
    avail = float(usdt.get("free") or 0.0)
    wallet = float(usdt.get("total") or 0.0)
    print(f"✅ AUTH OK — USDT available={avail} wallet={wallet}")
    if avail == 0:
        print("   ⚠️  USDT balance is zero on this testnet/demo account.")
        print("       Fund via the faucet at https://demo.binance.com (UI)")
        print("       or https://testnet.binancefuture.com (mirror) — both show your account.")
    else:
        print(f"   → ready to run scripts/smoke_binance.sh "
              f"(0.05% risk per trade → ~${avail * 0.0005:.2f} risk envelope)")
except ccxt.AuthenticationError as e:
    print(f"❌ AUTH FAILED — {e}")
    print()
    print("   Common causes:")
    print("   1. Key was generated for mainnet but BINANCE_TESTNET=true.")
    print("      → Use https://testnet.binancefuture.com (NOT www.binance.com)")
    print("   2. API key permissions don't include Futures.")
    print("      → Edit the key, tick 'Enable Futures' under permissions.")
    print("   3. IP restriction set but doesn't include your current IP.")
    sys.exit(2)
except Exception as e:
    print(f"❌ OTHER ERROR — {type(e).__name__}: {e}")
    sys.exit(3)
PY
