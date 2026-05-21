# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T3
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""RegretDecomposer — 7-component per-trade regret attribution.

Decomposes realized regret (realized_pnl − counterfactual_pnl) into 7 components
per dossier §6.6:

  1. forecast_error_usd          — regret from forecast/belief error at decision time
  2. observation_error_usd       — regret from observation/source error
  3. quote_error_usd             — regret from market quote error (mid vs fill)
  4. non_fill_error_usd          — regret from non-fill (order not executed)
  5. fee_error_usd               — regret from fee/spread estimation error
  6. timing_error_usd            — regret from alpha decay / timing error
  7. settlement_ambiguity_error_usd — regret from settlement source/oracle ambiguity
                                     at DECISION TIME (not general settlement deviation)

Sum invariant: sum(7 components) == realized_pnl - counterfactual_pnl within 1e-9.

Design notes
------------
- Components are signed USD values; positive = regret (cost); negative = gain vs counterfactual.
- The decomposition is additive by construction: callers provide the 7 component values
  and the total is verified; if not balanced, the caller must adjust one component
  (typically the residual / catch-all).
- ``settlement_ambiguity_error_usd`` captures uncertainty about which settlement source
  applies AT THE MOMENT OF DECISION, not ex-post settlement deviation from prediction.

INV-37: DB-writing functions accept a ``conn`` argument (caller-supplied).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegretComponents:
    """7-component decomposition of per-trade regret.

    All values in USD (signed; positive = cost/regret, negative = gain vs counterfactual).

    Sum invariant: sum of all 7 components == total_regret_usd within 1e-9.
    """
    forecast_error_usd: float
    observation_error_usd: float
    quote_error_usd: float
    non_fill_error_usd: float
    fee_error_usd: float
    timing_error_usd: float
    settlement_ambiguity_error_usd: float   # column name per plan §T3
    total_regret_usd: float                 # realized_pnl - counterfactual_pnl

    def verify_sum(self, tolerance: float = 1e-9) -> None:
        """Raise ValueError if components do not sum to total_regret_usd."""
        component_sum = (
            self.forecast_error_usd
            + self.observation_error_usd
            + self.quote_error_usd
            + self.non_fill_error_usd
            + self.fee_error_usd
            + self.timing_error_usd
            + self.settlement_ambiguity_error_usd
        )
        delta = abs(component_sum - self.total_regret_usd)
        if delta > tolerance:
            raise ValueError(
                f"RegretComponents sum={component_sum:.15f} != "
                f"total_regret_usd={self.total_regret_usd:.15f} "
                f"(delta={delta:.2e} > tolerance={tolerance:.2e})"
            )


# ---------------------------------------------------------------------------
# Decomposition function
# ---------------------------------------------------------------------------

def decompose_regret(
    *,
    forecast_error_usd: float = 0.0,
    observation_error_usd: float = 0.0,
    quote_error_usd: float = 0.0,
    non_fill_error_usd: float = 0.0,
    fee_error_usd: float = 0.0,
    timing_error_usd: float = 0.0,
    settlement_ambiguity_error_usd: float = 0.0,
    realized_pnl_usd: float,
    counterfactual_pnl_usd: float,
) -> RegretComponents:
    """Construct a RegretComponents and verify the sum invariant.

    total_regret_usd = realized_pnl_usd - counterfactual_pnl_usd.

    The 7 components must sum to total_regret_usd within 1e-9. If they do not,
    ValueError is raised. Callers should allocate the residual to whichever
    component is the catch-all for that trade (typically timing_error_usd).

    Parameters
    ----------
    forecast_error_usd:
        Regret attributable to forecast/belief error at decision time.
    observation_error_usd:
        Regret attributable to observation/source measurement error.
    quote_error_usd:
        Regret attributable to market quote error (mid vs actual fill).
    non_fill_error_usd:
        Regret attributable to non-fill (order not executed when it should be).
    fee_error_usd:
        Regret attributable to fee/spread estimation error.
    timing_error_usd:
        Regret attributable to alpha decay / timing / residual.
    settlement_ambiguity_error_usd:
        Regret attributable to settlement source/oracle ambiguity AT DECISION TIME.
    realized_pnl_usd:
        Actual realized PnL for this trade.
    counterfactual_pnl_usd:
        Counterfactual (hypothetical best) PnL for this trade.

    Returns
    -------
    RegretComponents
        Verified (sum == total within 1e-9) decomposition.

    Raises
    ------
    ValueError
        If components do not sum to total_regret_usd within 1e-9.
    """
    total_regret_usd = realized_pnl_usd - counterfactual_pnl_usd
    components = RegretComponents(
        forecast_error_usd=forecast_error_usd,
        observation_error_usd=observation_error_usd,
        quote_error_usd=quote_error_usd,
        non_fill_error_usd=non_fill_error_usd,
        fee_error_usd=fee_error_usd,
        timing_error_usd=timing_error_usd,
        settlement_ambiguity_error_usd=settlement_ambiguity_error_usd,
        total_regret_usd=total_regret_usd,
    )
    components.verify_sum()
    return components


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def write_regret_decomposition(
    experiment_id: str,
    decision_event_id: str,
    components: RegretComponents,
    *,
    conn: sqlite3.Connection,
    computed_at: Optional[datetime] = None,
) -> int:
    """Insert a RegretComponents row into regret_decompositions.

    Returns the rowid of the inserted row.

    INV-37: caller supplies conn; never auto-opens.
    """
    if computed_at is None:
        computed_at = datetime.now(tz=timezone.utc)
    elif computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)

    cursor = conn.execute(
        """
        INSERT INTO regret_decompositions (
            experiment_id, decision_event_id,
            forecast_error_usd, observation_error_usd, quote_error_usd,
            non_fill_error_usd, fee_error_usd, timing_error_usd,
            settlement_ambiguity_error_usd, total_regret_usd, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            decision_event_id,
            components.forecast_error_usd,
            components.observation_error_usd,
            components.quote_error_usd,
            components.non_fill_error_usd,
            components.fee_error_usd,
            components.timing_error_usd,
            components.settlement_ambiguity_error_usd,
            components.total_regret_usd,
            computed_at.isoformat(),
        ),
    )
    return cursor.lastrowid
