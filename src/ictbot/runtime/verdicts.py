"""
Persisted strategy validation verdicts — `data/reports/strategy_gates.json`.

Written by `scripts/validate_strategy.py --save-verdict` (kind `"survival"`, the
backtest DQ-safe + active gate) and `scripts/forward_promote.py` (kind `"forward"`,
the Part 7 forward-promotion check); read by the API snapshot to badge each arm in the
dashboard strategy selector.

Structure: ``{strategy_name: {"survival": {...}, "forward": {...}}}``. Pure file I/O —
a missing/corrupt file degrades to ``{}`` (the dashboard then shows arms "untested").
"""

from __future__ import annotations

import json
import os

from ictbot.settings import DATA_DIR

VERDICTS_FILE = DATA_DIR / "reports" / "strategy_gates.json"


def load() -> dict:
    """The full verdict map, or {} on absent/corrupt."""
    try:
        return json.loads(VERDICTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record(strategy: str, kind: str, payload: dict) -> dict:
    """Merge ``payload`` under ``[strategy][kind]`` and atomically rewrite the file.

    ``kind`` is ``"survival"`` (backtest gate) or ``"forward"`` (forward-promotion).
    Returns the full updated map.
    """
    data = load()
    entry = dict(data.get(strategy) or {})
    entry[kind] = payload
    data[strategy] = entry
    VERDICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VERDICTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, VERDICTS_FILE)
    return data
