"""
Read-only dashboard backend for the regime-adaptive trading agent.

A thin FastAPI layer that surfaces the allocator's already-written artifacts
(`data/journal/allocator_journal.jsonl`, `allocator_state.json`) plus the three
agent pillars (market regime read, TWAK execution journal, ERC-8004 identity) to a
React "Mission Control" SPA. It WRAPS the existing readers/functions — it never
re-implements strategy logic, and the read path never imports ccxt or a live broker.

Opt-in dependency:  python -m pip install -e ".[api]"
Launch:             make api    (uvicorn ictbot.api.app:app on :8000)
"""
