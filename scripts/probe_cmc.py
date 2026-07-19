#!/usr/bin/env python3
"""
Phase-0 CMC capability probe.

Issues ONE real call per candidate endpoint through the hardened CmcClient and records
which are in-tier on the CURRENT plan + their measured credit cost, to
data/journal/cmc_capability.json. `cmc_intel.py` consults this map and short-circuits
unavailable (e.g. tier-gated) endpoints so the agent never burns credits looping on a
403/402. Run once after a plan change:

    PYTHONPATH=src python scripts/probe_cmc.py

Cost: ~10 credits total (one call per endpoint).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Pin the LOCAL ictbot (this venv otherwise resolves ictbot to a sibling repo).
sys.path.insert(0, str(ROOT / "src"))

from ictbot.data.cmc_client import CMC  # noqa: E402
from ictbot.settings import JOURNAL_DIR  # noqa: E402

# (path, params) — minimal valid params per endpoint to keep the probe cheap.
# Probe exactly the endpoints the agent actually CONSUMES, so the capability
# short-circuit can gate every consumed call (CMC-3). global-metrics/quotes/historical
# is the macro-history source used by the regime backtest; it was previously fetched but
# unprobed. listings/latest + trending/latest were probed but never consumed — dropped.
PROBES: list[tuple[str, dict]] = [
    ("/v2/cryptocurrency/quotes/latest", {"symbol": "BNB"}),              # baseline (known good)
    ("/v3/fear-and-greed/latest", {}),                                   # baseline (known good)
    ("/v1/global-metrics/quotes/latest", {}),                            # dominance + total mktcap
    ("/v1/global-metrics/quotes/historical", {"interval": "1d", "count": 5}),  # macro history (regime)
    ("/v3/fear-and-greed/historical", {"limit": 5}),                     # F&G momentum
    ("/v2/cryptocurrency/ohlcv/historical",
     {"symbol": "BNB", "interval": "daily", "count": 5}),               # 24mo daily candles
    ("/v1/cryptocurrency/trending/gainers-losers", {"limit": 5}),        # momentum confirmation
    ("/v1/cryptocurrency/categories", {"limit": 5}),                     # sector rotation
]


def main() -> int:
    if not CMC.telemetry()["key_set"]:
        print("No CMC_API_KEY resolved (set it in .env). Aborting probe.")
        return 1

    out: dict[str, dict] = {}
    for path, params in PROBES:
        body = CMC.get(path, params, force=True, cache_ttl=0, est_credits=1)
        tel = CMC.telemetry()
        ok = body is not None
        rec: dict = {"ok": ok, "http": tel["last_status"], "credits": tel["last_credit_count"]}
        if ok:
            data = body.get("data")
            rec["shape"] = type(data).__name__
            rec["n"] = len(data) if isinstance(data, (list, dict)) else None
        else:
            rec["reason"] = f"http {tel['last_status']}"
        out[path] = rec
        print(f"{'OK ' if ok else 'XX '} {path:46} http={rec['http']} "
              f"credits={rec['credits']}" + ("" if ok else f"  ({rec['reason']})"))

    dest = JOURNAL_DIR / "cmc_capability.json"
    dest.write_text(json.dumps(out, indent=2))
    avail = sum(1 for r in out.values() if r["ok"])
    print(f"\nwrote {dest}")
    print(f"available: {avail}/{len(out)} endpoints · spent ~{CMC.telemetry()['credits_today']} "
          f"credits today (budget {CMC.telemetry()['daily_budget']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
