"""Persisted stability grades — `data/reports/strategy_stability.json`.

Written by `make stability` (scripts/strategy_stability.py) alongside the markdown report, and read
by the API snapshot to badge each arm robust/fragile/unstable in the dashboard strategy selector.

Display-only and separate from `strategy_gates.json` (which the campaign owns for survival/forward).
Shape: ``{arm: {"grade": "ROBUST"|"FRAGILE"|"UNSTABLE", "ts": "..."}}``. A missing/corrupt file
degrades to ``{}`` (the dashboard then shows no stability badge). `record` MERGES so a partial
`--arm` run updates only those arms without wiping the rest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ictbot.settings import DATA_DIR

STABILITY_FILE = DATA_DIR / "reports" / "strategy_stability.json"


def load(path: Path | None = None) -> dict:
    """The full grade map, or {} on absent/corrupt."""
    try:
        return json.loads((path or STABILITY_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def record(grades: dict, *, path: Path | None = None) -> dict:
    """Merge ``grades`` into the file and atomically rewrite it. Returns the full updated map."""
    target = path or STABILITY_FILE
    data = load(target)
    data.update(grades)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return data
