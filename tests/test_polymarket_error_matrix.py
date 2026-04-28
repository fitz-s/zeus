"""CLOB error-handling matrix for executor and exit-lifecycle paths.

Verifies that 429, 5xx, and timeout errors from PolymarketClient are handled
gracefully at every exit/entry boundary: executor returns a rejected OrderResult,
and exit_lifecycle converts that into a retry (not a silent close).
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import httpx

from src.execution.executor import (
    create_exit_order_intent,
    execute_exit_order,
)
from src.execution.exit_lifecycle import execute_exit, build_exit_intent, MAX_EXIT_RETRIES
from src.state.portfolio import ExitContext
from src.state.portfolio import Position, PortfolioState


@pytest.fixture(autouse=True)
def _mem_conn(monkeypatch):
    """Inject an in-memory DB into executor fallback connection per test.

    Each test gets a fresh in-memory DB to avoid cross-test idempotency
    collisions. execute_exit_order calls get_trade_connection_with_world()
    when no explicit conn is passed.
    """
    from src.state.db import init_schema

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys=ON")
    init_schema(mem)
    monkeypatch.setattr("src.execution.executor.get_trade_connection_with_world", lambda: mem)
    monkeypatch.setattr("src.execution.executor._assert_cutover_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
    monkeypatch.setattr("src.execution.executor._assert_heartbeat_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._assert_ws_gap_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._assert_collateral_allows_sell", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.exit_lifecycle.check_sell_collateral", lambda *args, **kwargs: (True, ""))
    yield mem
    mem.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHttpResponse:
    """Duck-typed httpx.Response for constructing PolyApiException."""
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def _make_poly_exc(status_code: int, body: dict = None):
    from py_clob_client.exceptions import PolyApiException
    return PolyApiException(resp=_FakeHttpResponse(status_code, body or {}))


def _ensure_snapshot(conn, *, token_id: str) -> dict:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    snapshot_id = f"err-matrix-snap-{token_id}"
    if get_snapshot(conn, snapshot_id) is None:
        insert_snapshot(
            conn,
            ExecutableMarketSnapshotV2(
                snapshot_id=snapshot_id,
                gamma_market_id="gamma-error-matrix",
                event_id="event-error-matrix",
                event_slug="event-error-matrix",
                condition_id="condition-error-matrix",
                question_id="question-error-matrix",
                yes_token_id=token_id,
                no_token_id=f"{token_id}-no",
                selected_outcome_token_id=token_id,
                outcome_label="YES",
                enable_orderbook=True,
                active=True,
                closed=False,
                accepting_orders=True,
                market_start_at=None,
                market_end_at=None,
                market_close_at=None,
                sports_start_at=None,
                min_tick_size=Decimal("0.01"),
                min_order_size=Decimal("0.01"),
                fee_details={},
                token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
                rfqe=None,
                neg_risk=False,
                orderbook_top_bid=Decimal("0.39"),
                orderbook_top_ask=Decimal("0.41"),
                orderbook_depth_jsonb="{}",
                raw_gamma_payload_hash="a" * 64,
                raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64,
                authority_tier="CLOB",
                captured_at=now,
                freshness_deadline=now + timedelta(days=365),
            ),
        )
    return {
        "executable_snapshot_id": snapshot_id,
        "executable_snapshot_min_tick_size": Decimal("0.01"),
        "executable_snapshot_min_order_size": Decimal("0.01"),
        "executable_snapshot_neg_risk": False,
    }


def _make_exit_order_intent(conn, *, trade_id: str, token_id: str, shares: float, current_price: float, best_bid: float):
    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
        best_bid=best_bid,
        **_ensure_snapshot(conn, token_id=token_id),
    )


def _base_position(**kwargs):
    defaults = dict(
        trade_id="pos-err-1",
        market_id="m-err",
        city="Dallas",
        cluster="tx",
        target_date="2026-05-01",
        bin_label="70-75",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.45,
        p_posterior=0.50,
        edge=0.05,
        entry_ci_width=0.05,
        token_id="yes-tok-aaa",
        no_token_id="no-tok-bbb",
        entry_method="ens_member_counting",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _base_exit_context(**kwargs):
    defaults = dict(
        exit_reason="TEST_EXIT",
        current_market_price=0.40,
        best_bid=0.39,
    )
    defaults.update(kwargs)
    return ExitContext(**defaults)


# ---------------------------------------------------------------------------
# execute_exit_order: raw executor-level rejection tests
# ---------------------------------------------------------------------------

class TestExecuteExitOrderErrorMatrix:
    """Executor records post-submit errors as unknown side effects — no exception propagation."""

    def _run(self, exc, monkeypatch, _mem_conn):
        class _BrokenClient:
            def __init__(self):
                pass
            def place_limit_order(self, **kwargs):
                raise exc

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _BrokenClient)

        return execute_exit_order(_make_exit_order_intent(
            _mem_conn,
            trade_id="trade-err",
            token_id="yes-tok-1",
            shares=10.0,
            current_price=0.45,
            best_bid=0.44,
        ))

    def test_429_rate_limit_returns_rejected(self, monkeypatch, _mem_conn):
        """429 rate-limit → OrderResult.status='rejected', reason contains status code."""
        exc = _make_poly_exc(429, {"error": "rate limited"})
        result = self._run(exc, monkeypatch, _mem_conn)
        assert result.status == "unknown_side_effect"
        assert "429" in result.reason

    def test_500_server_error_returns_rejected(self, monkeypatch, _mem_conn):
        """5xx server error → OrderResult.status='rejected'."""
        exc = _make_poly_exc(500, {"error": "internal server error"})
        result = self._run(exc, monkeypatch, _mem_conn)
        assert result.status == "unknown_side_effect"
        assert "500" in result.reason

    def test_503_unavailable_returns_rejected(self, monkeypatch, _mem_conn):
        """503 unavailable → OrderResult.status='rejected'."""
        exc = _make_poly_exc(503, {"error": "service unavailable"})
        result = self._run(exc, monkeypatch, _mem_conn)
        assert result.status == "unknown_side_effect"
        assert "503" in result.reason

    def test_timeout_returns_rejected(self, monkeypatch, _mem_conn):
        """httpx timeout → OrderResult.status='rejected', reason captures the error."""
        exc = httpx.TimeoutException("connect timeout")

        class _TimeoutClient:
            def __init__(self): pass
            def place_limit_order(self, **kwargs): raise exc

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _TimeoutClient)

        result = execute_exit_order(_make_exit_order_intent(
            _mem_conn,
            trade_id="trade-timeout",
            token_id="yes-tok-2",
            shares=5.0,
            current_price=0.50,
            best_bid=0.49,
        ))
        assert result.status == "unknown_side_effect"
        assert result.reason  # non-empty

    def test_network_error_returns_rejected(self, monkeypatch, _mem_conn):
        """Generic network error → rejected, no exception escapes executor boundary."""
        class _BrokenClient:
            def __init__(self): pass
            def place_limit_order(self, **kwargs): raise ConnectionError("network unreachable")

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _BrokenClient)

        result = execute_exit_order(_make_exit_order_intent(
            _mem_conn,
            trade_id="trade-net",
            token_id="yes-tok-3",
            shares=8.0,
            current_price=0.42,
            best_bid=0.41,
        ))
        assert result.status == "unknown_side_effect"
        assert result.reason


# ---------------------------------------------------------------------------
# execute_exit: full lifecycle path — rejected sell → retry_pending
# ---------------------------------------------------------------------------

class _MockClob:
    """Minimal CLOB stub for lifecycle tests: passes collateral check."""
    def get_balance(self):
        return 1000.0  # always sufficient

    def get_order_status(self, order_id):
        return None  # no fill yet


class TestExecuteExitLifecycleOnError:
    """When a sell order is rejected (any CLOB error), execute_exit puts the
    position into retry_pending, never into economically_closed.
    """

    def _run_with_sell_error(self, exc, monkeypatch):
        pos = _base_position()
        portfolio = PortfolioState(bankroll=500.0, positions=[pos])
        ctx = _base_exit_context()

        monkeypatch.setattr(
            "src.execution.exit_lifecycle.place_sell_order",
            lambda *a, **kw: (_ for _ in ()).throw(exc),
        )

        outcome = execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ctx,
            clob=_MockClob(),
            conn=None,
        )
        return pos, outcome

    def test_429_leaves_position_in_retry_pending(self, monkeypatch):
        exc = _make_poly_exc(429)
        pos, outcome = self._run_with_sell_error(exc, monkeypatch)
        assert pos.state != "economically_closed", "429 must not close position"
        assert any(k in outcome for k in ("sell_error", "sell_exception", "retry", "stranded"))

    def test_500_leaves_position_in_retry_pending(self, monkeypatch):
        exc = _make_poly_exc(500)
        pos, outcome = self._run_with_sell_error(exc, monkeypatch)
        assert pos.state != "economically_closed", "5xx must not close position"
        assert any(k in outcome for k in ("sell_error", "sell_exception", "retry", "stranded"))

    def test_timeout_leaves_position_not_closed(self, monkeypatch):
        exc = httpx.TimeoutException("read timeout")
        pos, outcome = self._run_with_sell_error(exc, monkeypatch)
        assert pos.state != "economically_closed", "timeout must not close position"
        assert any(k in outcome for k in ("sell_error", "sell_exception", "retry", "stranded"))


# ---------------------------------------------------------------------------
# Backoff exhaustion: MAX_EXIT_RETRIES reached → backoff_exhausted state
# ---------------------------------------------------------------------------

class TestBackoffExhaustion:
    """After MAX_EXIT_RETRIES consecutive rejections, position reaches
    backoff_exhausted (hold to settlement, stop retrying).
    """

    def test_max_retries_produces_backoff_exhausted(self, monkeypatch):
        pos = _base_position()
        portfolio = PortfolioState(bankroll=500.0, positions=[pos])

        exc = _make_poly_exc(429)
        monkeypatch.setattr(
            "src.execution.exit_lifecycle.place_sell_order",
            lambda *a, **kw: (_ for _ in ()).throw(exc),
        )

        clob = _MockClob()
        for _ in range(MAX_EXIT_RETRIES + 1):
            ctx = _base_exit_context()
            execute_exit(portfolio=portfolio, position=pos, exit_context=ctx, clob=clob, conn=None)
            if pos.exit_state == "backoff_exhausted":
                break

        assert pos.exit_state == "backoff_exhausted", (
            f"Expected backoff_exhausted after {MAX_EXIT_RETRIES} retries, got {pos.exit_state!r}"
        )
        assert pos.state != "economically_closed"
        assert pos.exit_retry_count >= MAX_EXIT_RETRIES
