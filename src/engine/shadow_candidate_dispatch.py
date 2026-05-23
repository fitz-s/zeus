# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/PROMOTION_PIPELINE_DESIGN.md §4
"""Track L-1: fail-open live shadow-candidate dispatch.

Dispatches all registered shadow candidates against the already-computed
MarketAnalysisVNext metrics. Each candidate writes its own decision_events or
no_trade_events row via the canonical writers inside evaluate().

Design constraints (§4):
  - Config flag ZEUS_SHADOW_CANDIDATE_CAPTURE (default OFF — research flag).
  - ENTIRE dispatch block is fail-open: any exception per-candidate is caught,
    logged, and execution continues. The live decision is NEVER affected.
  - Reuses already-computed analysis; does NOT re-run mainline evaluation.
  - Writes to WORLD.decision_events (source='shadow_decision') when live conn
    is passed; writes to in-memory DB in tests.

live_status: shadow dispatch only. No live sizing or order submission.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from src.contracts.decision_natural_key import DecisionNaturalKey

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

_SHADOW_CAPTURE_ENV = "ZEUS_SHADOW_CANDIDATE_CAPTURE"
_TRUTHY_FLAG_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSY_FLAG_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_warned_unrecognized: set[str] = set()


def shadow_candidate_capture_enabled() -> bool:
    """Return True iff ZEUS_SHADOW_CANDIDATE_CAPTURE is explicitly enabled.

    Default: OFF (research flag — must be explicitly opted in).

    Recognized values (case-insensitive):
      truthy: "1", "true", "yes", "on"
      falsy:  "0", "false", "no", "off"

    Empty / whitespace → default OFF.
    Unrecognized non-empty values → warn once, remain OFF (conservative).
    """
    raw = os.environ.get(_SHADOW_CAPTURE_ENV, "").strip().lower()
    if raw == "":
        return False  # default OFF
    if raw in _TRUTHY_FLAG_VALUES:
        return True
    if raw in _FALSY_FLAG_VALUES:
        return False
    # Unrecognized: warn once, stay OFF
    if raw not in _warned_unrecognized:
        _warned_unrecognized.add(raw)
        logger.warning(
            "Unrecognized %s=%r — expected one of %s (truthy) or %s (falsy). "
            "Remaining at default OFF. Fix the env var to silence this warning.",
            _SHADOW_CAPTURE_ENV, raw,
            sorted(_TRUTHY_FLAG_VALUES), sorted(_FALSY_FLAG_VALUES),
        )
    return False


# ---------------------------------------------------------------------------
# Registered shadow candidates
# ---------------------------------------------------------------------------

def _build_candidate_list() -> List[Any]:
    """Instantiate all registered shadow candidates.

    Import is deferred to avoid circular imports at module load time.
    Called once at module level; result stored in _ALL_SHADOW_CANDIDATES.
    """
    from src.strategy.candidates import (
        CenterSellParity,
        CrossMarketCorrelationHedge,
        LiquidityProvisionWithHeartbeat,
        NegRiskBasket,
        ResolutionWindowMaker,
        SettlementCaptureShadow,
        ShoulderImpossibleTailCapture,
        StaleQuoteDetector,
        WeatherEventArbitrage,
    )
    return [
        WeatherEventArbitrage(),
        LiquidityProvisionWithHeartbeat(),
        StaleQuoteDetector(),
        ResolutionWindowMaker(),
        ShoulderImpossibleTailCapture(),
        CenterSellParity(),
        CrossMarketCorrelationHedge(),
        NegRiskBasket(),
        SettlementCaptureShadow(),
    ]


# Module-level list — monkeypatchable in tests.
_ALL_SHADOW_CANDIDATES: List[Any] = _build_candidate_list()


# ---------------------------------------------------------------------------
# Main dispatch entry point
# ---------------------------------------------------------------------------

def dispatch_shadow_candidates(
    *,
    analysis: Any,
    natural_key: "DecisionNaturalKey",
    observed_at: str,
    conn: sqlite3.Connection,
    decision_time: datetime,
) -> None:
    """Dispatch all shadow candidates against the given analysis.

    Fail-open: any exception (per-candidate or outer) is caught, logged,
    and execution continues. The live cycle is NEVER affected.

    When shadow_candidate_capture_enabled() is False, returns immediately
    without dispatching any candidate.

    Args:
        analysis:      Already-computed MarketAnalysisVNext result (or analysis
                       object with a .metrics attribute). Passed through as-is.
        natural_key:   DecisionNaturalKey for the current market / observation.
        observed_at:   ISO-8601 UTC timestamp of the observation.
        conn:          DB connection. World-DB in live; in-memory in tests.
        decision_time: Wall-clock time of the decision (for decision_events rows).
    """
    if not shadow_candidate_capture_enabled():
        return

    from src.strategy.candidates import CandidateContext, write_candidate_no_trade_row

    try:
        context = CandidateContext(
            natural_key=natural_key,
            observed_at=observed_at,
            analysis=analysis,
        )
    except Exception as exc:
        logger.exception(
            "shadow_candidate_dispatch: failed to build CandidateContext — skipping all. "
            "natural_key=%r observed_at=%r exc=%r",
            natural_key, observed_at, exc,
        )
        return

    for candidate in _ALL_SHADOW_CANDIDATES:
        try:
            candidate.evaluate(
                context=context,
                conn=conn,
                decision_time=decision_time,
            )
        except Exception as exc:
            logger.exception(
                "shadow_candidate_dispatch: candidate %r raised — skipping. exc=%r",
                getattr(candidate, "strategy_key", repr(candidate)),
                exc,
            )
            # Per-candidate fail-open: continue to next candidate
            continue
