"""CLOB error-handling matrix for executor and exit-lifecycle paths.

Verifies that 429, 5xx, and timeout errors from PolymarketClient are handled
gracefully at every exit/entry boundary: executor returns a rejected OrderResult,
and exit_lifecycle converts that into a retry (not a silent close).
"""

import pytest
import httpx

from src.execution.executor import (
    create_exit_order_intent,
    execute_exit_order,
)
from src.execution.exit_lifecycle import execute_exit, build_exit_intent, MAX_EXIT_RETRIES
from src.state.portfolio import ExitContext
from src.state.portfolio import Position, PortfolioState


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
    """Executor rejects gracefully on any CLOB error — no exception propagation."""

    def _run(self, exc, monkeypatch):
        class _BrokenClient:
            def __init__(self):
                pass
            def place_limit_order(self, **kwargs):
                raise exc

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _BrokenClient)

        return execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-err",
                token_id="yes-tok-1",
                shares=10.0,
                current_price=0.45,
                best_bid=0.44,
            )
        )

    def test_429_rate_limit_returns_rejected(self, monkeypatch):
        """429 rate-limit → OrderResult.status='rejected', reason contains status code."""
        exc = _make_poly_exc(429, {"error": "rate limited"})
        result = self._run(exc, monkeypatch)
        assert result.status == "rejected"
        assert "429" in result.reason

    def test_500_server_error_returns_rejected(self, monkeypatch):
        """5xx server error → OrderResult.status='rejected'."""
        exc = _make_poly_exc(500, {"error": "internal server error"})
        result = self._run(exc, monkeypatch)
        assert result.status == "rejected"
        assert "500" in result.reason

    def test_503_unavailable_returns_rejected(self, monkeypatch):
        """503 unavailable → OrderResult.status='rejected'."""
        exc = _make_poly_exc(503, {"error": "service unavailable"})
        result = self._run(exc, monkeypatch)
        assert result.status == "rejected"
        assert "503" in result.reason

    def test_timeout_returns_rejected(self, monkeypatch):
        """httpx timeout → OrderResult.status='rejected', reason captures the error."""
        exc = httpx.TimeoutException("connect timeout")

        class _TimeoutClient:
            def __init__(self): pass
            def place_limit_order(self, **kwargs): raise exc

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _TimeoutClient)

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-timeout",
                token_id="yes-tok-2",
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
            )
        )
        assert result.status == "rejected"
        assert result.reason  # non-empty

    def test_network_error_returns_rejected(self, monkeypatch):
        """Generic network error → rejected, no exception escapes executor boundary."""
        class _BrokenClient:
            def __init__(self): pass
            def place_limit_order(self, **kwargs): raise ConnectionError("network unreachable")

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _BrokenClient)

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-net",
                token_id="yes-tok-3",
                shares=8.0,
                current_price=0.42,
                best_bid=0.41,
            )
        )
        assert result.status == "rejected"
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
