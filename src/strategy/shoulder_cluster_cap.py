# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3 + AUTHORITY_GPT_ROUND_1_DOSSIER.md §7.5

"""shoulder_cluster_cap — check_shoulder_cluster_cap function.

Implements the weather-system cluster cap per dossier §7.5:
  "No same-direction shoulder sell across multiple cities under one heat dome/cold front."

Two-gate design:
  Gate 1 (cross-city presence): if any DIFFERENT city already has a same-direction
    entry in this cluster, REFUSE — regardless of $ amount.
  Gate 2 ($ cap): if the total existing notional_usd + proposed_notional exceeds
    SHOULDER_CLUSTER_HARD_CAP_USD, REFUSE.

UNKNOWN regime (empty cluster string) → always allow (plan §5 R-1).

INV-37: caller supplies conn when the ledger check is needed.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from src.types import BinEdge

# Hard dollar cap per cluster per side.
# Operator-tunable post-T3 via config; defaulting to a conservative value.
# This cap is secondary to the cross-city presence gate (Gate 1).
SHOULDER_CLUSTER_HARD_CAP_USD: float = 2000.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShoulderClusterContext:
    cluster_id: str
    side: str
    regime: str


def shoulder_cluster_context_for_edge(
    *,
    city_name: str,
    target_date: str,
    edge: BinEdge,
) -> ShoulderClusterContext | None:
    """Return the canonical shoulder risk context for an edge, if applicable."""

    if not getattr(edge.bin, "is_shoulder", False):
        return None
    try:
        from src.contracts.weather_regime_tag import WeatherRegimeTag
        from src.strategy.correlation_cluster import tail_correlation_cluster_for

        regime = getattr(edge, "tail_regime_tag", WeatherRegimeTag.UNKNOWN)
        if isinstance(regime, str):
            try:
                regime = WeatherRegimeTag(regime)
            except ValueError:
                regime = WeatherRegimeTag.UNKNOWN
        target = date.fromisoformat(str(target_date))
        cluster_id = tail_correlation_cluster_for(city_name, regime, target)
        if not cluster_id:
            return None
        side = "sell" if edge.direction == "buy_no" else "buy"
        return ShoulderClusterContext(
            cluster_id=cluster_id,
            side=side,
            regime=str(getattr(regime, "value", regime)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "shoulder cluster context unavailable for city=%s target_date=%s: %s",
            city_name,
            target_date,
            exc,
        )
        return None


def shoulder_cluster_cap_rejection(
    *,
    conn: sqlite3.Connection | None,
    city_name: str,
    target_date: str,
    edge: BinEdge,
    proposed_notional: float,
) -> str | None:
    """Return a rejection reason when shoulder cluster cap blocks the edge."""

    context = shoulder_cluster_context_for_edge(
        city_name=city_name,
        target_date=target_date,
        edge=edge,
    )
    if context is None:
        return None
    if conn is None:
        logger.warning(
            "shoulder_cluster_cap skipped without DB conn (paper/shadow path): "
            "cluster=%s side=%s proposed_notional=%.4f",
            context.cluster_id,
            context.side,
            proposed_notional,
        )
        return None
    try:
        cap_ok, cap_reason = check_shoulder_cluster_cap(
            cluster=context.cluster_id,
            side=context.side,
            proposed_notional=float(proposed_notional),
            conn=conn,
            proposing_city=city_name,
        )
    except (sqlite3.OperationalError, AttributeError, ImportError) as exc:
        return f"shoulder_cluster_cap_unavailable: {exc}"
    if not cap_ok:
        return cap_reason
    return None


def record_accepted_shoulder_exposure(
    *,
    conn: sqlite3.Connection | None,
    city_name: str,
    target_date: str,
    edge: BinEdge,
    notional_usd: float,
    decision_event_id: str,
    observed_at: datetime,
    source: str,
) -> str | None:
    """Append accepted shoulder exposure into the risk ledger."""

    context = shoulder_cluster_context_for_edge(
        city_name=city_name,
        target_date=target_date,
        edge=edge,
    )
    if context is None or conn is None:
        return None
    try:
        from src.state.shoulder_exposure_ledger import write_shoulder_exposure_entry

        write_shoulder_exposure_entry(
            shoulder_side=context.side,
            weather_system_cluster=context.cluster_id,
            city=city_name,
            target_date=target_date,
            source=source or "evaluator",
            regime=context.regime,
            notional_usd=float(notional_usd),
            decision_event_id=decision_event_id,
            observed_at=observed_at.isoformat(),
            conn=conn,
        )
    except (sqlite3.OperationalError, AttributeError, ImportError) as exc:
        return f"shoulder_exposure_ledger_write_failed: {exc}"
    return None


def check_shoulder_cluster_cap(
    cluster: str,
    side: str,
    proposed_notional: float,
    *,
    conn: sqlite3.Connection,
    proposing_city: Optional[str] = None,
) -> tuple[bool, str]:
    """Check whether a new shoulder entry can be added to cluster without breaching cap.

    Parameters
    ----------
    cluster:
        weather_system_cluster ID (from correlation_cluster.tail_correlation_cluster_for).
        Empty string → UNKNOWN regime → always allow (no cluster aggregation).
    side:
        "sell" or "buy" — direction of the proposed shoulder exposure.
    proposed_notional:
        Proposed notional in USD for the new entry.
    conn:
        World-DB connection (INV-37). Required — caller provides.
    proposing_city:
        City name of the proposing edge. When provided, Gate 1 checks whether a
        DIFFERENT city already has a same-direction entry in the cluster.
        When None, Gate 1 is skipped (only $ cap applies).

    Returns
    -------
    tuple[bool, str]
        (allowed, reason)
        allowed=True → entry is permitted.
        allowed=False → entry refused; reason explains why.

    Notes
    -----
    This function fires BEFORE phase_aware_kelly_multiplier in evaluator.py
    (Invariant 4: wasted compute is the failure mode). Called only for
    is_shoulder edges — caller must gate on edge.bin.is_shoulder.

    Design: Gate 1 (cross-city presence) is checked first because it is cheaper
    and more restrictive per dossier §7.5 "No same-direction shoulder sell across
    multiple cities". Gate 2 ($ cap) is the fallback for single-city accumulation.
    """
    # UNKNOWN regime or empty cluster → no aggregation, always allow.
    if not cluster:
        return (True, "")

    from src.state.shoulder_exposure_ledger import (
        read_cluster_exposure,
        read_distinct_cities_in_cluster,
    )

    # Gate 1: cross-city presence check (dossier §7.5).
    # If a DIFFERENT city already has a same-direction entry, refuse.
    if proposing_city is not None:
        existing_cities = read_distinct_cities_in_cluster(cluster, side, conn=conn)
        other_cities = [c for c in existing_cities if c != proposing_city]
        if other_cities:
            return (
                False,
                (
                    f"shoulder_cluster_cap: cross-city {side!r} exposure already exists in "
                    f"cluster {cluster!r} from {other_cities} — "
                    f"same-direction {side!r} from {proposing_city!r} refused "
                    f"(dossier §7.5 no same-direction shoulder sell across multiple cities)"
                ),
            )

    # Gate 2: $ hard cap check.
    existing_total = read_cluster_exposure(cluster, side, conn=conn)
    projected_total = existing_total + proposed_notional
    if projected_total > SHOULDER_CLUSTER_HARD_CAP_USD:
        return (
            False,
            (
                f"shoulder_cluster_cap: projected cluster {side!r} notional "
                f"${projected_total:.2f} exceeds hard cap "
                f"${SHOULDER_CLUSTER_HARD_CAP_USD:.2f} for cluster {cluster!r}"
            ),
        )

    return (True, "")
