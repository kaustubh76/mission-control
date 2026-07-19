#!/usr/bin/env bash
# Full scanner smoke against Binance Futures testnet.
#
# Validates the live broker pipeline (factory → BinanceLiveBroker →
# place_order → SignalRouter.on_close) against the real testnet. No
# real money at risk — testnet only. Pair: BTC/USDT. Risk: 0.05%.
#
# Prereqs in .env:
#   BINANCE_API_KEY        — from https://testnet.binancefuture.com
#   BINANCE_API_SECRET
#   BINANCE_TESTNET=true   (set inline by this script too)
#   TELEGRAM_TOKEN         — for signal alerts
#   TELEGRAM_CHAT_ID
#
# Expected first log lines:
#   ICT AI BOT PRO MAX scanner started for 1 pairs.
#   router using broker=binance-live cap_gate=3 caps
#
# Stop with Ctrl-C. Roll back without code edits:
#   unset ENABLE_LIVE_TRADING, or `touch data/KILL_SWITCH_ENGAGED`.

set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

export EXCHANGE=binance
export BINANCE_TESTNET=true
export ENABLE_LIVE_TRADING=true
export PAIRS='["BTC/USDT:USDT"]'
export RISK_PCT=0.0005
export TG_HEARTBEAT_EVERY_N_CYCLES=0

echo "── Binance Futures testnet smoke: BTC/USDT, risk=0.05% ──"
echo "   testnet trade UI: https://testnet.binancefuture.com/en/futures/BTCUSDT"
echo "   Ctrl-C to stop."
echo

exec python -m ictbot.orchestrator.scanner
