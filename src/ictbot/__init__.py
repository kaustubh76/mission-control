"""ICT AI BOT PRO MAX — package root.

Public submodules:
  - settings           — configuration constants (PAIRS, paths, knobs)
  - data               — exchange adapters (Binance + Delta)
  - indicators         — leaf ICT primitives (bias, poi, mss, fvg, delta, …)
  - strategy           — composition of indicators into a Strategy (Phase 4)
  - engine             — offline analysis (backtest, sweep, wfo, compare, …)
  - portfolio          — journal, equity, risk caps
  - exec               — broker / order routing (Phase 8)
  - notify             — Telegram, etc.
  - runtime            — logger, metrics, sessions, signal memory
  - orchestrator       — analyzer + scanner composition root
  - cli                — command-line entry points
  - ui                 — Streamlit dashboard
"""

__version__ = "0.1.0"
