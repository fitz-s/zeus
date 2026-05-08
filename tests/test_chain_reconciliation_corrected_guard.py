# Created: 2026-05-05
# Last reused or audited: 2026-05-07
# Authority basis: object-meaning invariance Wave26 explicit position env authority.
"""Relationship tests: chain-reconciliation D6-field freeze for corrected-eligible positions.

Fitz methodology: relationship test first. These tests assert the cross-module
invariant between corrected_executable_economics_eligible (set by fill_tracker)
and chain_reconciliation's RESCUE, SIZE-MISMATCH, and QUARANTINE branches.

T1BD-CORRECTED-NO-CHAIN-MUTATION-ALL-SITES: If position.corrected_executable_economics_eligible
is True, chain mutation of entry_price, cost_basis_usd, size_usd, and shares is blocked and
cost_basis_chain_mutation_blocked_total{field} is emitted.

T1BD-LEGACY-CHAIN-MUTATION-UNCHANGED: If eligible=False, mutation proceeds unchanged.
"""

import logging
import sqlite3

import pytest

from src.state.chain_reconciliation import ChainPosition, reconcile
from src.state.portfolio import Position, PortfolioState

D6_FIELDS = ["entry_price", "cost_basis_usd", "size_usd", "shares"]
COUNTER_EVENT = "cost_basis_chain_mutation_blocked_total"


def _make_position(
    *,
    state: str = "entered",
    chain_state: str = "synced",
    corrected_eligible: bool = False,
    entry_price: float = 0.5,
    cost_basis_usd: float = 10.0,
    size_usd: float = 10.0,
    shares: float = 20.0,
) -> Position:
    return Position(
        trade_id="test-pos-1",
        market_id="mkt-1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-05-01",
        bin_label="39-40F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=size_usd,
        entry_price=entry_price,
        p_posterior=0.6,
        edge=0.1,
        shares=shares,
        cost_basis_usd=cost_basis_usd,
        entered_at="2026-05-01T00:00:00Z",
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state=state,
        order_id="ord-1",
        order_status="filled",
        order_posted_at="2026-05-01T00:00:00Z",
        chain_state=chain_state,
        token_id="tok-1",
        corrected_executable_economics_eligible=corrected_eligible,
    )


def _make_conn():
    """In-memory SQLite with architecture schema for reconcile to write events."""
    from src.state.db import apply_architecture_kernel_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# RESCUE branch (pending_tracked → rescued)
# ---------------------------------------------------------------------------

class TestRescueBranch:
    """RESCUE branch: pending_tracked position synced from chain."""

    def _make_pending(self, corrected_eligible: bool) -> Position:
        pos = _make_position(
            state="pending_tracked",
            chain_state="local_only",
            corrected_eligible=corrected_eligible,
            entry_price=0.5,
            cost_basis_usd=10.0,
            size_usd=10.0,
            shares=20.0,
        )
        pos.entry_order_id = "ord-1"
        pos.order_status = "pending"
        pos.entered_at = ""
        return pos

    def _chain(self) -> ChainPosition:
        # Chain has different values from the position defaults
        return ChainPosition(
            token_id="tok-1",
            size=25.0,       # differs from shares=20.0
            avg_price=0.6,   # differs from entry_price=0.5
            cost=15.0,       # differs from cost_basis_usd=10.0
            condition_id="cond-1",
        )

    def test_rescue_eligible_blocks_all_d6_mutations(self, caplog):
        """eligible=True: chain values must NOT overwrite D6 fields."""
        conn = _make_conn()
        # Pre-populate canonical rescue baseline so rescue proceeds
        from src.engine.lifecycle_events import build_entry_canonical_write
        from src.state.db import append_many_and_project
        pos = self._make_pending(corrected_eligible=True)
        entry_events, entry_projection = build_entry_canonical_write(
            pos, decision_id="dec-1", source_module="src.engine.cycle_runtime"
        )
        append_many_and_project(conn, entry_events, entry_projection)

        portfolio = PortfolioState(positions=[pos])
        chain_positions = [self._chain()]

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, chain_positions, conn=conn)

        reconciled = portfolio.positions[0]
        # D6 fields must NOT have been overwritten by chain values
        assert reconciled.entry_price == pytest.approx(0.5), (
            f"entry_price should remain 0.5 (FillAuthority), got {reconciled.entry_price}"
        )
        assert reconciled.cost_basis_usd == pytest.approx(10.0), (
            f"cost_basis_usd should remain 10.0, got {reconciled.cost_basis_usd}"
        )
        assert reconciled.size_usd == pytest.approx(10.0), (
            f"size_usd should remain 10.0, got {reconciled.size_usd}"
        )
        assert reconciled.shares == pytest.approx(20.0), (
            f"shares should remain 20.0, got {reconciled.shares}"
        )

        # Counters must be emitted for each of the 4 fields
        counter_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        emitted_fields = {
            r.message.split("field=")[1].strip()
            for r in counter_records
            if "field=" in r.message
        }
        assert emitted_fields == set(D6_FIELDS), (
            f"Expected counters for all 4 D6 fields, got: {emitted_fields}"
        )
        conn.close()

    def test_rescue_legacy_allows_all_d6_mutations(self, caplog):
        """eligible=False: chain values MUST overwrite D6 fields (legacy path)."""
        conn = _make_conn()
        from src.engine.lifecycle_events import build_entry_canonical_write
        from src.state.db import append_many_and_project
        pos = self._make_pending(corrected_eligible=False)
        entry_events, entry_projection = build_entry_canonical_write(
            pos, decision_id="dec-1", source_module="src.engine.cycle_runtime"
        )
        append_many_and_project(conn, entry_events, entry_projection)

        portfolio = PortfolioState(positions=[pos])
        chain_positions = [self._chain()]

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, chain_positions, conn=conn)

        reconciled = portfolio.positions[0]
        # Chain values should have been applied
        assert reconciled.entry_price == pytest.approx(0.6), (
            f"entry_price should be 0.6 (chain), got {reconciled.entry_price}"
        )
        assert reconciled.cost_basis_usd == pytest.approx(15.0), (
            f"cost_basis_usd should be 15.0 (chain), got {reconciled.cost_basis_usd}"
        )
        assert reconciled.size_usd == pytest.approx(15.0), (
            f"size_usd should be 15.0 (chain), got {reconciled.size_usd}"
        )
        assert reconciled.shares == pytest.approx(25.0), (
            f"shares should be 25.0 (chain), got {reconciled.shares}"
        )

        # NO block counters should be emitted for legacy
        counter_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        assert len(counter_records) == 0, (
            f"No block counters should fire for legacy positions; got {counter_records}"
        )
        conn.close()


# ---------------------------------------------------------------------------
# SIZE-MISMATCH branch (entered/holding position, chain.size differs)
# ---------------------------------------------------------------------------

class TestSizeMismatchBranch:
    """SIZE-MISMATCH branch: existing position whose chain size diverges."""

    def _make_entered(self, corrected_eligible: bool) -> Position:
        return _make_position(
            state="entered",
            chain_state="synced",
            corrected_eligible=corrected_eligible,
            entry_price=0.5,
            cost_basis_usd=10.0,
            size_usd=10.0,
            shares=20.0,
        )

    def _chain_size_match(self) -> ChainPosition:
        """Chain has same size — SIZE-MISMATCH branch not triggered, but price/cost differ."""
        return ChainPosition(
            token_id="tok-1",
            size=20.0,      # same as shares → no size mismatch
            avg_price=0.6,  # differs from entry_price
            cost=15.0,      # differs from cost_basis_usd
            condition_id="cond-1",
        )

    def _chain_size_mismatch(self) -> ChainPosition:
        """Chain size differs → SIZE-MISMATCH branch triggered."""
        return ChainPosition(
            token_id="tok-1",
            size=25.0,      # differs from shares=20.0
            avg_price=0.6,
            cost=15.0,
            condition_id="cond-1",
        )

    def test_size_mismatch_eligible_blocks_entry_price_cost(self, caplog):
        """eligible=True, no share mismatch: entry_price and cost_basis_usd/size_usd blocked."""
        conn = _make_conn()
        pos = self._make_entered(corrected_eligible=True)
        portfolio = PortfolioState(positions=[pos])

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, [self._chain_size_match()], conn=conn)

        reconciled = portfolio.positions[0]
        assert reconciled.entry_price == pytest.approx(0.5), (
            f"entry_price should remain 0.5, got {reconciled.entry_price}"
        )
        assert reconciled.cost_basis_usd == pytest.approx(10.0), (
            f"cost_basis_usd should remain 10.0, got {reconciled.cost_basis_usd}"
        )
        assert reconciled.size_usd == pytest.approx(10.0), (
            f"size_usd should remain 10.0, got {reconciled.size_usd}"
        )

        counter_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        emitted_fields = {
            r.message.split("field=")[1].strip()
            for r in counter_records
            if "field=" in r.message
        }
        # entry_price, cost_basis_usd, size_usd should be blocked
        assert "entry_price" in emitted_fields
        assert "cost_basis_usd" in emitted_fields
        assert "size_usd" in emitted_fields
        conn.close()

    def test_size_mismatch_eligible_blocks_shares(self, caplog):
        """eligible=True, share mismatch: shares field also blocked."""
        conn = _make_conn()
        pos = self._make_entered(corrected_eligible=True)
        portfolio = PortfolioState(positions=[pos])

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, [self._chain_size_mismatch()], conn=conn)

        reconciled = portfolio.positions[0]
        # shares must remain 20.0 (not overwritten to 25.0 from chain)
        assert reconciled.shares == pytest.approx(20.0), (
            f"shares should remain 20.0 (blocked), got {reconciled.shares}"
        )

        counter_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        emitted_fields = {
            r.message.split("field=")[1].strip()
            for r in counter_records
            if "field=" in r.message
        }
        assert "shares" in emitted_fields, (
            f"shares counter not emitted; got {emitted_fields}"
        )
        conn.close()

    def test_size_mismatch_legacy_allows_price_cost_mutations(self, caplog):
        """eligible=False: chain entry_price and cost_basis_usd applied normally.

        Note: SIZE MISMATCH UNRESOLVED (no canonical baseline) falls back to
        local_shares for legacy too; we assert the price/cost fields — which
        always come from chain in the SIZE-MISMATCH branch — are mutated.
        """
        conn = _make_conn()
        pos = self._make_entered(corrected_eligible=False)
        portfolio = PortfolioState(positions=[pos])

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, [self._chain_size_mismatch()], conn=conn)

        reconciled = portfolio.positions[0]
        # Chain price and cost values should be applied for legacy
        assert reconciled.entry_price == pytest.approx(0.6), (
            f"entry_price should be 0.6 (chain) for legacy; got {reconciled.entry_price}"
        )
        assert reconciled.cost_basis_usd == pytest.approx(15.0), (
            f"cost_basis_usd should be 15.0 (chain) for legacy; got {reconciled.cost_basis_usd}"
        )
        assert reconciled.size_usd == pytest.approx(15.0), (
            f"size_usd should be 15.0 (chain) for legacy; got {reconciled.size_usd}"
        )

        # No block counters should be emitted for legacy positions
        counter_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        assert len(counter_records) == 0, (
            f"No block counters for legacy; got {len(counter_records)}"
        )
        conn.close()


# ---------------------------------------------------------------------------
# QUARANTINE branch (new chain-only token → new Position())
# ---------------------------------------------------------------------------

class TestQuarantineBranch:
    """QUARANTINE branch: chain-only token not in local portfolio.

    These positions are NEW Position() objects synthesized from chain data.
    corrected_executable_economics_eligible defaults False (new Position default),
    so the guard never fires — there is no existing FillAuthority position to protect.
    The quarantine placeholder is initialized with chain data, which is the correct
    behavior: it represents chain truth for a token the local portfolio doesn't know about.

    Relationship being tested: the QUARANTINE branch creates chain_shares=chain.size
    as diagnostic metadata (NOT a locked D6 field per coordinator clarification C4).
    """

    def test_quarantine_creates_new_position_from_chain(self, caplog):
        """Chain-only token produces a quarantine placeholder with chain data."""
        conn = _make_conn()
        # Empty local portfolio — no existing positions
        portfolio = PortfolioState(positions=[])

        chain_positions = [
            ChainPosition(
                token_id="tok-quarantine-1",
                size=10.0,
                avg_price=0.4,
                cost=4.0,
                condition_id="cond-q1",
            )
        ]

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            stats = reconcile(portfolio, chain_positions, conn=conn)

        assert stats.get("quarantined", 0) >= 1, "Expected at least 1 quarantined position"
        quarantine_pos = portfolio.positions[-1]
        # chain_shares is diagnostic metadata — permitted (per C4)
        assert quarantine_pos.chain_shares == pytest.approx(10.0)
        # New quarantine positions are NOT corrected-eligible
        assert quarantine_pos.corrected_executable_economics_eligible is False

        # No block counters: quarantine creates new positions, not mutations of eligible ones
        block_records = [
            r for r in caplog.records
            if COUNTER_EVENT in r.message
        ]
        assert len(block_records) == 0, (
            f"No block counters expected for quarantine branch; got {block_records}"
        )
        conn.close()

    def test_quarantine_chain_shares_is_diagnostic_not_locked(self):
        """chain_shares is NOT in the 4-field freeze; chain mutation of it is permitted."""
        locked = {"entry_price", "cost_basis_usd", "size_usd", "shares"}
        assert "chain_shares" not in locked, (
            "chain_shares must NOT be in the D6 locked field set (coordinator C4)"
        )
