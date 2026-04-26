# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: T5.a of midstream remediation packet
# (docs/operations/task_2026-04-23_midstream_remediation/plan.md);
# D3 typed-pipeline extension past evaluator into executor CLOB-send
# boundary. Guards that src/execution/executor.py:_live_order rejects
# malformed limit_price via ExecutionPrice.__post_init__ contract
# before any CLOB API call.

"""T5.a antibody — executor._live_order refuses malformed limit_price.

Defense-in-depth: the evaluator is the primary Kelly-safety seam
(INV-21, D3). `_live_order` now re-asserts the `ExecutionPrice`
contract at the executor boundary so a regression in the upstream
path (e.g. NaN limit_price, negative limit_price, or probability
value > 1.0) is caught before the CLOB API receives it. The
`ExecutionPrice(value=limit_price, price_type="fee_adjusted",
fee_deducted=True, currency="probability_units")` construction raises
`ValueError` from `__post_init__` on any of:
- non-finite value
- negative value
- value > 1.0 (because currency is probability_units)

`_live_order` catches these into an OrderResult with
status="rejected" and reason="malformed_limit_price: ..."
so the cycle surfaces the rejection instead of posting a bad order.

The tests construct a synthetic `ExecutionIntent` + call
`_live_order` directly without touching the live CLOB client (the
rejection path bails out before the client is even constructed).
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from src.contracts import Direction, ExecutionIntent
from src.execution.executor import OrderResult, _live_order


@pytest.fixture(autouse=True)
def _inject_mem_conn(monkeypatch):
    """Inject an in-memory DB into _live_order's get_connection fallback.

    P1.S3: _live_order persists a venue_commands row (INV-30) before
    submitting. Tests that call _live_order without an explicit conn
    now hit get_connection() as a fallback. Supply an in-memory DB with
    schema so unit tests remain self-contained.
    """
    from src.state.db import init_schema
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys=ON")
    init_schema(mem)
    monkeypatch.setattr("src.execution.executor.get_trade_connection_with_world", lambda: mem)
    yield mem
    mem.close()


def _make_intent(limit_price: float) -> ExecutionIntent:
    """Build a minimal ExecutionIntent with the limit_price under test.

    T5.a 2026-04-23 note: the initial draft of this fixture omitted
    ``decision_edge`` because the dataclass at that moment did not
    carry the field even though ``src/execution/executor.py:136,428``
    passed and read it. Surrogate critic flagged that omission — the
    accept-path tests would have passed for the wrong reason (boundary
    check green, then ``AttributeError`` on ``intent.decision_edge``
    at L428, swallowed by the broad except). T5.a now closes that
    latent bug in the same slice by adding
    ``decision_edge: float = 0.0`` to ``ExecutionIntent`` (see
    ``src/contracts/execution_intent.py``). The fixture passes an
    explicit value so the field is exercised end-to-end.
    """
    # P3-fix1 (post-review BLOCKER, 2026-04-26): max_slippage now requires
    # SlippageBps per ExecutionIntent.__post_init__ runtime guard.
    from src.contracts.slippage_bps import SlippageBps
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="test-market",
        token_id="test-token-12345",
        timeout_seconds=3600,
        decision_edge=0.05,
    )


def _run(limit_price: float) -> OrderResult:
    return _live_order(
        trade_id="test-trade-001",
        intent=_make_intent(limit_price),
        shares=10.0,
    )


class TestExecutionPriceBoundaryRejects:
    """Malformed limit_price must be rejected before CLOB contact."""

    def test_rejects_nan_limit_price(self) -> None:
        result = _run(math.nan)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")

    def test_rejects_positive_infinity(self) -> None:
        result = _run(math.inf)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")

    def test_rejects_negative_infinity(self) -> None:
        result = _run(-math.inf)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")

    def test_rejects_negative_price(self) -> None:
        result = _run(-0.25)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")

    def test_rejects_price_above_one_in_probability_units(self) -> None:
        # Probability units must be <= 1.0. Polymarket prices are in [0, 1].
        # A limit_price of 1.5 would mean "buy at 150% probability", nonsense.
        result = _run(1.5)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")

    def test_rejects_price_of_two(self) -> None:
        result = _run(2.0)
        assert result.status == "rejected"
        assert "malformed_limit_price" in (result.reason or "")


class TestExecutionPriceBoundaryAccepts:
    """Well-formed limit_price must flow past the boundary check.

    NOTE: these tests verify the boundary check does NOT reject valid
    prices. They do NOT exercise the full CLOB-post path (that would
    hit live API). Status may be "rejected" with a DIFFERENT reason
    (e.g. PolymarketClient construction failure in a bare test env)
    but crucially the reason must NOT contain
    "malformed_limit_price".
    """

    @pytest.mark.parametrize("price", [0.0, 0.01, 0.5, 0.75, 0.99, 1.0])
    def test_passes_boundary_in_range(self, price: float) -> None:
        result = _run(price)
        assert "malformed_limit_price" not in (result.reason or ""), (
            f"price={price} should pass the ExecutionPrice boundary check but "
            f"was rejected with: {result.reason!r}"
        )
