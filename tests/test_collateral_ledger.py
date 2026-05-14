# Created: 2026-04-27
# Purpose: Lock R3 Z4 CollateralLedger pUSD/CTF reservation and fail-closed executor preflight behavior.
# Reuse: Run when collateral snapshots, pUSD/CTF accounting, wrap/unwrap command state, or executor collateral gates change.
# Last reused/audited: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
"""R3 Z4 collateral-ledger antibodies."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts import Direction
from src.contracts.fx_classification import (
    FXClassification,
    FXClassificationPending,
    require_fx_classification,
)
from src.contracts.slippage_bps import SlippageBps
from src.contracts.execution_intent import DecisionSourceContext, ExecutionIntent
from src.execution.wrap_unwrap_commands import (
    WrapUnwrapState,
    confirm_command,
    fail_command,
    get_command,
    init_wrap_unwrap_schema,
    mark_tx_hashed,
    request_unwrap,
    request_wrap,
)
from src.state.collateral_ledger import (
    CollateralInsufficient,
    CollateralLedger,
    CollateralSnapshot,
    init_collateral_schema,
    require_pusd_redemption_allowed,
)

YES_TOKEN = "yes-token-001"
_CTF_SCALE = 1_000_000


def _ctf_units(shares: float) -> int:
    return int(round(shares * _CTF_SCALE))


def _decision_source_context() -> DecisionSourceContext:
    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-04-26T00:00:00+00:00",
        forecast_valid_time="2026-04-26T06:00:00+00:00",
        forecast_fetch_time="2026-04-26T01:00:00+00:00",
        forecast_available_at="2026-04-26T00:30:00+00:00",
        raw_payload_hash="a" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time="2026-04-26T02:00:00+00:00",
        decision_time_status="OK",
    )


def _fake_submit_result(
    bound_envelope,
    *,
    order_id: str | None = None,
    status: str = "LIVE",
    success: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    raw_payload = {"status": status}
    if order_id is not None:
        raw_payload["orderID"] = order_id
    if success is not None:
        raw_payload["success"] = success
    changes = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
        "order_id": order_id,
    }
    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""
    final = bound_envelope.with_updates(**changes)
    result = {
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if order_id is not None:
        result["orderID"] = order_id
    if success is not None:
        result["success"] = success
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


class FakeCollateralAdapter:
    def __init__(self, payload=None, exc: Exception | None = None):
        self.payload = payload or {}
        self.exc = exc

    def get_collateral_payload(self):
        if self.exc:
            raise self.exc
        return self.payload


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_collateral_schema(db)
    init_wrap_unwrap_schema(db)
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _allow_non_collateral_execution_guards(monkeypatch):
    """This file isolates collateral behavior; other live gates are tested elsewhere."""

    monkeypatch.setattr(
        "src.execution.executor._assert_risk_allocator_allows_submit",
        lambda *args, **kwargs: {"component": "risk_allocator", "allowed": True, "reason": "unit_test"},
    )
    monkeypatch.setattr(
        "src.execution.executor._assert_risk_allocator_allows_exit_submit",
        lambda *args, **kwargs: {"component": "risk_allocator", "allowed": True, "reason": "unit_test"},
    )
    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
    monkeypatch.setattr(
        "src.execution.executor._assert_ws_gap_allows_submit",
        lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "unit_test"},
    )


def _buy_intent(
    size_usd: float = 10.0,
    token_id: str = YES_TOKEN,
    limit_price: float = 0.50,
    executable_snapshot_id: str = "",
    executable_snapshot_min_tick_size=None,
    executable_snapshot_min_order_size=None,
    executable_snapshot_neg_risk: bool | None = None,
) -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=size_usd,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="z4-market",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.10,
        executable_snapshot_id=executable_snapshot_id,
        executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
        executable_snapshot_min_order_size=executable_snapshot_min_order_size,
        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
        decision_source_context=_decision_source_context(),
    )


def _exec_snapshot_kwargs(
    conn,
    *,
    token_id: str = YES_TOKEN,
    min_tick_size: str = "0.01",
    min_order_size: str = "0.01",
) -> dict:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    captured_at = datetime(2026, 4, 27, tzinfo=timezone.utc)
    snapshot_id = f"snap-{token_id}-{min_tick_size}-{min_order_size}"
    if get_snapshot(conn, snapshot_id) is None:
        insert_snapshot(
            conn,
            ExecutableMarketSnapshotV2(
                snapshot_id=snapshot_id,
                gamma_market_id="gamma-test",
                event_id="event-test",
                event_slug="event-test",
                condition_id="condition-test",
                question_id="question-test",
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
                min_tick_size=Decimal(min_tick_size),
                min_order_size=Decimal(min_order_size),
                fee_details={
                    "source": "test",
                    "token_id": token_id,
                    "fee_rate_fraction": 0.0,
                    "fee_rate_bps": 0.0,
                    "fee_rate_source_field": "fee_rate_fraction",
                    "fee_rate_raw_unit": "fraction",
                },
                token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
                rfqe=None,
                neg_risk=False,
                orderbook_top_bid=Decimal("0.49"),
                orderbook_top_ask=Decimal("0.51"),
                orderbook_depth_jsonb="{}",
                raw_gamma_payload_hash="a" * 64,
                raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64,
                authority_tier="CLOB",
                captured_at=captured_at,
                freshness_deadline=captured_at + timedelta(days=365),
            ),
        )
    return {
        "executable_snapshot_id": snapshot_id,
        "executable_snapshot_min_tick_size": Decimal(min_tick_size),
        "executable_snapshot_min_order_size": Decimal(min_order_size),
        "executable_snapshot_neg_risk": False,
    }


def _snapshot(
    *,
    pusd: int = 100_000_000,
    pusd_allowance: int | None = None,
    usdc_e: int = 0,
    ctf: dict[str, int | float] | None = None,
    ctf_allowances: dict[str, int | float] | None = None,
    reserved_pusd: int = 0,
    reserved_tokens: dict[str, int | float] | None = None,
    authority: str = "CHAIN",
    captured_at: datetime | None = None,
) -> CollateralSnapshot:
    ctf_units = {token: _ctf_units(float(shares)) for token, shares in (ctf or {}).items()}
    allowance_source = ctf if ctf_allowances is None else ctf_allowances
    allowance_units = {token: _ctf_units(float(shares)) for token, shares in (allowance_source or {}).items()}
    reserved_token_units = {token: _ctf_units(float(shares)) for token, shares in (reserved_tokens or {}).items()}
    return CollateralSnapshot(
        pusd_balance_micro=pusd,
        pusd_allowance_micro=pusd if pusd_allowance is None else pusd_allowance,
        usdc_e_legacy_balance_micro=usdc_e,
        ctf_token_balances=ctf_units,
        ctf_token_allowances=allowance_units,
        reserved_pusd_for_buys_micro=reserved_pusd,
        reserved_tokens_for_sells=reserved_token_units,
        captured_at=captured_at or datetime.now(timezone.utc),
        authority_tier=authority,  # type: ignore[arg-type]
    )


def test_buy_preflight_blocks_when_pusd_insufficient(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=9_000_000))
    ledger.reserve_pusd_for_buy("cmd-existing", 1_000_000)

    with pytest.raises(CollateralInsufficient, match="pusd_insufficient"):
        ledger.buy_preflight(_buy_intent(size_usd=10.0))


def test_buy_preflight_blocks_when_pusd_allowance_insufficient(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, pusd_allowance=9_000_000))

    with pytest.raises(CollateralInsufficient, match="pusd_allowance_insufficient"):
        ledger.buy_preflight(_buy_intent(size_usd=10.0))


def test_buy_preflight_blocks_when_snapshot_stale(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(captured_at=datetime.now(timezone.utc) - timedelta(seconds=61)))

    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_stale"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_buy_preflight_blocks_when_snapshot_timestamp_is_future(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(captured_at=datetime.now(timezone.utc) + timedelta(seconds=61)))

    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_future"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_buy_preflight_blocks_when_persisted_snapshot_timestamp_is_malformed(conn):
    conn.execute(
        """
        INSERT INTO collateral_ledger_snapshots (
          pusd_balance_micro,
          pusd_allowance_micro,
          usdc_e_legacy_balance_micro,
          ctf_token_balances_json,
          ctf_token_allowances_json,
          reserved_pusd_for_buys_micro,
          reserved_tokens_for_sells_json,
          captured_at,
          authority_tier,
          raw_balance_payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            100_000_000,
            100_000_000,
            0,
            "{}",
            "{}",
            0,
            "{}",
            "not-a-timestamp",
            "CHAIN",
            None,
        ),
    )
    conn.commit()
    ledger = CollateralLedger(conn)

    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_stale"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_buy_preflight_nets_existing_reservations_from_pusd_allowance(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, pusd_allowance=10_000_000))
    ledger.reserve_pusd_for_buy("cmd-existing", 6_000_000)

    with pytest.raises(CollateralInsufficient, match="available_allowance_micro=4000000"):
        ledger.buy_preflight(_buy_intent(size_usd=5.0))


def test_sell_preflight_blocks_when_token_balance_insufficient(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 9}))

    with pytest.raises(CollateralInsufficient, match="ctf_tokens_insufficient"):
        ledger.sell_preflight(token_id=YES_TOKEN, size=10)


def test_sell_preflight_blocks_when_ctf_allowance_insufficient(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(ctf={YES_TOKEN: 10}, ctf_allowances={YES_TOKEN: 9}))

    with pytest.raises(CollateralInsufficient, match="ctf_allowance_insufficient"):
        ledger.sell_preflight(token_id=YES_TOKEN, size=10)


def test_sell_preflight_blocks_when_snapshot_stale(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        _snapshot(
            ctf={YES_TOKEN: 10},
            ctf_allowances={YES_TOKEN: 10},
            captured_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
    )

    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_stale"):
        ledger.sell_preflight(token_id=YES_TOKEN, size=1)


def test_sell_preflight_nets_existing_reservations_from_ctf_allowance(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(ctf={YES_TOKEN: 100}, ctf_allowances={YES_TOKEN: 10}))
    ledger.reserve_tokens_for_sell("cmd-existing", YES_TOKEN, 6)

    with pytest.raises(CollateralInsufficient, match="available_allowance=4000000"):
        ledger.sell_preflight(token_id=YES_TOKEN, size=5)


def test_fractional_ctf_inventory_cannot_be_rounded_up_to_cover_larger_sell(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(ctf={YES_TOKEN: 0.01}, ctf_allowances={YES_TOKEN: 0.01}))

    with pytest.raises(CollateralInsufficient, match="ctf_tokens_insufficient"):
        ledger.sell_preflight(token_id=YES_TOKEN, size=0.02)


def test_sell_preflight_does_NOT_substitute_pusd_for_tokens(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000_000, ctf={YES_TOKEN: 0}))

    with pytest.raises(CollateralInsufficient) as exc:
        ledger.sell_preflight(token_id=YES_TOKEN, size=1)

    assert "ctf_tokens_insufficient" in str(exc.value)
    assert "pusd" not in str(exc.value).lower()


def test_open_sell_reserves_tokens_blocks_duplicate_sell(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(ctf={YES_TOKEN: 10}))

    ledger.reserve_tokens_for_sell("cmd-a", YES_TOKEN, 10)

    assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(10)
    with pytest.raises(CollateralInsufficient, match="available=0"):
        ledger.reserve_tokens_for_sell("cmd-b", YES_TOKEN, 1)


@pytest.mark.parametrize("terminal_state", ["CANCELLED", "CANCELED", "FILLED", "EXPIRED"])
def test_release_reservation_on_cancel_or_fill(conn, terminal_state):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(ctf={YES_TOKEN: 10}))
    ledger.reserve_tokens_for_sell("cmd-a", YES_TOKEN, 10)

    assert ledger.release_reservation_on_command_terminal("cmd-a", terminal_state) is True

    ledger.reserve_tokens_for_sell("cmd-b", YES_TOKEN, 10)
    assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(10)


def test_legacy_usdc_e_classified_separately_from_pusd(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=0, usdc_e=100_000_000, ctf={YES_TOKEN: 100}))

    snap = ledger.snapshot()
    assert snap.pusd_balance_micro == 0
    assert snap.usdc_e_legacy_balance_micro == 100_000_000
    with pytest.raises(CollateralInsufficient, match="pusd_insufficient"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_authority_tier_DEGRADED_when_chain_unreachable(conn):
    ledger = CollateralLedger(conn)

    snap = ledger.refresh(FakeCollateralAdapter(exc=RuntimeError("chain_unreachable")))

    assert snap.authority_tier == "DEGRADED"
    assert snap.pusd_balance_micro == 0
    assert snap.ctf_token_balances == {}
    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_degraded"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_wrap_command_lifecycle_atomic_states(conn):
    command_id = request_wrap(5_000_000, conn=conn)
    assert get_command(command_id, conn)["state"] == WrapUnwrapState.WRAP_REQUESTED.value

    mark_tx_hashed(command_id, "0xabc", block_number=10, conn=conn)
    assert get_command(command_id, conn)["state"] == WrapUnwrapState.WRAP_TX_HASHED.value

    confirm_command(command_id, confirmation_count=2, block_number=12, conn=conn)
    row = get_command(command_id, conn)
    assert row["state"] == WrapUnwrapState.WRAP_CONFIRMED.value
    assert row["terminal_at"]
    assert conn.execute(
        "SELECT COUNT(*) FROM wrap_unwrap_events WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0] == 3


def test_unwrap_failed_does_not_decrement_pusd(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=10_000_000))
    command_id = request_unwrap(5_000_000, conn=conn)

    fail_command(command_id, error_payload={"reason": "operator_gate"}, conn=conn)

    assert get_command(command_id, conn)["state"] == WrapUnwrapState.UNWRAP_FAILED.value
    assert ledger.snapshot().pusd_balance_micro == 10_000_000


def test_pusd_redemption_blocks_until_q_fx_1_classified(monkeypatch):
    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)

    with pytest.raises(FXClassificationPending):
        require_pusd_redemption_allowed()


def test_fx_classification_enum_required_at_redemption(monkeypatch):
    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)

    with pytest.raises(TypeError):
        require_fx_classification("trading_pnl")  # type: ignore[arg-type]
    assert require_fx_classification(FXClassification.FX_LINE_ITEM) is FXClassification.FX_LINE_ITEM



def test_polymarket_client_get_balance_persists_snapshot_without_global_side_effect(tmp_path, monkeypatch):
    """Compat wrapper persists a fresh snapshot to the trade DB but MUST NOT
    publish to the global ledger slot.

    2026-05-13 contract change: the deprecated `PolymarketClient.get_balance()`
    wrapper previously did `configure_global_ledger(ledger)` while still
    owning the conn — its `finally: conn.close()` then closed the conn and
    poisoned the singleton (sqlite3.ProgrammingError on every preflight).
    Global-singleton lifecycle is owned exclusively by daemon startup.
    The compat path now only computes and persists the snapshot.
    """
    from src.data.polymarket_client import PolymarketClient
    from src.state.collateral_ledger import configure_global_ledger, get_global_ledger
    from src.state.db import init_schema

    db_path = tmp_path / "trade.db"

    def get_conn():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        init_schema(db)
        return db

    payload = {
        "pusd_balance_micro": 7_000_000,
        "pusd_allowance_micro": 7_000_000,
        "ctf_token_balances_units": {YES_TOKEN: _ctf_units(0.01)},
        "ctf_token_allowances_units": {YES_TOKEN: _ctf_units(0.01)},
        "authority_tier": "CHAIN",
    }
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", get_conn)
    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", lambda self: FakeCollateralAdapter(payload=payload))
    # Pre-condition: no global ledger set.
    configure_global_ledger(None)
    try:
        assert PolymarketClient().get_balance() == 7.0
        # Contract: get_balance() does NOT install the global singleton.
        assert get_global_ledger() is None
        # Contract: snapshot persisted to the trade DB. Verify via a fresh
        # conn (the wrapper closes its own conn).
        fresh_conn = get_conn()
        try:
            fresh = CollateralLedger(fresh_conn).snapshot()
            assert fresh.pusd_balance_micro == 7_000_000
            assert fresh.ctf_token_balances[YES_TOKEN] == _ctf_units(0.01)
        finally:
            fresh_conn.close()
    finally:
        configure_global_ledger(None)


def test_executor_buy_preflight_blocks_before_command_persistence(conn, monkeypatch):
    from src.execution.executor import _live_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=9_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - assertion tripwire
            raise AssertionError("collateral preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        with pytest.raises(CollateralInsufficient, match="pusd_insufficient"):
            _live_order("z4-buy-block", _buy_intent(size_usd=10.0), 20.0, conn=conn, decision_id="z4-buy")
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        configure_global_ledger(None)


def test_executor_buy_preflight_uses_quantized_submitted_notional(conn, monkeypatch):
    from src.execution.executor import execute_intent
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    # target_size_usd is exactly 10 pUSD, but BUY quantization at 0.333 submits
    # 30.04 shares, i.e. 10.003320 pUSD. A target-sized balance must fail closed.
    ledger.set_snapshot(_snapshot(pusd=10_000_000, pusd_allowance=10_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - assertion tripwire
            raise AssertionError("quantized-notional preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        with pytest.raises(CollateralInsufficient, match="pusd_insufficient"):
            execute_intent(
                _buy_intent(size_usd=10.0, limit_price=0.333),
                edge_vwmp=0.333,
                label="z4-quantized-notional",
                conn=conn,
                decision_id="z4-quantized-notional",
            )
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        configure_global_ledger(None)


def test_executor_sell_preflight_blocks_before_command_persistence(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 0}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - assertion tripwire
            raise AssertionError("collateral preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    intent = create_exit_order_intent(
        trade_id="z4-sell-block",
        token_id=YES_TOKEN,
        shares=5.0,
        current_price=0.50,
        best_bid=0.49,
    )
    try:
        with pytest.raises(CollateralInsufficient, match="ctf_tokens_insufficient"):
            execute_exit_order(intent, conn=conn, decision_id="z4-sell")
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        configure_global_ledger(None)



def test_executor_ack_reserves_pusd_until_terminal_release(conn, monkeypatch):
    from src.execution.executor import _live_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class FakeClient:
        def v2_preflight(self):
            return None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            return _fake_submit_result(self.bound_envelope, order_id="entry-order-1", success=True)

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        result = _live_order(
            "z4-buy-reserve",
            _buy_intent(size_usd=10.0, **_exec_snapshot_kwargs(conn)),
            20.0,
            conn=conn,
            decision_id="z4-buy-reserve",
        )
        assert result.status == "pending"
        assert ledger.snapshot().reserved_pusd_for_buys_micro == 10_000_000
        command_id = conn.execute("SELECT command_id FROM venue_commands WHERE position_id = ?", ("z4-buy-reserve",)).fetchone()[0]

        from src.state.venue_command_repo import append_event
        append_event(conn, command_id=command_id, event_type="FILL_CONFIRMED", occurred_at=datetime.now(timezone.utc).isoformat())

        assert ledger.snapshot().reserved_pusd_for_buys_micro == 0
    finally:
        configure_global_ledger(None)


def test_executor_buy_reserves_quantized_submitted_notional(conn, monkeypatch):
    from src.execution.executor import execute_intent
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class FakeClient:
        def v2_preflight(self):
            return None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            assert kwargs["size"] == 30.04
            assert kwargs["price"] == 0.333
            return _fake_submit_result(self.bound_envelope, order_id="entry-order-quantized", success=True)

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        result = execute_intent(
            _buy_intent(
                size_usd=10.0,
                limit_price=0.333,
                **_exec_snapshot_kwargs(conn, min_tick_size="0.001"),
            ),
            edge_vwmp=0.333,
            label="z4-buy-reserve-quantized",
            conn=conn,
            decision_id="z4-buy-reserve-quantized",
        )
        assert result.status == "pending"
        assert ledger.snapshot().reserved_pusd_for_buys_micro == 10_003_320
    finally:
        configure_global_ledger(None)


def test_executor_buy_rejection_release_requires_successful_terminal_append(conn, monkeypatch):
    from src.execution.executor import _live_order
    from src.state import venue_command_repo
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    real_append_event = venue_command_repo.append_event

    def append_event_fails_for_terminal(conn, *, command_id, event_type, occurred_at, payload=None):
        if event_type == "SUBMIT_REJECTED":
            raise RuntimeError("terminal append failed")
        return real_append_event(
            conn,
            command_id=command_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )

    class FakeClient:
        def v2_preflight(self):
            return None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            return _fake_submit_result(
                self.bound_envelope,
                status="REJECTED",
                success=False,
                error_code="unit_rejected",
                error_message="unit rejection",
            )

    monkeypatch.setattr("src.state.venue_command_repo.append_event", append_event_fails_for_terminal)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        result = _live_order(
                "z4-buy-terminal-append-fails",
                _buy_intent(size_usd=10.0, **_exec_snapshot_kwargs(conn)),
                20.0,
            conn=conn,
            decision_id="z4-buy-terminal-append-fails",
        )
        assert result.status == "rejected"
        assert ledger.snapshot().reserved_pusd_for_buys_micro == 10_000_000
        row = conn.execute(
            "SELECT state FROM venue_commands WHERE position_id = ?",
            ("z4-buy-terminal-append-fails",),
        ).fetchone()
        assert row[0] == "SUBMITTING"
    finally:
        configure_global_ledger(None)


def test_executor_ack_reserves_ctf_tokens_until_terminal_release(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 5}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            return _fake_submit_result(self.bound_envelope, order_id="exit-order-1", success=True)

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    intent = create_exit_order_intent(
        trade_id="z4-sell-reserve",
        token_id=YES_TOKEN,
            shares=5.0,
            current_price=0.50,
            best_bid=0.49,
            **_exec_snapshot_kwargs(conn, token_id=YES_TOKEN),
        )
    try:
        result = execute_exit_order(intent, conn=conn, decision_id="z4-sell-reserve")
        assert result.status == "pending"
        assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(5)
        command_id = conn.execute("SELECT command_id FROM venue_commands WHERE position_id = ?", ("z4-sell-reserve",)).fetchone()[0]

        from src.state.venue_command_repo import append_event
        append_event(conn, command_id=command_id, event_type="FILL_CONFIRMED", occurred_at=datetime.now(timezone.utc).isoformat())

        assert ledger.snapshot().reserved_tokens_for_sells == {}
    finally:
        configure_global_ledger(None)


def test_executor_sell_rejection_release_requires_successful_terminal_append(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state import venue_command_repo
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 5}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    real_append_event = venue_command_repo.append_event

    def append_event_fails_for_terminal(conn, *, command_id, event_type, occurred_at, payload=None):
        if event_type == "SUBMIT_REJECTED":
            raise RuntimeError("terminal append failed")
        return real_append_event(
            conn,
            command_id=command_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            return _fake_submit_result(
                self.bound_envelope,
                status="REJECTED",
                success=False,
                error_code="unit_rejected",
                error_message="unit rejection",
            )

    monkeypatch.setattr("src.state.venue_command_repo.append_event", append_event_fails_for_terminal)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    intent = create_exit_order_intent(
        trade_id="z4-sell-terminal-append-fails",
        token_id=YES_TOKEN,
            shares=5.0,
            current_price=0.50,
            best_bid=0.49,
            **_exec_snapshot_kwargs(conn, token_id=YES_TOKEN),
        )
    try:
        result = execute_exit_order(
            intent,
            conn=conn,
            decision_id="z4-sell-terminal-append-fails",
        )
        assert result.status == "rejected"
        assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(5)
        row = conn.execute(
            "SELECT state FROM venue_commands WHERE position_id = ?",
            ("z4-sell-terminal-append-fails",),
        ).fetchone()
        assert row[0] == "SUBMITTING"
    finally:
        configure_global_ledger(None)



def test_polymarket_client_get_balance_commits_snapshot_to_trade_db(tmp_path, monkeypatch):
    from src.data.polymarket_client import PolymarketClient
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    db_path = tmp_path / "trade.db"

    def get_conn():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        init_schema(db)
        return db

    payload = {
        "pusd_balance_micro": 8_000_000,
        "pusd_allowance_micro": 8_000_000,
        "ctf_token_balances_units": {YES_TOKEN: _ctf_units(0.01)},
        "ctf_token_allowances_units": {YES_TOKEN: _ctf_units(0.01)},
        "authority_tier": "CHAIN",
    }
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", get_conn)
    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", lambda self: FakeCollateralAdapter(payload=payload))
    try:
        assert PolymarketClient().get_balance() == 8.0
        fresh_conn = get_conn()
        try:
            assert CollateralLedger(fresh_conn).snapshot().pusd_balance_micro == 8_000_000
        finally:
            fresh_conn.close()
    finally:
        configure_global_ledger(None)


def test_polymarket_client_redeem_fails_closed_before_adapter_when_q_fx_open(monkeypatch):
    from src.data.polymarket_client import PolymarketClient

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)

    def adapter_tripwire(self):  # pragma: no cover - assertion tripwire
        raise AssertionError("redeem must not touch adapter while Q-FX-1 is open")

    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", adapter_tripwire)

    with pytest.raises(FXClassificationPending):
        PolymarketClient().redeem("condition-1")


def test_polymarket_client_redeem_defers_to_r1_without_sdk_side_effect(monkeypatch):
    from src.data.polymarket_client import PolymarketClient

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)

    def adapter_tripwire(self):  # pragma: no cover - assertion tripwire
        raise AssertionError("Z4 must not perform direct redeem side effects")

    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", adapter_tripwire)

    result = PolymarketClient().redeem("condition-1")
    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"


def test_v2_adapter_redeem_deferred_without_sdk_contact(monkeypatch):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    def client_factory(**kwargs):  # pragma: no cover - assertion tripwire
        raise AssertionError("Z4 adapter redeem must not construct SDK client")

    adapter = PolymarketV2Adapter(
        funder_address="0xabc",
        signer_key="key",
        q1_egress_evidence_path=None,
        client_factory=client_factory,
    )

    result = adapter.redeem("condition-1")
    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
