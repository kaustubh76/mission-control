"""
Natural-language strategy spec -> allocator parameters (the "rules you set" pillar).

The build's framing: "natural-language strategy in, on-chain execution out".
`config/strategy.md` is the agent's strategy in plain English; this module parses it
**deterministically** (a documented mini-grammar over key phrases/numbers) into the
AllocatorParams + deployment band the agent runs by — parsed ONCE at startup and frozen
for the run, so there is no fragile runtime LLM in the autonomous loop.

Anything the spec doesn't pin falls back to the validated committed defaults, so a
sloppy edit degrades gracefully rather than breaking the agent.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from ictbot.settings import PROJECT_ROOT
from ictbot.strategy.momentum_allocator import AllocatorParams

DEFAULT_SPEC = PROJECT_ROOT / "config" / "strategy.md"
DEFAULT_FLOOR, DEFAULT_CEILING = 0.40, 0.85


def parse_spec(text: str) -> tuple[AllocatorParams, float, float]:
    """Return (AllocatorParams, cap_floor, cap_ceiling) parsed from NL `text`."""
    p = AllocatorParams()  # committed defaults backstop everything
    floor, ceiling = DEFAULT_FLOOR, DEFAULT_CEILING
    low = " ".join(text.lower().split())  # collapse whitespace for robust matching

    if m := re.search(r"top[\s-]*(\d+)", low):
        p = replace(p, top_k=int(m.group(1)))
    if m := (
        re.search(r"(\d+)[\s-]*bar\s+momentum", low)
        or re.search(r"momentum[^.]*?(\d+)[\s-]*bar", low)
    ):
        p = replace(p, lookback=int(m.group(1)))
    if any(k in low for k in ("inverse vol", "inverse-vol", "inverse volatility")):
        p = replace(p, inverse_vol=True)
    # deployment band: "between 40% and 85%" / "0.40 to 0.85"
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%?\s*(?:and|to|–|—|-)\s*(\d+(?:\.\d+)?)\s*%", low):
        a, b = float(m.group(1)), float(m.group(2))
        a, b = (a / 100 if a > 1 else a), (b / 100 if b > 1 else b)
        floor, ceiling = min(a, b), max(a, b)
    if "rebalance daily" in low or "every day" in low:
        p = replace(p, rebal_bars=6)  # 6 x 4h = daily

    return p, floor, ceiling


def load_spec(path: str | Path | None = None) -> tuple[AllocatorParams, float, float]:
    """Parse the strategy file; fall back to committed defaults if missing/unreadable."""
    try:
        return parse_spec(Path(path or DEFAULT_SPEC).read_text())
    except Exception:
        return AllocatorParams(), DEFAULT_FLOOR, DEFAULT_CEILING


def summary(path: str | Path | None = None, *, n_tokens: int | None = None) -> str:
    """One-line human summary of the parsed strategy (for the identity profile / logs).

    `n_tokens` is the ACTIVE universe size (UI token toggles); default 8 keeps
    the historical wording byte-for-byte for callers that don't pass it.
    """
    p, floor, ceiling = load_spec(path)
    n = n_tokens if n_tokens is not None else 8
    return (
        f"top-{min(p.top_k, n)} of {n} by {p.lookback}-bar momentum, inverse-vol, "
        f"regime-adaptive deploy {floor:.0%}-{ceiling:.0%}, daily rebalance, "
        f"TWAK-signed (native gas; identity gasless via MegaFuel)"
    )
