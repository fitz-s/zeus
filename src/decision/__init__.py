# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md Stage 0 (lines 994-1033) +
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (src/decision/ package
#   does not exist yet — create it with __init__.py; schema lives at
#   src/state/schema/no_trade_events_schema.py, NOT spec's src/events/...)

"""src.decision — the q-kernel rebuild decision spine.

Stage 0 of the rebuild creates this package. It holds the one observability
object that makes every live candidate reconstructable from source inputs to
decision: ``DecisionReceipt`` (src/decision/decision_receipt.py).

Later stages add ``payoff_vector.py`` and ``family_decision_engine.py`` here.
"""

from __future__ import annotations

from src.decision.decision_receipt import (
    DecisionReceipt,
    ForecastSpine,
    QSpine,
    RouteSpine,
    SizeSpine,
)

__all__ = [
    "DecisionReceipt",
    "ForecastSpine",
    "QSpine",
    "RouteSpine",
    "SizeSpine",
]
