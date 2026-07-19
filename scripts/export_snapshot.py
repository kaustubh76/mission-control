#!/usr/bin/env python3
"""
Dump the current dashboard snapshot to web/public/snapshot.json.

Vite copies public/* into the build, so this becomes the SPA's OFFLINE FALLBACK:
when the live API isn't reachable (e.g. a static Vercel deploy, or the Render API
cold-starting), the dashboard still renders real — if frozen — data instead of
blanking. Run by scripts/build_web.sh before each build.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Pin the LOCAL ictbot (this venv otherwise resolves ictbot to a sibling repo).
sys.path.insert(0, str(ROOT / "src"))

from ictbot.api import reads  # noqa: E402


def _default(o):
    """Serialize like the live API would: on-chain reads come back as Decimal — emit them as JSON
    NUMBERS (not strings) so the committed fallback matches the Pydantic-coerced live payload. A bare
    `default=str` stringified them, which broke components doing numeric ops (e.g. IdentityCard's
    `bnb.toFixed(4)`) the moment the SPA fell back to this snapshot. Anything else → str (datetime etc.)."""
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def main() -> int:
    out = ROOT / "web" / "public" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reads.snapshot(), indent=2, default=_default))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
