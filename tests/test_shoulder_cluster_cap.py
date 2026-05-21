# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3 + §3 Invariants
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Relationship + unit tests for shoulder_cluster_cap.check_shoulder_cluster_cap — two-gate cluster cap.
# Reuse: Run when shoulder_cluster_cap, shoulder_exposure_ledger, or evaluator cluster-cap wire changes.

"""Tests for shoulder_cluster_cap.py — check_shoulder_cluster_cap and related invariants.

Relationship tests (per Fitz methodology: tests land BEFORE implementation):
  test_inv_shoulder_cluster_cap_prevents_correlated_overconcentration — cross-module
  test_same_direction_shoulder_sell_refuse_across_cluster — cap NOT numerically exceeded
    but 2nd-city same-direction shoulder sell under same cluster still REFUSE (plan §2 T3 G3)

Function tests (post-implementation):
  test_cap_allow_opposite_side
  test_cap_allow_different_cluster
  test_empty_ledger_always_allows
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal in-memory ledger setup
# ---------------------------------------------------------------------------

def _make_world_conn() -> sqlite3.Connection:
    """Create an in-memory world DB with shoulder_exposure_ledger table."""
    conn = sqlite3.connect(":memory:")
    from src.state.schema.shoulder_exposure_ledger_schema import ensure_table
    ensure_table(conn)
    return conn


def _insert_ledger_row(
    conn: sqlite3.Connection,
    *,
    shoulder_side: str,
    weather_system_cluster: str,
    city: str,
    target_date: str = "2026-07-15",
    source: str = "ecmwf",
    regime: str = "heat_dome",
    notional_usd: float = 100.0,
    decision_event_id: str = "deid_v1_test_01",
    observed_at: str = "2026-07-10T12:00:00Z",
    schema_version: int = 23,
) -> None:
    conn.execute(
        """
        INSERT INTO shoulder_exposure_ledger (
            shoulder_side, weather_system_cluster, city, target_date,
            source, regime, notional_usd, decision_event_id,
            observed_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shoulder_side, weather_system_cluster, city, target_date,
            source, regime, notional_usd, decision_event_id,
            observed_at, schema_version,
        ),
    )


# ---------------------------------------------------------------------------
# RELATIONSHIP TESTS (must FAIL before implementation, GREEN after)
# ---------------------------------------------------------------------------

class TestInvShoulderClusterCapPreventsCorrelatedOverconcentration:
    """INV: cluster cap prevents same-direction shoulder sell overconcentration.

    Two cities (Atlanta, Chicago) both under heat_dome_east_2026_07_15 cluster.
    Atlanta already has a sell entry in the ledger.
    Chicago same-direction sell should be REFUSED by check_shoulder_cluster_cap.

    Cross-module invariant: shoulder_cluster_cap queries shoulder_exposure_ledger
    to detect cross-city correlation.
    """

    def test_second_city_same_direction_refused_when_cluster_saturated(self) -> None:
        """check_shoulder_cluster_cap refuses 2nd city same-direction when $ cap exceeded."""
        conn = _make_world_conn()
        cluster = "heat_dome_east_2026_07_15"
        # Atlanta already in ledger — large notional exceeding cap
        _insert_ledger_row(
            conn,
            shoulder_side="sell",
            weather_system_cluster=cluster,
            city="Atlanta",
            notional_usd=5000.0,
        )

        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        allowed, reason = check_shoulder_cluster_cap(
            cluster=cluster,
            side="sell",
            proposed_notional=500.0,
            conn=conn,
        )
        assert not allowed, "Cap exceeded: 2nd city same-direction sell must be REFUSED"
        assert "cluster" in reason.lower() or "cap" in reason.lower(), (
            f"reason should mention cluster/cap: {reason!r}"
        )

    def test_opposite_side_allowed_even_in_same_cluster(self) -> None:
        """Opposite-direction (buy) is NOT blocked by a sell cluster cap."""
        conn = _make_world_conn()
        cluster = "heat_dome_east_2026_07_15"
        _insert_ledger_row(
            conn,
            shoulder_side="sell",
            weather_system_cluster=cluster,
            city="Atlanta",
            notional_usd=5000.0,
        )

        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        allowed, _reason = check_shoulder_cluster_cap(
            cluster=cluster,
            side="buy",
            proposed_notional=500.0,
            conn=conn,
        )
        assert allowed, "Opposite side (buy) must not be blocked by sell cluster cap"


class TestSameDirectionShoulderSellRefuseAcrossCluster:
    """G3 (plan §2 T3): cluster cap NOT numerically exceeded but 2nd-city same-direction
    shoulder sell still REFUSE under same heat-dome cluster.

    Scenario: Atlanta has a tiny sell entry (notional=50 USD).
    Cluster $ cap is e.g. 10000 USD.
    Chicago proposes a 100 USD sell in the same cluster.
    Total = 150 USD << cap.
    HOWEVER: a same-direction entry from a *different city* exists in the cluster —
    check_shoulder_cluster_cap must REFUSE (presence, not just $ amount).
    """

    def test_refuse_on_second_city_same_direction_regardless_of_cap_amount(self) -> None:
        """Present-in-cluster from different city → REFUSE even below $ cap."""
        conn = _make_world_conn()
        cluster = "heat_dome_east_2026_07_15"
        # Atlanta has a tiny sell — far below any $ cap
        _insert_ledger_row(
            conn,
            shoulder_side="sell",
            weather_system_cluster=cluster,
            city="Atlanta",
            notional_usd=50.0,  # tiny — cap not numerically exceeded
        )

        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        # Chicago tries to add a sell in the same cluster — different city
        allowed, reason = check_shoulder_cluster_cap(
            cluster=cluster,
            side="sell",
            proposed_notional=100.0,  # total=150 << cap
            conn=conn,
            proposing_city="Chicago",  # different city
        )
        assert not allowed, (
            "Same-direction sell from different city must be REFUSED "
            "even when $ cap not exceeded"
        )
        assert reason, "Must provide a reason string"

    def test_same_city_second_entry_allowed_if_under_cap(self) -> None:
        """Same city adding more to its own cluster entry is allowed if under cap."""
        conn = _make_world_conn()
        cluster = "heat_dome_east_2026_07_15"
        _insert_ledger_row(
            conn,
            shoulder_side="sell",
            weather_system_cluster=cluster,
            city="Atlanta",
            notional_usd=50.0,
        )

        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        # Atlanta adds more to its own position — same city, same cluster, under cap
        allowed, _reason = check_shoulder_cluster_cap(
            cluster=cluster,
            side="sell",
            proposed_notional=100.0,
            conn=conn,
            proposing_city="Atlanta",  # same city — only $ check applies
        )
        assert allowed, "Same city adding more under cap should be allowed"

    def test_empty_cluster_always_allows(self) -> None:
        """No existing entries → always allow."""
        conn = _make_world_conn()
        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        allowed, _ = check_shoulder_cluster_cap(
            cluster="heat_dome_east_2026_07_15",
            side="sell",
            proposed_notional=100.0,
            conn=conn,
            proposing_city="Atlanta",
        )
        assert allowed

    def test_different_cluster_always_allows(self) -> None:
        """Different cluster → no cross-cluster correlation; always allow."""
        conn = _make_world_conn()
        cluster_a = "heat_dome_east_2026_07_15"
        cluster_b = "cold_snap_central_2026_01_20"
        _insert_ledger_row(
            conn,
            shoulder_side="sell",
            weather_system_cluster=cluster_a,
            city="Atlanta",
            notional_usd=5000.0,
        )

        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        allowed, _ = check_shoulder_cluster_cap(
            cluster=cluster_b,
            side="sell",
            proposed_notional=100.0,
            conn=conn,
            proposing_city="Chicago",
        )
        assert allowed, "Different cluster must not block"


# ---------------------------------------------------------------------------
# FUNCTION TESTS (check_shoulder_cluster_cap interface)
# ---------------------------------------------------------------------------

class TestCheckShoulderClusterCapInterface:
    """verify the function signature and return contract."""

    def test_returns_tuple_bool_str(self) -> None:
        conn = _make_world_conn()
        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        result = check_shoulder_cluster_cap(
            cluster="heat_dome_east_2026_07_15",
            side="sell",
            proposed_notional=100.0,
            conn=conn,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        allowed, reason = result
        assert isinstance(allowed, bool)
        assert isinstance(reason, str)

    def test_unknown_cluster_empty_string_always_allows(self) -> None:
        """UNKNOWN regime → empty cluster string → no aggregation (plan §5 R-1)."""
        conn = _make_world_conn()
        from src.strategy.shoulder_cluster_cap import check_shoulder_cluster_cap
        allowed, _ = check_shoulder_cluster_cap(
            cluster="",  # UNKNOWN regime → empty cluster
            side="sell",
            proposed_notional=9999.0,
            conn=conn,
        )
        assert allowed, "Empty cluster (UNKNOWN regime) must never block"
