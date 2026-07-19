"""
Unified CLI dispatcher.

Usage:
  python -m ictbot <command> [args...]

Commands:
  scan              Run the background scanner (alerts to Telegram)
  app               Launch the Streamlit dashboard
  backtest          Walk-forward backtest one pair
  sweep             Grid-search parameters on one pair (or --all)
  wfo               Walk-forward optimisation: train + out-of-sample test
  compare           Compare sma / swing / slope bias engines
  bt-curve          Write backtest equity curve for the dashboard
  size              Position sizing calculator (fixed / Kelly / RoR)
  journal           Inspect the signal journal

Anything you can pass to the underlying script you can pass here. Example:

  python -m ictbot backtest BTC/USDT:USDT --bars 5000 --invert
"""

import subprocess
import sys

COMMANDS = {
    "scan": "ictbot.orchestrator.scanner",
    "backtest": "ictbot.engine.backtest",
    "sweep": "ictbot.engine.sweep",
    "wfo": "ictbot.engine.wfo",
    "compare": "ictbot.engine.compare",
    "bt-curve": "ictbot.engine.bt_curve",
    "bt_curve": "ictbot.engine.bt_curve",  # underscore alias
    "size": "ictbot.engine.sizing",
    "journal": "ictbot.cli.journal_cmd",
}


def _print_help() -> int:
    print(__doc__)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        return _print_help()

    cmd, rest = argv[0], argv[1:]

    if cmd == "app":
        # Streamlit needs to be invoked through its own runner.
        return subprocess.call(["streamlit", "run", "src/ictbot/ui/app.py", *rest])

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd!r}\n", file=sys.stderr)
        _print_help()
        return 2

    module = COMMANDS[cmd]
    # Dispatch by importing the module's main() — avoids subprocess overhead
    # and keeps tracebacks first-class.
    import importlib

    mod = importlib.import_module(module)
    # Replace argv so the target's argparse sees a sensible program name.
    sys.argv = [f"python -m ictbot {cmd}", *rest]
    return mod.main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
