"""
C3 (ROADMAP §C3) — live trading kill switch.

A *file*-based switch the scanner checks every iteration. We deliberately
do not mutate `settings.enable_live_trading` in memory (that would only
affect the current process); instead the kill switch writes a sentinel
file the scanner re-reads each loop, AND atomically rewrites the
`ENABLE_LIVE_TRADING=...` line in `.env` so a restart picks up the
disabled state.

The dashboard's "kill" button calls `engage()`. Anything live-trading
checks `is_engaged()` and refuses if True.

Note: this is a coarse safety net. The pair-level allowlist on
each live broker (BinanceLiveBroker / DeltaLiveBroker) is the
fine-grained version.
"""

from __future__ import annotations

import os

from ictbot.settings import DATA_DIR, PROJECT_ROOT

# Anchor the safety sentinel + .env to the repo root, NOT the process CWD. Relative
# paths were correct only because every documented launch happens to chdir to the
# repo; a foreign CWD would have silently pointed the kill switch at the wrong files.
KILL_SENTINEL = DATA_DIR / "KILL_SWITCH_ENGAGED"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_KEY = "ENABLE_LIVE_TRADING"


def is_engaged() -> bool:
    """True when the kill switch sentinel exists. Process-local check
    that's cheap enough to run every scanner iteration."""
    return KILL_SENTINEL.exists()


def engage(reason: str = "manual") -> None:
    """Flip the kill switch ON. Side effects:
      1. Create the sentinel file (process-local check).
      2. Rewrite `.env` so ENABLE_LIVE_TRADING=false survives restart.
    The sentinel write happens FIRST so a half-completed engage still
    halts the next scanner tick. Both writes are atomic enough for our
    single-writer use case.
    """
    KILL_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    KILL_SENTINEL.write_text(f"engaged: {reason}\n", encoding="utf-8")
    _rewrite_env_key(ENV_KEY, "false")


def release() -> None:
    """Clear the kill switch. Doesn't flip ENABLE_LIVE_TRADING back on —
    that's a separate manual edit, intentionally. Releasing the kill
    switch is necessary but not sufficient to resume live trading."""
    if KILL_SENTINEL.exists():
        KILL_SENTINEL.unlink()


def _rewrite_env_key(key: str, value: str) -> None:
    """Replace (or append) a single KEY=VALUE line in .env atomically."""
    if not ENV_FILE.exists():
        # Don't create a .env if one doesn't already exist — that would
        # be surprising. The sentinel file alone is enough for the
        # in-process halt.
        return
    text = ENV_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_FILE)


def rewrite_env_key(key: str, value: str) -> None:
    """Public, atomic single-key `.env` writer (KEY=VALUE), reusing the same
    read-modify-`os.replace` the kill switch uses. For one-shot persisters that need
    to survive a restart — e.g. `register_agent` saving the freshly-minted AGENT_ID —
    without reaching into a private helper. No-ops if `.env` doesn't exist (it never
    surprise-creates one)."""
    _rewrite_env_key(key, value)
