"""
The agent layer — what makes the trading core an *AI agent*.

Three pillars wrap the validated regime-adaptive momentum allocator:
  - identity.py     : on-chain agent SDK (bnbagent) — the agent's ERC-8004 identity.
  - strategy_spec.py: the natural-language strategy ("rules you set") -> params.
  - rationale.py    : per-tick natural-language explanation of what it sees + does.

Data and execution (TWAK) live in ictbot.data / ictbot.exec.* respectively.
"""
