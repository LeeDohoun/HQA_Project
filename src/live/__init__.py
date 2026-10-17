"""Strategy runtime (M8): shadow and PAPER batch rebalancing of research-passed strategies.

Decisions are read from sealed FW001 records; nothing here recomputes a decision.
Every write and every network call is opt-in (``--execute``).
"""
