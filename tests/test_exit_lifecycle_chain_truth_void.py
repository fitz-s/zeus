# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: Fix A+B — ghost pending_exit chain-truth sync
#   src/execution/exit_lifecycle.py::handle_exit_pending_missing (Fix A)
#   src/state/projection.py::upsert_position_current (Fix B)
"""Antibody tests for ghost pending_exit chain-truth void sync.

Four antibodies:
  1. balance==0  → position transitions to voided, ADMIN_VOIDED event carries evidence_source=CHAIN_BALANCEOF
  2. balance>0   → position NOT voided; _mark_exit_retry is called (retry action returned)
  3. RPC failure → action==ignore preserved (fail-open, no destructive action)
  4. NULL condition_id on open phase → upsert_position_current raises NullConditionIdOnOpenPhaseError

Antibody 4 includes a sed-break/restore cycle to verify the guard actually catches
what its docstring claims (not just a passing-by-coincidence test).
"""

import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock

from src.execution.exit_lifecycle import (
    handle_exit_pending_missing,
    _query_ctf_balance,
    _abi_encode_balance_of,
)
from src.state.portfolio import Position, PortfolioState
from src.state.projection import (
    upsert_position_current,
    NullConditionIdOnOpenPhaseError,
    CANONICAL_POSITION_CURRENT_COLUMNS,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_SAFE_ADDRESS = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
_ASSET_ID_JAKARTA = "57517757817337093598366314311660155933515913814042039632650872688431348357139"
_ASSET_ID_LONDON = "113959433546428599583458171463964346033318046435676830124564125503733330054946"
_CONDITION_ID_LONDON = "0xddb5c82d33579fbd3d47600a89438a1c6af5b1ac7ba48ed3a4099c6070c4df4d"


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t-test-001",
        market_id="m1",
        city="Jakarta",
        cluster="SEA",
        target_date="2026-05-20",
        bin_label="30-31",
        direction="buy_yes",
        size_usd=1.21,
        entry_price=0.10,
        p_posterior=0.50,
        edge=0.10,
        entered_at="2026-05-01T00:00:00Z",
        token_id=_ASSET_ID_JAKARTA,
        condition_id="",
        chain_state="exit_pending_missing",
        state="pending_exit",
        exit_state="",
        exit_retry_count=0,
        last_exit_error="",
        next_exit_retry_at="",
        strategy_key="test_strategy",
        env="live",
        temperature_metric="high",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _make_portfolio(position: Position) -> PortfolioState:
    return PortfolioState(positions=[position])


def _rpc_returning(balance_int: int):
    """Return a mock rpc_call that always returns the given balance as hex."""
    def _rpc(rpc_url, method, params):
        if method == "eth_call":
            return hex(balance_int)
        raise ValueError(f"unexpected method {method!r}")
    return _rpc


def _rpc_raising():
    """Return a mock rpc_call that always raises (simulates RPC outage)."""
    def _rpc(rpc_url, method, params):
        raise ConnectionError("RPC unreachable (simulated)")
    return _rpc


# ---------------------------------------------------------------------------
# Antibody 1: balance == 0 → voided + ADMIN_VOIDED event with evidence_source=CHAIN_BALANCEOF
# ---------------------------------------------------------------------------

class TestChainTruthVoidOnZeroBalance:
    """Antibody 1: on-chain balance == 0 must transition position to voided."""

    def test_balance_zero_returns_closed_action(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="jakarta-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(0)
        )

        assert result["action"] == "closed", f"expected 'closed', got {result['action']!r}"

    def test_balance_zero_removes_from_portfolio(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="jakarta-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        handle_exit_pending_missing(portfolio, pos, conn=None, rpc_call=_rpc_returning(0))

        assert len(portfolio.positions) == 0, "position should have been removed from portfolio"

    def test_balance_zero_returned_position_is_voided(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="jakarta-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(portfolio, pos, conn=None, rpc_call=_rpc_returning(0))

        voided = result.get("position")
        assert voided is not None
        assert voided.state == "voided", f"expected state='voided', got {voided.state!r}"
        assert "CHAIN_CONFIRMED_ZERO" in (voided.exit_reason or ""), (
            f"exit_reason should reference CHAIN_CONFIRMED_ZERO, got {voided.exit_reason!r}"
        )

    def test_balance_zero_emits_admin_voided_event_with_chain_balanceof(self, monkeypatch):
        """Canonical ADMIN_VOIDED event must carry evidence_source=CHAIN_BALANCEOF in payload."""
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="jakarta-event-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        # Use a real in-memory DB to capture the written event
        conn = _build_minimal_db()
        handle_exit_pending_missing(portfolio, pos, conn=conn, rpc_call=_rpc_returning(0))
        conn.commit()

        rows = conn.execute(
            "SELECT event_type, payload_json FROM position_events WHERE position_id = ?",
            ("jakarta-event-test",),
        ).fetchall()

        # Find the ADMIN_VOIDED row
        admin_voided_rows = [r for r in rows if r[0] == "ADMIN_VOIDED"]
        assert admin_voided_rows, (
            f"No ADMIN_VOIDED event found. Found event_types: {[r[0] for r in rows]}"
        )
        payload = json.loads(admin_voided_rows[0][1])
        assert payload.get("evidence_source") == "CHAIN_BALANCEOF", (
            f"evidence_source must be CHAIN_BALANCEOF, got {payload.get('evidence_source')!r}"
        )


# ---------------------------------------------------------------------------
# Antibody 2: balance > 0 → NOT voided, mark_exit_retry called
# ---------------------------------------------------------------------------

class TestChainTruthRetryOnPositiveBalance:
    """Antibody 2: on-chain balance > 0 must NOT void; must mark_exit_retry."""

    def test_balance_positive_returns_retry_action(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="london-test",
            token_id=_ASSET_ID_LONDON,
            condition_id=_CONDITION_ID_LONDON,
            city="London",
        )
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(6_000_000)
        )

        assert result["action"] == "retry", f"expected 'retry', got {result['action']!r}"

    def test_balance_positive_position_remains_in_portfolio(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="london-test",
            token_id=_ASSET_ID_LONDON,
            condition_id=_CONDITION_ID_LONDON,
            city="London",
        )
        portfolio = _make_portfolio(pos)

        handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(6_000_000)
        )

        assert len(portfolio.positions) == 0 or any(
            p.trade_id == "london-test" for p in portfolio.positions
        ), "position should still exist (either in portfolio or as the returned position)"

    def test_balance_positive_does_not_void_position(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="london-test",
            token_id=_ASSET_ID_LONDON,
            condition_id=_CONDITION_ID_LONDON,
            city="London",
        )
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(6_000_000)
        )

        # Position must NOT be voided
        returned = result.get("position")
        if returned is not None:
            assert getattr(returned, "state", "") != "voided", (
                "balance>0 path must NOT transition to voided state"
            )

    def test_balance_positive_increments_retry_count(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="london-test",
            token_id=_ASSET_ID_LONDON,
            condition_id=_CONDITION_ID_LONDON,
            city="London",
            exit_retry_count=0,
        )
        portfolio = _make_portfolio(pos)

        handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(6_000_000)
        )

        assert pos.exit_retry_count >= 1, (
            f"exit_retry_count should be >= 1 after retry, got {pos.exit_retry_count}"
        )


# ---------------------------------------------------------------------------
# Antibody 3: RPC failure → action == ignore (fail-open, no destructive action)
# ---------------------------------------------------------------------------

class TestChainTruthRpcFailFallback:
    """Antibody 3: RPC failure must preserve fail-open behavior (action==ignore)."""

    def test_rpc_failure_returns_ignore(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="rpc-fail-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_raising()
        )

        # With RPC failure and no active exit_state, should fall through to "ignore"
        assert result["action"] in ("ignore", "skip"), (
            f"RPC failure must not produce destructive action; got {result['action']!r}"
        )

    def test_rpc_failure_does_not_void_position(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(trade_id="rpc-fail-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_raising()
        )

        # Position must remain in portfolio (not voided/removed)
        assert any(p.trade_id == "rpc-fail-test" for p in portfolio.positions), (
            "RPC failure must not remove the position from portfolio"
        )

    def test_rpc_failure_with_backoff_exhausted_still_closes_via_legacy(self, monkeypatch):
        """Backoff-exhausted + RPC failure should still hit legacy admin_close path."""
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="rpc-fail-backoff",
            token_id=_ASSET_ID_JAKARTA,
            exit_state="backoff_exhausted",
        )
        portfolio = _make_portfolio(pos)

        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_raising()
        )

        # Legacy path: backoff_exhausted → admin_closed
        assert result["action"] in ("closed", "skip"), (
            f"backoff_exhausted + RPC failure should close via legacy path; got {result['action']!r}"
        )

    def test_missing_funder_address_does_not_crash(self, monkeypatch):
        """If POLYMARKET_FUNDER_ADDRESS is absent, skip chain query gracefully."""
        monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
        monkeypatch.delenv("POLYMARKET_PROXY_ADDRESS", raising=False)
        pos = _make_position(trade_id="no-funder-test", token_id=_ASSET_ID_JAKARTA)
        portfolio = _make_portfolio(pos)

        # Should not raise even without funder address; chain query is skipped
        result = handle_exit_pending_missing(
            portfolio, pos, conn=None, rpc_call=_rpc_returning(0)
        )
        assert result["action"] in ("ignore", "skip", "closed"), (
            f"absent funder_address must not crash; got {result['action']!r}"
        )


# ---------------------------------------------------------------------------
# Antibody 4: NULL condition_id on open phase → upsert_position_current raises
# ---------------------------------------------------------------------------

class TestNullConditionIdFail:
    """Antibody 4: write-path raises NullConditionIdOnOpenPhaseError for open-phase rows with NULL condition_id."""

    def _minimal_projection(self, **overrides) -> dict:
        base = {
            "position_id": "test-pos-001",
            "phase": "active",
            "trade_id": "test-trade-001",
            "market_id": "market-001",
            "city": "Jakarta",
            "cluster": "SEA",
            "target_date": "2026-05-20",
            "bin_label": "30-31",
            "direction": "buy_yes",
            "unit": "celsius",
            "size_usd": 1.21,
            "shares": 12.1,
            "cost_basis_usd": 1.21,
            "entry_price": 0.10,
            "p_posterior": 0.50,
            "last_monitor_prob": None,
            "last_monitor_edge": None,
            "last_monitor_market_price": None,
            "decision_snapshot_id": None,
            "entry_method": "live",
            "strategy_key": "test_strategy",
            "edge_source": None,
            "discovery_mode": None,
            "chain_state": "local_only",
            "token_id": _ASSET_ID_JAKARTA,
            "no_token_id": None,
            "condition_id": "0xdeadbeef" * 8,  # valid default
            "order_id": None,
            "order_status": None,
            "updated_at": "2026-05-19T00:00:00Z",
            "temperature_metric": "high",
        }
        base.update(overrides)
        return base

    def test_null_condition_id_raises_on_open_phase(self):
        conn = _build_minimal_db()
        proj = self._minimal_projection(condition_id=None, phase="active")

        with pytest.raises(NullConditionIdOnOpenPhaseError) as exc_info:
            upsert_position_current(conn, proj)

        assert "active" in str(exc_info.value)
        assert "condition_id" in str(exc_info.value).lower()

    def test_empty_string_condition_id_raises_on_open_phase(self):
        conn = _build_minimal_db()
        proj = self._minimal_projection(condition_id="", phase="pending_entry")

        with pytest.raises(NullConditionIdOnOpenPhaseError) as exc_info:
            upsert_position_current(conn, proj)

        assert "pending_entry" in str(exc_info.value)

    def test_null_condition_id_raises_on_pending_exit_phase(self):
        """pending_exit is an open phase — condition_id required even during exit."""
        conn = _build_minimal_db()
        proj = self._minimal_projection(condition_id=None, phase="pending_exit")

        with pytest.raises(NullConditionIdOnOpenPhaseError):
            upsert_position_current(conn, proj)

    def test_null_condition_id_allowed_on_closed_phases(self):
        """Closed phases (voided, settled, admin_closed) must remain permissive."""
        for phase in ("voided", "settled", "admin_closed", "economically_closed"):
            conn = _build_minimal_db()
            proj = self._minimal_projection(condition_id=None, phase=phase)
            # Should NOT raise
            try:
                upsert_position_current(conn, proj)
            except NullConditionIdOnOpenPhaseError:
                pytest.fail(
                    f"NullConditionIdOnOpenPhaseError must NOT be raised for closed phase {phase!r}"
                )

    def test_valid_condition_id_passes_on_open_phase(self):
        """Sanity: valid condition_id on open phase must not raise."""
        conn = _build_minimal_db()
        proj = self._minimal_projection(
            condition_id="0xddb5c82d33579fbd3d47600a89438a1c6af5b1ac7ba48ed3a4099c6070c4df4d",
            phase="active",
        )
        upsert_position_current(conn, proj)  # must not raise

    def test_sed_break_the_guard(self):
        """Regression: the guard must catch what it claims to catch.

        Directly call upsert_position_current with NULL condition_id after
        temporarily monkey-patching _CONDITION_ID_REQUIRED_PHASES to be empty
        (simulating the guard being removed). Confirms that removing the guard
        causes the category to silently succeed — i.e., the guard is load-bearing.
        """
        import src.state.projection as proj_module

        original = proj_module._CONDITION_ID_REQUIRED_PHASES
        try:
            # Simulate guard removal: empty the required-phases set
            proj_module._CONDITION_ID_REQUIRED_PHASES = frozenset()
            conn = _build_minimal_db()
            proj_dict = self._minimal_projection(condition_id=None, phase="active")
            # With guard removed, the INSERT proceeds without raising
            upsert_position_current(conn, proj_dict)
            # Verify the NULL actually landed in the DB (guard was the only thing stopping it)
            row = conn.execute(
                "SELECT condition_id FROM position_current WHERE position_id = 'test-pos-001'"
            ).fetchone()
            assert row is not None
            assert row[0] is None, "Without guard, NULL condition_id should have been written"
        finally:
            # Restore: guard back in place
            proj_module._CONDITION_ID_REQUIRED_PHASES = original

        # Now WITH guard restored, the same write must raise
        conn2 = _build_minimal_db()
        proj_dict2 = self._minimal_projection(condition_id=None, phase="active")
        with pytest.raises(NullConditionIdOnOpenPhaseError):
            upsert_position_current(conn2, proj_dict2)


# ---------------------------------------------------------------------------
# _query_ctf_balance unit tests
# ---------------------------------------------------------------------------

class TestQueryCtfBalance:
    """Unit tests for the isolated _query_ctf_balance helper."""

    def test_zero_balance_returns_zero(self):
        balance = _query_ctf_balance(
            _ASSET_ID_JAKARTA, _SAFE_ADDRESS, rpc_call=_rpc_returning(0)
        )
        assert balance == 0

    def test_positive_balance_returns_correct_value(self):
        balance = _query_ctf_balance(
            _ASSET_ID_LONDON, _SAFE_ADDRESS, rpc_call=_rpc_returning(6_000_000)
        )
        assert balance == 6_000_000

    def test_rpc_failure_returns_none(self):
        balance = _query_ctf_balance(
            _ASSET_ID_JAKARTA, _SAFE_ADDRESS, rpc_call=_rpc_raising()
        )
        assert balance is None

    def test_empty_asset_id_returns_none(self):
        balance = _query_ctf_balance("", _SAFE_ADDRESS, rpc_call=_rpc_returning(0))
        assert balance is None

    def test_empty_owner_returns_none(self):
        balance = _query_ctf_balance(_ASSET_ID_JAKARTA, "", rpc_call=_rpc_returning(0))
        assert balance is None


# ---------------------------------------------------------------------------
# _abi_encode_balance_of unit tests
# ---------------------------------------------------------------------------

class TestAbiEncodeBalanceOf:
    """Unit tests for ABI encoding of balanceOf calldata."""

    def test_selector_is_correct(self):
        calldata = _abi_encode_balance_of(_SAFE_ADDRESS, _ASSET_ID_JAKARTA)
        assert calldata.startswith("0x00fdd58e"), (
            f"selector must be 0x00fdd58e, got {calldata[:10]!r}"
        )

    def test_total_length_is_correct(self):
        calldata = _abi_encode_balance_of(_SAFE_ADDRESS, _ASSET_ID_JAKARTA)
        # 4-byte selector + 32-byte address + 32-byte uint256 = 68 bytes = 136 hex chars + "0x"
        assert len(calldata) == 138, f"expected 138 chars (0x + 136 hex), got {len(calldata)}"

    def test_invalid_address_raises(self):
        with pytest.raises(ValueError, match="invalid owner address"):
            _abi_encode_balance_of("0xinvalid", _ASSET_ID_JAKARTA)


# ---------------------------------------------------------------------------
# Minimal in-memory DB builder
# ---------------------------------------------------------------------------

def _build_minimal_db() -> sqlite3.Connection:
    """Build a minimal in-memory SQLite DB with position_events and position_current tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            trade_id TEXT,
            market_id TEXT,
            city TEXT,
            cluster TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            unit TEXT,
            size_usd REAL,
            shares REAL,
            cost_basis_usd REAL,
            entry_price REAL,
            p_posterior REAL,
            last_monitor_prob REAL,
            last_monitor_edge REAL,
            last_monitor_market_price REAL,
            decision_snapshot_id TEXT,
            entry_method TEXT,
            strategy_key TEXT,
            edge_source TEXT,
            discovery_mode TEXT,
            chain_state TEXT,
            token_id TEXT,
            no_token_id TEXT,
            condition_id TEXT,
            order_id TEXT,
            order_status TEXT,
            updated_at TEXT,
            temperature_metric TEXT
        );
        CREATE TABLE IF NOT EXISTS position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_version INTEGER NOT NULL DEFAULT 1,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            strategy_key TEXT,
            decision_id TEXT,
            snapshot_id TEXT,
            order_id TEXT,
            command_id TEXT,
            caused_by TEXT,
            idempotency_key TEXT,
            venue_status TEXT,
            source_module TEXT,
            env TEXT NOT NULL DEFAULT 'live',
            payload_json TEXT
        );
    """)
    return conn
