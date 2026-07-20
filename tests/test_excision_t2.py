# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md T2 (family-scoped
#   entry blocking + worst-case exposure replaces the portfolio-wide quarantine gate)
#   + "Consult adjudication" BLOCKER-1.
# Lifecycle: created=2026-07-11; last_reviewed=2026-07-11; last_reused=never
# Purpose: Unit + integration coverage for T2's six pieces: canonical asset dedup
#   reducer, ChainOnlyFact exposure accounting, family-scoped candidate block,
#   single admission fold, EntryRiskReservation, and the adjudication's binding
#   acceptance sequence.
# Reuse: Run when canonical_asset_exposure.py / evaluator family-block seam /
#   cycle_runner risk-exposure fold / executor EntryRiskReservation change.

"""T2 quarantine excision: family-scoped entry blocking + worst-case exposure."""

from __future__ import annotations

import sqlite3

import pytest

from src.contracts.entry_exposure_obligation import EntryExposureObligation
from src.contracts.review_work_item import FamilyKey, ReviewReasonCode
from src.state.canonical_asset_exposure import chain_only_worst_case_add_usd
from src.state.entry_exposure_obligation import (
    has_unbounded_obligation,
    open_entry_exposure_obligation,
    resolve_entry_exposure_obligation,
    total_open_obligation_usd,
)
from src.state.portfolio import PortfolioState, Position
from src.state.review_work_items import blocked_family_keys, open_work_item
from src.strategy.family_exclusive_dedup import WeatherFamilyKey


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    from src.state.schema.entry_exposure_obligations_schema import ensure_table as _ensure_obligations
    from src.state.schema.review_work_items_schema import ensure_table as _ensure_review_items

    _ensure_obligations(conn)
    _ensure_review_items(conn)
    return conn


def _add_market_event(conn: sqlite3.Connection, **kwargs) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
        "VALUES (:market_slug, :city, :target_date, :temperature_metric, :condition_id, :token_id)",
        kwargs,
    )


class _FakeChainOnlyFact:
    def __init__(self, *, token_id="", condition_id="", size=1.0, blocks_entry=True):
        self.token_id = token_id
        self.condition_id = condition_id
        self.size = size
        self.blocks_entry = blocks_entry


def _position(**overrides) -> Position:
    defaults = dict(
        trade_id="pos-1",
        market_id="m1",
        city="NYC",
        cluster="NYC",
        target_date="2026-08-01",
        bin_label="70-71",
        direction="buy_yes",
        state="active",
        token_id="tok-open-1",
        no_token_id="",
        shares=10.0,
        cost_basis_usd=4.0,
    )
    defaults.update(overrides)
    return Position(**defaults)


# ---------------------------------------------------------------------------
# T2 item 1+2: canonical asset dedup reducer + exposure accounting
# ---------------------------------------------------------------------------


class TestCanonicalAssetDedupReducer:
    def test_deduped_against_open_position_same_token(self) -> None:
        """A token represented by BOTH an open Position and a ChainOnlyFact is
        ONE exposure — the ChainOnlyFact side must not be added on top.
        """
        pos = _position(token_id="tok-shared")
        fact = _FakeChainOnlyFact(token_id="tok-shared", size=99.0)
        portfolio = PortfolioState(positions=[pos], chain_only_facts=[fact])
        add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
        assert add_usd == 0.0

    def test_not_deduped_when_token_differs(self) -> None:
        pos = _position(token_id="tok-a")
        fact = _FakeChainOnlyFact(token_id="tok-b", size=7.0)
        portfolio = PortfolioState(positions=[pos], chain_only_facts=[fact])
        add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
        assert add_usd == 7.0

    def test_fact_with_no_token_id_never_dropped(self) -> None:
        """A fact with no identity to dedup against is always counted —
        fail-safe, never silently skipped."""
        fact = _FakeChainOnlyFact(token_id="", size=3.0)
        portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
        add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
        assert add_usd == 3.0

    def test_unmapped_family_flagged_never_silently_dropped(self) -> None:
        """Census finding this reducer must NOT repeat: a fact with real size
        and no resolvable family must still be counted AND flagged degraded.
        """
        conn = _make_conn()
        fact = _FakeChainOnlyFact(token_id="tok-unmapped", condition_id="cond-unmapped", size=5.0)
        portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
        add_usd, any_unmapped = chain_only_worst_case_add_usd(conn, portfolio)
        assert add_usd == 5.0
        assert any_unmapped is True

    def test_mapped_family_not_flagged(self) -> None:
        conn = _make_conn()
        _add_market_event(
            conn,
            market_slug="slug1",
            city="Boston",
            target_date="2026-09-01",
            temperature_metric="high",
            condition_id="cond-mapped",
            token_id="tok-mapped",
        )
        fact = _FakeChainOnlyFact(token_id="tok-mapped", condition_id="cond-mapped", size=5.0)
        portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
        add_usd, any_unmapped = chain_only_worst_case_add_usd(conn, portfolio)
        assert add_usd == 5.0
        assert any_unmapped is False


# ---------------------------------------------------------------------------
# T2 item 3: family-scoped candidate block (evaluator seam)
# ---------------------------------------------------------------------------


class TestEvaluatorFamilyScopedBlock:
    def test_blocked_family_keys_rejects_sibling_bin_same_family(self) -> None:
        """One quarantine fact + N healthy candidates -> N-1 markets still
        pass the gate (T2 verification bar, docs/rebuild/quarantine_excision_
        2026-07-11.md). A ReviewWorkItem opened for one family blocks a
        SIBLING temperature bin in the SAME family (different market_family_id
        does not narrow the match)."""
        conn = _make_conn()
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            exposure_bound_usd=10.0,
            family_key=FamilyKey(city="NYC", target_date="2026-08-01", temperature_metric="high"),
        )
        blocked = blocked_family_keys(conn, portfolio=None)
        # Sibling bin candidate: same (city, target_date, metric), different
        # market_family_id — market_family_id never narrows the match.
        candidate_identity = ("NYC", "2026-08-01", "high")
        assert any(
            (k.city, k.target_date, k.temperature_metric) == candidate_identity for k in blocked
        )

    def test_other_family_not_blocked(self) -> None:
        conn = _make_conn()
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            exposure_bound_usd=10.0,
            family_key=FamilyKey(city="NYC", target_date="2026-08-01", temperature_metric="high"),
        )
        blocked = blocked_family_keys(conn, portfolio=None)
        other_family_identity = ("Chicago", "2026-08-01", "high")
        assert not any(
            (k.city, k.target_date, k.temperature_metric) == other_family_identity for k in blocked
        )


# ---------------------------------------------------------------------------
# T2 item 5: EntryRiskReservation (executor pre-submit seam)
# ---------------------------------------------------------------------------


class TestEntryRiskReservation:
    def _make_intent(self, **overrides):
        from src.contracts import Direction, ExecutionIntent
        from src.contracts.slippage_bps import SlippageBps

        defaults = dict(
            direction=Direction.YES,
            target_size_usd=10.0,
            limit_price=0.4,
            toxicity_budget=0.05,
            max_slippage=SlippageBps(value_bps=200, direction="adverse"),
            is_sandbox=False,
            market_id="cond-reservation",
            token_id="tok-reservation",
            timeout_seconds=3600,
        )
        defaults.update(overrides)
        return ExecutionIntent(**defaults)

    def test_open_persists_bounded_obligation_shares_times_one_dollar(self) -> None:
        from src.execution.executor import _open_entry_risk_reservation

        conn = _make_conn()
        intent = self._make_intent()
        _open_entry_risk_reservation(
            conn, command_id="cmd-1", intent=intent, shares=25.0, cost_basis_usd=10.0,
            family_key=("Denver", "2026-08-10", "high"),
        )
        assert total_open_obligation_usd(conn) == pytest.approx(25.0)
        assert has_unbounded_obligation(conn) is False

    def test_open_persists_exact_snapshot_family_without_market_event_lookup(self) -> None:
        from src.execution.executor import _open_entry_risk_reservation

        conn = _make_conn()
        intent = self._make_intent(market_id="cond-reservation-2", token_id="tok-reservation-2")
        _open_entry_risk_reservation(
            conn, command_id="cmd-2", intent=intent, shares=5.0, cost_basis_usd=2.0,
            family_key=("Denver", "2026-08-10", "high"),
        )
        blocked = blocked_family_keys(conn, portfolio=None)
        assert WeatherFamilyKey("Denver", "2026-08-10", "high", "") in blocked

    def test_release_resolves_open_obligation(self) -> None:
        from src.execution.executor import (
            _open_entry_risk_reservation,
            _release_entry_risk_reservation,
        )

        conn = _make_conn()
        intent = self._make_intent()
        _open_entry_risk_reservation(
            conn, command_id="cmd-3", intent=intent, shares=25.0, cost_basis_usd=10.0,
            family_key=("Denver", "2026-08-10", "high"),
        )
        assert total_open_obligation_usd(conn) == pytest.approx(25.0)
        released = _release_entry_risk_reservation(conn, command_id="cmd-3")
        assert released is True
        assert total_open_obligation_usd(conn) == pytest.approx(0.0)

    def test_release_on_nonexistent_command_returns_false_never_raises(self) -> None:
        from src.execution.executor import _release_entry_risk_reservation

        conn = _make_conn()
        assert _release_entry_risk_reservation(conn, command_id="never-opened") is False


# ---------------------------------------------------------------------------
# T2 item 4a: unbounded obligation -> DATA_DEGRADED (RiskGuard fold)
# ---------------------------------------------------------------------------


class TestUnboundedObligationDataDegraded:
    def test_riskguard_fold_returns_data_degraded_when_unbounded_open(self) -> None:
        from src.riskguard.riskguard import _unresolved_exposure_data_degraded_level
        from src.riskguard.risk_level import RiskLevel

        conn = _make_conn()
        open_entry_exposure_obligation(conn, command_id="cmd-unbounded", owner_domain="trade", unbounded=True)
        level = _unresolved_exposure_data_degraded_level(conn, portfolio=None)
        assert level == RiskLevel.DATA_DEGRADED

    def test_riskguard_fold_green_when_only_bounded_open(self) -> None:
        from src.riskguard.riskguard import _unresolved_exposure_data_degraded_level
        from src.riskguard.risk_level import RiskLevel

        conn = _make_conn()
        open_entry_exposure_obligation(
            conn, command_id="cmd-bounded", owner_domain="trade", shares=1.0, cost_basis_usd=0.5,
        )
        level = _unresolved_exposure_data_degraded_level(conn, portfolio=None)
        assert level == RiskLevel.GREEN

    def test_riskguard_fold_green_when_no_obligations(self) -> None:
        from src.riskguard.riskguard import _unresolved_exposure_data_degraded_level
        from src.riskguard.risk_level import RiskLevel

        conn = _make_conn()
        level = _unresolved_exposure_data_degraded_level(conn, portfolio=None)
        assert level == RiskLevel.GREEN


# ---------------------------------------------------------------------------
# T2 item 6: acceptance test (adjudication's binding sequence)
# ---------------------------------------------------------------------------


class TestAcceptanceSequence:
    """command admitted -> venue submit maybe-succeeded -> fill observation
    write FAILS before RiskGuard's fact source -> position stays pending ->
    obligation enters family+total risk view -> next candidate in same family
    rejected, candidate in OTHER family passes -> monitor/exit/reconcile lanes
    still run (simulated at the mechanism level: this packet's own machinery,
    not fill_tracker.py's T4 failure-classification wiring).
    """

    def test_full_acceptance_sequence(self) -> None:
        from src.execution.executor import _open_entry_risk_reservation
        from src.riskguard.riskguard import _unresolved_exposure_data_degraded_level
        from src.riskguard.risk_level import RiskLevel
        from src.contracts import Direction, ExecutionIntent
        from src.contracts.slippage_bps import SlippageBps

        conn = _make_conn()
        _add_market_event(
            conn,
            market_slug="slug-accept",
            city="Miami",
            target_date="2026-08-20",
            temperature_metric="high",
            condition_id="cond-accept-1",
            token_id="tok-accept-1",
        )

        # 1. Command admitted (simulates insert_command's transaction boundary):
        #    the EntryRiskReservation is opened atomically, BEFORE any network
        #    post outcome is known.
        intent = ExecutionIntent(
            direction=Direction.YES,
            target_size_usd=10.0,
            limit_price=0.4,
            toxicity_budget=0.05,
            max_slippage=SlippageBps(value_bps=200, direction="adverse"),
            is_sandbox=False,
            market_id="cond-accept-1",
            token_id="tok-accept-1",
            timeout_seconds=3600,
        )
        _open_entry_risk_reservation(
            conn, command_id="cmd-accept-1", intent=intent, shares=20.0, cost_basis_usd=8.0,
            family_key=("Miami", "2026-08-20", "high"),
        )

        # 2. Venue submit maybe-succeeded; 3. fill observation write FAILS
        #    before RiskGuard's fact source (never released — T4's job, out of
        #    this packet's scope) -> 4. position stays pending: simulated by
        #    NOT calling _release_entry_risk_reservation. The obligation stays
        #    OPEN, exactly modeling "fate unresolved".

        # 5. obligation enters family+total risk view:
        assert total_open_obligation_usd(conn) == pytest.approx(20.0)
        assert has_unbounded_obligation(conn) is False
        risk_level = _unresolved_exposure_data_degraded_level(conn, portfolio=None)
        # Bounded (not unbounded) -> this specific leg stays GREEN; the
        # *exposure* is nonetheless now counted (previous assert) rather than
        # invisible, closing BLOCKER-1's undercount hole.
        assert risk_level == RiskLevel.GREEN
        blocked = blocked_family_keys(conn, portfolio=None)
        assert WeatherFamilyKey("Miami", "2026-08-20", "high", "") in blocked

        # 6. next candidate in SAME family rejected, candidate in OTHER
        #    family passes:
        same_family_identity = ("Miami", "2026-08-20", "high")
        other_family_identity = ("Chicago", "2026-08-20", "high")
        assert any(
            (k.city, k.target_date, k.temperature_metric) == same_family_identity for k in blocked
        )
        assert not any(
            (k.city, k.target_date, k.temperature_metric) == other_family_identity for k in blocked
        )

        # 7. monitor/exit/reconcile lanes still run: nothing in this
        #    machinery touches risk_level beyond DATA_DEGRADED (never RED/
        #    ORANGE), and _risk_allows_new_entries is the ONLY consumer that
        #    gates on it for NEW entries — monitor/exit/RED-sweep lanes in
        #    cycle_runner.py are untouched by this packet (Tier 0 requirement;
        #    see _execute_force_exit_sweep / _execute_monitoring_phase, never
        #    referenced by this test's machinery).
        assert risk_level != RiskLevel.RED
        assert risk_level != RiskLevel.ORANGE
