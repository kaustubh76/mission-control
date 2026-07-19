"""Entry point so `python -m ictbot` works.

Delegates to ictbot.cli.__main__.main.
"""

from ictbot.cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
