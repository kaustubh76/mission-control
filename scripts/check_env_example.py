#!/usr/bin/env python3
"""
Audit M1 acceptance check: every env-settable Settings field must be NAMED in
.env.example (commented-out is fine — the point is discoverability, not defaults).

    PYTHONPATH=src .venv/bin/python scripts/check_env_example.py   # prints `missing: N`

Exit 0 when missing == 0, else 1. Resolves each field's env alias the same way
pydantic does: explicit Field(alias=...) / AliasChoices first, else the upper-cased
field name.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    src = Path("src/ictbot/settings.py").read_text()
    env = Path(".env.example").read_text()

    body = src.split("class Settings(BaseSettings):", 1)[1]
    fields: dict[str, str] = {}
    cur: str | None = None
    for line in body.splitlines():
        m = re.match(r"^    ([a-z_][a-z0-9_]*)\s*:\s*[^=]+=(.*)$", line)
        if m and not line.strip().startswith("#"):
            cur = m.group(1)
            fields[cur] = line
        elif cur and line.startswith("        "):
            fields[cur] += " " + line.strip()

    missing: list[str] = []
    for name, text in fields.items():
        if name == "model_config":
            continue
        aliases = re.findall(r'alias="([A-Z0-9_]+)"', text)
        choices = re.findall(r"AliasChoices\(([^)]*)\)", text)
        if choices:
            aliases = re.findall(r'"([A-Z0-9_]+)"', choices[0])
        if not aliases:
            aliases = [name.upper()]
        if not any(re.search(rf"\b{a}\b", env) for a in aliases):
            missing.append(aliases[0])

    print(f"missing: {len(missing)}")
    for name in missing:
        print(f"  {name}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
