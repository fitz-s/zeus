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
        CenterBuyCalibratedShadow,
        CenterSellModelNo,
        CenterSellParity,
        CrossMarketCorrelationHedge,
        ImminentOpenCapturePosteriorCollapse,
        LiquidityProvisionWithHeartbeat,
        NegRiskBasket,
        OpeningInertiaRelaxation,
        OpeningStaleQuoteFOK,
        ResolutionWindowMaker,
        SettlementCaptureShadow,
        ShoulderBuyEVT,
        ShoulderImpossibleTailCapture,
        StaleQuoteDetector,
        WeatherEventArbitrage,
    )
    return [
        # Original 9 (pre-wave)
        WeatherEventArbitrage(),
        LiquidityProvisionWithHeartbeat(),
        StaleQuoteDetector(),
        ResolutionWindowMaker(),
        ShoulderImpossibleTailCapture(),
        CenterSellParity(),
        CrossMarketCorrelationHedge(),
        NegRiskBasket(),
        SettlementCaptureShadow(),
        # Wave-added stochastic candidates (S1-S5)
        CenterBuyCalibratedShadow(),
        OpeningInertiaRelaxation(),
        ImminentOpenCapturePosteriorCollapse(),
        CenterSellModelNo(),
        ShoulderBuyEVT(),
        # C-EPIC combination candidate (§14). Wired into the pipeline per
        # operator directive 2026-06-09 ('全部打开'): it was never instantiated in
        # the live pipeline (shadow purgatory). Now it evaluates each cycle and
        # emits its own decision/no_trade rows. Its own EV gate (theorem §14)
        # remains the economic gate — purgatory wiring, not the math, is removed.
        OpeningStaleQuoteFOK(),
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
    world_conn: sqlite3.Connection | None = None,
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
        world_conn:    WORLD-DB connection. Must point to the world DB (owns
                       decision_events / no_trade_events). If None, self-opens
                       via get_world_connection(write_class=WriteClass.LIVE).
                       Callers MUST NOT pass the trade-DB conn here — the
                       candidate writers route on _is_world_db_conn() and will
                       silently fail to write if given a trade-DB connection
                       (K1 ghost-split, MAJOR-1).
        decision_time: Wall-clock time of the decision (for decision_events rows).
    """
    if not shadow_candidate_capture_enabled():
        return

    from src.strategy.candidates import CandidateContext

    # Resolve world connection. Self-open if not provided (live path).
    # Mirrors write_decision_event's conn=None self-open pattern (cycle_runtime.py).
    _own_conn = world_conn is None
    if _own_conn:
        from src.state.db import get_world_connection
        from src.state.db_writer_lock import WriteClass
        world_conn = get_world_connection(write_class=WriteClass.LIVE)

    try:
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
                    conn=world_conn,
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
    finally:
        if _own_conn and world_conn is not None:
            world_conn.close()
