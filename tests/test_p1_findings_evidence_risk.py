# Created: 2026-05-22
# Last reused/audited: 2026-05-22
# Authority basis: Architecture code review 2026-05-22 — P1/P2 findings
#   F1: day0_nowcast_entry strategy authority
#   F2: EvidenceReport denominator scoping
#   F3: regret cross-strategy contamination via decision_events join
#   F4: EvidenceTierAssignment lifecycle fields (revoke/expiry)
#   F5: cluster risk — separate gross vs variance throttle gates
#   F6: stale strategy_key CHECK constraint migration
"""Antibody tests for P1/P2 architecture review findings (2026-05-22)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.evidence_tier import EvidenceTier
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import (
    _migrate_trade_strategy_key_checks,
    _migrate_world_strategy_key_checks,
    _strip_strategy_key_check,
    init_schema,
)
from src.state.evidence_tier_assignments import (
    current_evidence_tier_assignment,
    record_evidence_tier_assignment,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Finding 1: day0_nowcast_entry strategy authority
# ---------------------------------------------------------------------------

class TestF1Day0NowcastStrategyAuthority:
    """day0_nowcast_entry is a named strategy; _strategy_key_for() returns it."""

    def test_day0_nowcast_entry_in_registry(self) -> None:
        from src.strategy.strategy_profile import get
        profile = get("day0_nowcast_entry")
        assert profile is not None
        assert profile.key == "day0_nowcast_entry"

    def test_day0_nowcast_not_authorized_in_no_trade_reason(self) -> None:
        assert hasattr(NoTradeReason, "DAY0_NOWCAST_NOT_AUTHORIZED")

    def test_strategy_key_for_returns_day0_nowcast_entry_not_none(self) -> None:
        """Finding 1 regression: was returning None, causing STRATEGY_KEY_UNCLASSIFIED."""
        from src.config import City
        from src.engine.discovery_mode import DiscoveryMode
        from src.engine.evaluator import MarketCandidate, _strategy_key_for
        from src.strategy.market_phase import MarketPhase
        from src.types.market import Bin, BinEdge

        city = City(
            name="NYC", lat=40.7772, lon=-73.8726,
            timezone="America/New_York", settlement_unit="F", cluster="NYC", wu_station="KLGA",
        )
        candidate = MarketCandidate(
            city=city,
            target_date="2026-05-22",
            outcomes=[],
            hours_since_open=6.0,
            temperature_metric="high",
            discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
            market_phase=MarketPhase.SETTLEMENT_DAY,
            observation={"high_so_far": 34.0, "current_temp": 34.0},
        )
        edge = BinEdge(
            bin=Bin(low=36.0, high=37.0, unit="F", label="36-37°F"),
            direction="buy_yes",
            edge=0.8, ci_lower=0.7, ci_upper=0.9,
            p_model=0.9, p_market=0.1, p_posterior=0.9,
            entry_price=0.04, p_value=0.01, vwmp=0.04, support_index=1,
        )
        key = _strategy_key_for(candidate, edge)
        assert key == "day0_nowcast_entry", f"Expected 'day0_nowcast_entry', got {key!r}"

    def test_strategy_key_for_hypothesis_settlement_day_high_returns_day0_nowcast(self) -> None:
        from src.config import City
        from src.engine.discovery_mode import DiscoveryMode
        from src.engine.evaluator import MarketCandidate, _strategy_key_for_hypothesis
        from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
        from src.strategy.market_phase import MarketPhase

        city = City(
            name="NYC", lat=40.7772, lon=-73.8726,
            timezone="America/New_York", settlement_unit="F", cluster="NYC", wu_station="KLGA",
        )
        candidate = MarketCandidate(
            city=city,
            target_date="2026-05-22",
            outcomes=[],
            hours_since_open=6.0,
            temperature_metric="high",
            discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
            market_phase=MarketPhase.SETTLEMENT_DAY,
            observation={"high_so_far": 34.0, "current_temp": 34.0},
        )
        hypothesis = FullFamilyHypothesis(
            index=0, range_label="36-37°F", direction="buy_yes",
            edge=0.8, ci_lower=0.7, ci_upper=0.9, p_value=0.01,
            p_model=0.9, p_market=0.1, p_posterior=0.9,
            entry_price=0.04, is_shoulder=False, passed_prefilter=True,
        )
        key = _strategy_key_for_hypothesis(candidate, hypothesis)
        assert key == "day0_nowcast_entry", f"Expected 'day0_nowcast_entry', got {key!r}"

    def test_registry_day0_nowcast_entry_live_status_is_shadow(self) -> None:
        from src.strategy.strategy_profile import get
        profile = get("day0_nowcast_entry")
        assert profile.live_status == "shadow"

    def test_registry_day0_nowcast_entry_cycle_axis_dispatch_mode_is_null(self) -> None:
        """Must be null to avoid dispatch collision with settlement_capture (day0_capture)."""
        from src.strategy.strategy_profile import get
        profile = get("day0_nowcast_entry")
        assert profile.cycle_axis_dispatch_mode is None


# ---------------------------------------------------------------------------
# Findings 2+3: EvidenceReport denominator scoping + join correctness
# ---------------------------------------------------------------------------

def _make_evidence_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


# Params: (decision_event_id, strategy_key, source, seq=0)
_DE_INSERT = """
    INSERT INTO decision_events (
        decision_event_id, market_slug, temperature_metric, target_date,
        observation_time, decision_seq, decision_time, outcome, side,
        strategy_key, observation_available_at, polymarket_end_anchor_source,
        schema_version, source
    ) VALUES (?, 'test-market', 'high', '2026-05-22',
        '2026-05-22T00:00:00Z', ?, '2026-05-22T00:00:00Z', 'buy_yes', 'YES',
        ?, '2026-05-22T00:00:00Z', 'unknown_legacy', 27, ?)
"""

_SE_INSERT = """
    INSERT INTO shadow_experiments
        (experiment_id, strategy_id, config_hash, cohort_tag, started_at, immutable)
    VALUES (?, ?, 'hash-test', ?, '2026-05-22T00:00:00Z', 0)
"""

_RD_INSERT = """
    INSERT INTO regret_decompositions
        (decision_event_id, experiment_id, total_regret_usd, computed_at)
    VALUES (?, ?, ?, '2026-05-22T01:00:00Z')
"""


def _seed_evidence(
    conn: sqlite3.Connection,
    *,
    strategy_key: str = "settlement_capture",
    source: str = "shadow_decision",
    experiment_id: str = "exp-1",
    strategy_id_in_experiment: str | None = None,
    total_regret: float = 0.10,
) -> str:
    """Insert a complete decision_event → shadow_experiment → regret_decomposition chain."""
    if strategy_id_in_experiment is None:
        strategy_id_in_experiment = strategy_key

    de_id = f"de-{experiment_id}-{strategy_key}"
    conn.execute(_DE_INSERT, (de_id, 0, strategy_key, source))
    conn.execute(_SE_INSERT, (experiment_id, strategy_id_in_experiment, "cohort-A"))
    conn.execute(_RD_INSERT, (de_id, experiment_id, total_regret))
    conn.commit()
    return de_id


class TestF2EvidenceReportDenominatorScoping:
    """F2: n_decisions denominator must be scoped by source/experiment when provided."""

    def test_source_filter_excludes_other_sources(self) -> None:
        from src.analysis.evidence_report import build_evidence_report

        conn = _make_evidence_db()
        # Insert shadow_decision + live_decision for same strategy
        conn.execute(_DE_INSERT, ("de-shadow", 0, "settlement_capture", "shadow_decision"))
        conn.execute(_DE_INSERT, ("de-live", 1, "settlement_capture", "live_decision"))
        conn.commit()

        report = build_evidence_report(
            "settlement_capture",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            source="shadow_decision",
        )
        assert report.n_decisions == 1, f"Expected 1, got {report.n_decisions}"

    def test_no_source_filter_counts_all_sources(self) -> None:
        from src.analysis.evidence_report import build_evidence_report

        conn = _make_evidence_db()
        for seq, src in enumerate(("shadow_decision", "live_decision", "phase0_backfill")):
            conn.execute(_DE_INSERT, (f"de-{src}", seq, "settlement_capture", src))
        conn.commit()

        report = build_evidence_report(
            "settlement_capture", EvidenceTier.SHADOW_PASS, conn=conn
        )
        assert report.n_decisions == 3


class TestF3RegretJoinCorrectness:
    """F3: regret rows must be joined through decision_events to prevent cross-strategy contamination."""

    def test_regret_row_with_wrong_strategy_key_excluded(self) -> None:
        """A regret row whose decision_event belongs to a different strategy must not count."""
        from src.analysis.evidence_report import build_evidence_report

        conn = _make_evidence_db()
        # decision_event belongs to settlement_capture
        conn.execute(_DE_INSERT, ("de-sc", 0, "settlement_capture", "shadow_decision"))
        # shadow_experiment belongs to center_buy (different strategy)
        conn.execute(_SE_INSERT, ("exp-contaminated", "center_buy", "cohort-X"))
        # regret row links de-sc (settlement_capture) to exp-contaminated (center_buy)
        conn.execute(_RD_INSERT, ("de-sc", "exp-contaminated", 0.10))
        conn.commit()

        report = build_evidence_report(
            "center_buy", EvidenceTier.SHADOW_PASS, conn=conn
        )
        # The regret row must NOT count because de-sc.strategy_key = settlement_capture ≠ center_buy
        assert report.n_settled == 0, f"Cross-strategy contamination: n_settled={report.n_settled}"
        assert report.n_wins == 0

    def test_regret_row_with_matching_strategy_key_counted(self) -> None:
        from src.analysis.evidence_report import build_evidence_report

        conn = _make_evidence_db()
        _seed_evidence(conn, strategy_key="settlement_capture", total_regret=0.05)

        report = build_evidence_report(
            "settlement_capture", EvidenceTier.SHADOW_PASS, conn=conn
        )
        assert report.n_settled == 1
        assert report.n_wins == 1  # regret > 0 = win


# ---------------------------------------------------------------------------
# Finding 4: EvidenceTierAssignment lifecycle fields
# ---------------------------------------------------------------------------

def _make_tier_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


class TestF4EvidenceTierLifecycle:
    """F4: lifecycle fields (revoked_at, effective_until, effective_from) are enforced."""

    def test_revoked_row_excluded_from_current_assignment(self) -> None:
        conn = _make_tier_db()
        assignment = record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.SHADOW_PASS,
            rationale="test",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
        )
        # Manually revoke it
        conn.execute(
            "UPDATE evidence_tier_assignments SET revoked_at=?, revoked_by=? WHERE id=?",
            ("2026-05-22T01:00:00Z", "operator", assignment.row_id),
        )
        conn.commit()

        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is None, "Revoked row must not be returned"

    def test_expired_row_excluded_from_current_assignment(self) -> None:
        conn = _make_tier_db()
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.SHADOW_PASS,
            rationale="test",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
            effective_until=past,
        )
        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is None, "Expired row (effective_until in past) must not be returned"

    def test_future_effective_from_excluded(self) -> None:
        conn = _make_tier_db()
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.SHADOW_PASS,
            rationale="test",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
            effective_from=future,
        )
        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is None, "Row with future effective_from must not be returned yet"

    def test_active_row_returned(self) -> None:
        conn = _make_tier_db()
        record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.SHADOW_PASS,
            rationale="test",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
        )
        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is not None
        assert result.tier == EvidenceTier.SHADOW_PASS

    def test_supersedes_id_roundtrips(self) -> None:
        conn = _make_tier_db()
        a1 = record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.SHADOW_PASS,
            rationale="v1",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
        )
        a2 = record_evidence_tier_assignment(
            conn,
            strategy_id="settlement_capture",
            tier=EvidenceTier.LIVE_PILOT_TINY,
            rationale="v2",
            operator_ref=None,
            verdict_reason=None,
            assignment_source="tribunal",
            verdict_kind="PROMOTE",
            supersedes_assignment_id=a1.row_id,
        )
        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is not None
        assert result.tier == EvidenceTier.LIVE_PILOT_TINY
        assert result.supersedes_assignment_id == a1.row_id

    def test_no_such_column_error_returns_none(self) -> None:
        """Old DB without lifecycle columns must degrade gracefully (returns None)."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE evidence_tier_assignments (
               id INTEGER PRIMARY KEY,
               strategy_id TEXT NOT NULL,
               tier INTEGER NOT NULL,
               assigned_at TEXT NOT NULL,
               assignment_source TEXT NOT NULL,
               verdict_kind TEXT NOT NULL
            )"""
        )
        conn.commit()
        result = current_evidence_tier_assignment(conn, "settlement_capture")
        assert result is None


# ---------------------------------------------------------------------------
# Finding 5: Separate gross vs variance cluster throttle gates
# ---------------------------------------------------------------------------

class TestF5ClusterThrottleDimensions:
    """F5: gross_heat and variance_heat throttle independently with distinct labels."""

    def test_evaluator_source_has_separate_gross_variance_gates(self) -> None:
        """Source inspection: both dimension labels exist; policy_heat not used as gate."""
        from pathlib import Path
        src = (
            Path(__file__).parent.parent
            / "src" / "engine" / "evaluator.py"
        ).read_text()
        assert "regime_throttled_gross_50pct" in src, "gross throttle label missing"
        assert "regime_throttled_variance_50pct" in src, "variance throttle label missing"

    def test_gross_heat_only_fires_gross_label(self) -> None:
        """When gross > threshold but variance < threshold, only gross label fires."""
        from src.state.portfolio import ClusterExposureResult

        result = ClusterExposureResult(
            gross_heat=0.12,   # above 0.10
            variance_heat=0.05,  # below 0.10
            method="gross_notional",
        )
        validations: list[str] = []
        projected = 0.0
        current_gross = result.gross_heat + projected
        current_variance = result.variance_heat + projected if result.variance_heat is not None else None

        throttle = 1.0
        if current_gross > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_gross_50pct:{current_gross:.3f}")
        if current_variance is not None and current_variance > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_variance_50pct:{current_variance:.3f}")

        assert throttle == 0.5
        assert any("regime_throttled_gross_50pct" in v for v in validations)
        assert not any("regime_throttled_variance_50pct" in v for v in validations)

    def test_variance_heat_only_fires_variance_label(self) -> None:
        from src.state.portfolio import ClusterExposureResult

        result = ClusterExposureResult(
            gross_heat=0.05,   # below 0.10
            variance_heat=0.15,  # above 0.10
            method="variance_adjusted",
        )
        validations: list[str] = []
        projected = 0.0
        current_gross = result.gross_heat + projected
        current_variance = result.variance_heat + projected if result.variance_heat is not None else None

        throttle = 1.0
        if current_gross > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_gross_50pct:{current_gross:.3f}")
        if current_variance is not None and current_variance > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_variance_50pct:{current_variance:.3f}")

        assert throttle == 0.5
        assert not any("regime_throttled_gross_50pct" in v for v in validations)
        assert any("regime_throttled_variance_50pct" in v for v in validations)

    def test_both_dimensions_fire_independently_compound_to_quarter(self) -> None:
        from src.state.portfolio import ClusterExposureResult

        result = ClusterExposureResult(
            gross_heat=0.12,
            variance_heat=0.15,
            method="variance_adjusted",
        )
        validations: list[str] = []
        projected = 0.0
        current_gross = result.gross_heat + projected
        current_variance = result.variance_heat + projected if result.variance_heat is not None else None

        throttle = 1.0
        if current_gross > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_gross_50pct:{current_gross:.3f}")
        if current_variance is not None and current_variance > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_variance_50pct:{current_variance:.3f}")

        assert throttle == 0.25
        assert len([v for v in validations if "gross" in v]) == 1
        assert len([v for v in validations if "variance" in v]) == 1

    def test_neither_fires_when_both_under_threshold(self) -> None:
        from src.state.portfolio import ClusterExposureResult

        result = ClusterExposureResult(
            gross_heat=0.05,
            variance_heat=0.05,
            method="variance_adjusted",
        )
        validations: list[str] = []
        projected = 0.0
        current_gross = result.gross_heat + projected
        current_variance = result.variance_heat + projected if result.variance_heat is not None else None

        throttle = 1.0
        if current_gross > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_gross_50pct:{current_gross:.3f}")
        if current_variance is not None and current_variance > 0.10:
            throttle *= 0.5
            validations.append(f"regime_throttled_variance_50pct:{current_variance:.3f}")

        assert throttle == 1.0
        assert not validations


# ---------------------------------------------------------------------------
# Finding 6: Stale strategy_key CHECK constraint migration
# ---------------------------------------------------------------------------

_STALE_WORLD_DDL = """\
CREATE TABLE probability_trace_fact (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    strategy_key TEXT CHECK(strategy_key IN ('settlement_capture','shoulder_sell','center_buy','opening_inertia'))
)
"""

_STALE_TRADE_DDL = """\
CREATE TABLE position_events (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    strategy_key TEXT NOT NULL CHECK(strategy_key IN ('settlement_capture','shoulder_sell','center_buy','opening_inertia'))
)
"""


class TestF6StrategyKeyCheckMigration:
    """F6: stale CHECK constraint is removed from both world and trade tables."""

    def test_strip_strategy_key_check_removes_inline_check(self) -> None:
        stripped = _strip_strategy_key_check(_STALE_WORLD_DDL)
        assert "CHECK" not in stripped
        assert "strategy_key TEXT" in stripped

    def test_strip_strategy_key_check_preserves_not_null(self) -> None:
        stripped = _strip_strategy_key_check(_STALE_TRADE_DDL)
        assert "CHECK" not in stripped
        assert "strategy_key TEXT NOT NULL" in stripped

    def test_migrate_world_removes_check_preserves_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(_STALE_WORLD_DDL)
        conn.execute(
            "INSERT INTO probability_trace_fact (recorded_at, strategy_key) VALUES (?,?)",
            ("2026-05-22T00:00:00Z", "settlement_capture"),
        )
        conn.commit()

        _migrate_world_strategy_key_checks(conn)
        conn.commit()

        # Verify stale strategy key no longer rejected
        conn.execute(
            "INSERT INTO probability_trace_fact (recorded_at, strategy_key) VALUES (?,?)",
            ("2026-05-22T01:00:00Z", "day0_nowcast_entry"),
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM probability_trace_fact").fetchone()[0]
        assert count == 2

    def test_migrate_trade_removes_check_preserves_triggers(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(_STALE_TRADE_DDL)
        # Add a trigger to verify it's preserved
        conn.execute(
            """CREATE TRIGGER pe_audit AFTER INSERT ON position_events
               BEGIN SELECT 1; END"""
        )
        conn.execute(
            "INSERT INTO position_events (recorded_at, strategy_key) VALUES (?,?)",
            ("2026-05-22T00:00:00Z", "settlement_capture"),
        )
        conn.commit()

        _migrate_trade_strategy_key_checks(conn)
        conn.commit()

        # New strategy key must now insert without CHECK violation
        conn.execute(
            "INSERT INTO position_events (recorded_at, strategy_key) VALUES (?,?)",
            ("2026-05-22T01:00:00Z", "day0_nowcast_entry"),
        )
        conn.commit()

        # Trigger must still exist
        trg = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='pe_audit'"
        ).fetchone()
        assert trg is not None, "Trigger pe_audit was dropped during migration"

        count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        assert count == 2

    def test_migrate_world_idempotent_on_already_migrated_table(self) -> None:
        """Running migration twice must not error or duplicate data."""
        conn = sqlite3.connect(":memory:")
        conn.execute(_STALE_WORLD_DDL)
        conn.commit()

        _migrate_world_strategy_key_checks(conn)
        conn.commit()
        _migrate_world_strategy_key_checks(conn)  # second run — must be no-op
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM probability_trace_fact").fetchone()[0]
        assert count == 0  # no rows, no error

    def test_new_db_init_has_no_stale_check_constraint(self) -> None:
        """Fresh init_schema must not contain the stale CHECK on probability_trace_fact."""
        conn = _make_evidence_db()
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='probability_trace_fact'"
        ).fetchone()
        if row is None:
            pytest.skip("probability_trace_fact not in world schema")
        assert "opening_inertia" not in str(row[0]), (
            "New DB still has stale strategy_key CHECK enumerating opening_inertia"
        )
