# Created: 2026-04-27
# Purpose: Lock R3 Z4 CollateralLedger pUSD/CTF reservation and fail-closed executor preflight behavior.
# Reuse: Run when collateral snapshots, pUSD/CTF accounting, wrap/unwrap command state, or executor collateral gates change.
# Last reused/audited: 2026-05-20
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-20; last_reused=2026-05-20
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
#                  2026-05-20 live readiness repair: wrap confirmation refresh stays behind V2 adapter boundary.
"""R3 Z4 collateral-ledger antibodies."""

from __future__ import annotations

import hashlib
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
from src.contracts.execution_intent import DecisionSourceContext, ExecutionIntent, FinalExecutionIntent
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

# Captured at import time, BEFORE conftest's autouse no-op patch runs per-test —
# the genuine unconditional redeem-submission guard (operator law 2026-06-10).
from src.execution.settlement_commands import (
    assert_redeem_submission_allowed as _REAL_ASSERT_REDEEM,
)
from src.state.collateral_ledger import (
    CollateralInsufficient,
    CollateralLedger,
    CollateralSnapshot,
    COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS,
    DEFAULT_COLLATERAL_BUSY_TIMEOUT_MS,
    SQLITE_SIGNED_INTEGER_MAX,
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


def test_init_collateral_schema_preserves_existing_busy_timeout(tmp_path):
    db_path = tmp_path / "trade.db"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 12345")

    try:
        init_collateral_schema(db)

        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 12345
    finally:
        db.close()


def test_path_backed_collateral_ledger_short_connection_uses_live_db_pragmas(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "23456")

    db_path = tmp_path / "trade.db"
    ledger = CollateralLedger(db_path=db_path)
    try:
        assert ledger._conn is None
        assert ledger._db_path == db_path
        with ledger._connection_scope() as conn:
            assert conn is not None
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 23456
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        ledger.close()


def test_path_backed_collateral_ledger_bad_timeout_env_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "1e10000")

    ledger = CollateralLedger(db_path=tmp_path / "trade.db")
    try:
        assert ledger._conn is None
        with ledger._connection_scope() as conn:
            assert conn is not None
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == DEFAULT_COLLATERAL_BUSY_TIMEOUT_MS
    finally:
        ledger.close()


def test_live_collateral_refresh_skips_when_refresh_lane_is_busy(monkeypatch):
    """RELATIONSHIP: heartbeat lane arbitration -> CollateralLedger refresh.

    External heartbeat mode can schedule a dedicated collateral refresh and
    venue-background maintenance in the same window.  Only one path may refresh
    the process-global ledger at a time; otherwise concurrent trade DB writes can
    starve submit preflight.
    """

    import src.main as main
    from src.state.collateral_ledger import configure_global_ledger

    class Adapter:
        calls = 0

        def get_collateral_payload(self):
            self.calls += 1
            return {
                "pusd_balance_micro": 10_000_000,
                "pusd_allowance_micro": 10_000_000,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
            }

    adapter = Adapter()
    ledger = CollateralLedger()
    configure_global_ledger(ledger)
    main._last_collateral_heartbeat_refresh_attempt_at = None
    assert main._collateral_background_refresh_lock.acquire(blocking=False)
    try:
        assert (
            main._refresh_global_collateral_snapshot_if_due(
                adapter,
                now=datetime.now(timezone.utc),
            )
            is False
        )
        assert adapter.calls == 0
    finally:
        main._collateral_background_refresh_lock.release()
        configure_global_ledger(None)


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
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    captured_at = datetime(2026, 4, 27, tzinfo=timezone.utc)
    snapshot_id = f"snap-{token_id}-{min_tick_size}-{min_order_size}"
    if get_snapshot(conn, snapshot_id) is None:
        insert_snapshot(
            conn,
            ExecutableMarketSnapshot(
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


def _final_buy_intent(
    conn,
    *,
    token_id: str = YES_TOKEN,
    submitted_shares: str = "10.00",
    final_limit_price: str = "0.50",
    min_tick_size: str = "0.01",
    min_order_size: str = "0.01",
) -> FinalExecutionIntent:
    """Current (a4707d1be) live entry contract: execute_intent/ExecutionIntent is
    LEGACY_EXECUTION_INTENT_LIVE_BLOCKED under get_mode()=='live'; live entry
    submission goes through FinalExecutionIntent + execute_final_intent. Shape
    mirrors tests/test_executor_command_split.py's
    test_final_intent_legacy_envelope_ignores_pre_submit_audit_only_gaps and
    tests/test_executor.py's _final_execution_intent -- placeholder q_live/
    q_lcb_5pct/etc are not the subject under test here (collateral preflight
    is) and are bypassed the same way ExecutionIntent's economics were, via
    the _entry_economics_component monkeypatch each caller already installs.
    submitted_shares is taken verbatim by execute_final_intent
    (_final_intent_submit_shares) -- no internal re-quantization -- so a
    caller wanting a specific quantized share count (e.g. 30.04) passes it
    directly, matching what the legacy ExecutionIntent-based test asserted
    the venue received.
    """
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = f"snap-final-{token_id}-{min_tick_size}-{min_order_size}"
    captured_at = datetime(2026, 4, 27, tzinfo=timezone.utc)
    price = Decimal(final_limit_price)
    top_ask = price
    top_bid = max(Decimal("0.01"), top_ask - Decimal("0.01"))
    if get_snapshot(conn, snapshot_id) is None:
        insert_snapshot(
            conn,
            ExecutableMarketSnapshot(
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
                orderbook_top_bid=top_bid,
                orderbook_top_ask=top_ask,
                # order_policy=limit_may_take_conservative requires actual
                # depth (not "{}") or pre-venue validation fails closed with
                # PreVenueSubmitError: "executable depth validation failed: EMPTY_BOOK".
                orderbook_depth_jsonb=json.dumps(
                    {
                        "bids": [{"price": str(top_bid), "size": "100"}],
                        "asks": [{"price": str(top_ask), "size": "100"}],
                    }
                ),
                raw_gamma_payload_hash="a" * 64,
                raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64,
                authority_tier="CLOB",
                captured_at=captured_at,
                freshness_deadline=captured_at + timedelta(days=365),
            ),
        )
    # _final_intent_snapshot_metadata requires snapshot_hash to match the
    # STORED snapshot's own computed executable_snapshot_hash exactly -- an
    # arbitrary placeholder ("a" * 64) fails closed with
    # "snapshot_hash does not match executable snapshot".
    snapshot = get_snapshot(conn, snapshot_id)
    assert snapshot is not None
    return FinalExecutionIntent(
        hypothesis_id=f"hyp-{token_id}",
        selected_token_id=token_id,
        direction="buy_yes",
        size_kind="shares",
        size_value=Decimal(submitted_shares),
        submitted_shares=Decimal(submitted_shares),
        final_limit_price=Decimal(final_limit_price),
        expected_fill_price_before_fee=Decimal(final_limit_price),
        fee_adjusted_execution_price=Decimal(final_limit_price),
        order_policy="limit_may_take_conservative",
        order_type="FOK",
        post_only=False,
        cancel_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot.executable_snapshot_hash,
        cost_basis_id="cost_basis:" + ("b" * 16),
        cost_basis_hash="b" * 64,
        max_slippage_bps=Decimal("200"),
        tick_size=Decimal(min_tick_size),
        min_order_size=Decimal(min_order_size),
        fee_rate=Decimal("0"),
        neg_risk=False,
        event_id="event-test",
        resolution_window="2026-04-27",
        correlation_key="z4:2026-04-27",
        decision_source_context=_decision_source_context(),
        q_live=0.99,
        q_lcb_5pct=0.95,
        expected_edge=0.07,
        min_entry_price=0.05,
        min_expected_profit_usd=0.05,
        min_submit_edge_density=0.02,
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.99,
            "payoff_q_lcb": 0.95,
            "cost": float(final_limit_price),
            "edge_lcb": 0.70,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.95,
        },
    )


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


def test_refresh_caps_uint256_allowance_to_sqlite_domain(conn):
    class Adapter:
        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 199_396_602,
                "pusd_allowance_micro": (2**256) - 1,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
            }

    ledger = CollateralLedger(conn)
    snapshot = ledger.refresh(Adapter())

    assert snapshot.pusd_allowance_micro == SQLITE_SIGNED_INTEGER_MAX
    assert ledger.buy_preflight(_buy_intent(size_usd=10.0))
    row = conn.execute(
        "SELECT pusd_allowance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == SQLITE_SIGNED_INTEGER_MAX


def test_set_snapshot_caps_uint256_allowance_to_sqlite_domain(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        _snapshot(pusd=199_396_602, pusd_allowance=(2**256) - 1)
    )

    row = conn.execute(
        "SELECT pusd_allowance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == SQLITE_SIGNED_INTEGER_MAX


def test_buy_preflight_blocks_when_snapshot_stale(conn):
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        _snapshot(
            captured_at=datetime.now(timezone.utc)
            - timedelta(seconds=COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS + 1)
        )
    )

    with pytest.raises(CollateralInsufficient, match="collateral_snapshot_stale"):
        ledger.buy_preflight(_buy_intent(size_usd=1.0))


def test_buy_preflight_allows_background_attestation_jitter_within_sla(conn):
    """RELATIONSHIP: background collateral attestation -> submit preflight."""

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        _snapshot(
            captured_at=datetime.now(timezone.utc)
            - timedelta(seconds=COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS - 30)
        )
    )

    assert ledger.buy_preflight(_buy_intent(size_usd=1.0)) is True


def test_buy_preflight_reloads_newer_persisted_snapshot_before_freshness_gate(conn):
    """RELATIONSHIP: external snapshot writer -> live preflight freshness gate."""

    ledger = CollateralLedger(conn)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    ledger.set_snapshot(_snapshot(pusd=0, captured_at=old_time))

    writer = CollateralLedger(conn)
    fresh_time = datetime.now(timezone.utc)
    writer.set_snapshot(_snapshot(pusd=2_000_000, captured_at=fresh_time))

    assert ledger.buy_preflight(_buy_intent(size_usd=1.0)) is True
    assert ledger.snapshot().pusd_balance_micro == 2_000_000


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
            captured_at=datetime.now(timezone.utc)
            - timedelta(seconds=COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS + 1),
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


def test_refresh_keeps_fresh_chain_snapshot_when_next_attestation_fails(conn):
    """RELATIONSHIP: transient collateral read failure -> latest live snapshot.

    A short API/SDK failure must not insert a zero-balance DEGRADED row over a
    still-fresh CHAIN snapshot. Once the authoritative snapshot ages out, the
    same failure path remains fail-closed and may persist DEGRADED.
    """

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=10_000_000, pusd_allowance=10_000_000))

    snap = ledger.refresh(FakeCollateralAdapter(exc=RuntimeError("chain_unreachable")))

    assert snap.authority_tier == "CHAIN"
    assert snap.pusd_balance_micro == 10_000_000
    rows = conn.execute(
        "SELECT authority_tier, pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC"
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("CHAIN", 10_000_000)]


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


def test_wrap_confirm_refreshes_collateral_through_v2_adapter_boundary(conn):
    """RELATIONSHIP: WRAP_CONFIRMED -> V2 adapter payload refresh, no SDK v1 import."""
    from src.execution.wrap_unwrap_commands import reconcile_pending_wraps

    class FakeEth:
        def get_transaction_receipt(self, tx_hash):
            return {"status": 1, "blockNumber": 12}

    class FakeWeb3:
        eth = FakeEth()

    class V2AdapterOnly:
        def __init__(self) -> None:
            self.payload_calls = 0

        def get_collateral_payload(self):
            self.payload_calls += 1
            return {"pusd_balance_micro": 1, "pusd_allowance_micro": 1}

    adapter = V2AdapterOnly()
    command_id = request_wrap(5_000_000, conn=conn)
    mark_tx_hashed(command_id, "0x" + "a" * 64, block_number=10, conn=conn)
    conn.commit()

    results = reconcile_pending_wraps(FakeWeb3(), adapter, conn)

    assert results
    assert get_command(command_id, conn)["state"] == WrapUnwrapState.WRAP_CONFIRMED.value
    assert adapter.payload_calls == 1


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



def test_polymarket_client_get_balance_reads_wallet_without_trade_db_write(monkeypatch):
    """Scalar bankroll readers must not contend for the trade DB write lock."""
    from src.data.polymarket_client import PolymarketClient
    from src.state.collateral_ledger import configure_global_ledger, get_global_ledger

    def locked_trade_conn():
        raise sqlite3.OperationalError("database is locked")

    payload = {
        "pusd_balance_micro": 7_000_000,
        "pusd_allowance_micro": 7_000_000,
        "ctf_token_balances_units": {YES_TOKEN: _ctf_units(0.01)},
        "ctf_token_allowances_units": {YES_TOKEN: _ctf_units(0.01)},
        "authority_tier": "CHAIN",
    }
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", locked_trade_conn)
    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", lambda self: FakeCollateralAdapter(payload=payload))
    configure_global_ledger(None)
    try:
        assert PolymarketClient().get_balance() == 7.0
        assert get_global_ledger() is None
    finally:
        configure_global_ledger(None)


def test_v2_collateral_payload_uses_data_api_positions_and_conditional_micro_balance(monkeypatch, tmp_path):
    """RELATIONSHIP: data-api position set -> CLOB conditional balance -> CTF sell preflight units."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    class FakeClient:
        def update_balance_allowance(self, params):
            return {}

        def get_balance_allowance(self, params):
            token_id = getattr(params, "token_id", None)
            if token_id == "token-A":
                return {"balance": "10000000"}  # CLOB conditional balances are already micro-units.
            return {"balance": "200000000", "allowance": "99000000"}

    def fake_urlopen(request, timeout):
        assert "data-api.polymarket.com/positions" in request.full_url
        return FakeResponse(
            [
                {
                    "asset": "token-A",
                    "size": 10,
                    "outcome": "Yes",
                }
            ]
        )

    monkeypatch.setattr(
        "src.venue.polymarket_v2_adapter.urllib.request.urlopen",
        fake_urlopen,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: FakeClient(),
    )

    payload = adapter.get_collateral_payload()

    assert payload["ctf_token_balances_units"] == {"token-A": 10_000_000}
    assert payload["ctf_token_allowances_units"] == {"token-A": 10_000_000}


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
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - assertion tripwire
            raise AssertionError("collateral preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        # Contract since a6f47aa4a (2026-06-28, provenance-verified live fix,
        # predates this rebuild base): _live_order no longer lets
        # CollateralInsufficient escape -- it is caught internally and
        # converted to a rejected OrderResult (executor.py's pre-command
        # branch: no venue command exists yet, so no SUBMIT_REJECTED
        # journaling either, just a rollback + warning log).
        result = _live_order("z4-buy-block", _buy_intent(size_usd=10.0), 20.0, conn=conn, decision_id="z4-buy")
        assert result.status == "rejected"
        assert result.command_state == "REJECTED"
        assert "pusd_insufficient" in (result.reason or "")
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        configure_global_ledger(None)


def test_executor_buy_preflight_uses_quantized_submitted_notional(conn, monkeypatch):
    from src.execution.executor import execute_final_intent
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    # target_size_usd is exactly 10 pUSD, but BUY quantization at 0.50 submits
    # 20.02 shares, i.e. 10.01 pUSD. A target-sized balance must fail closed.
    # (was 30.04 shares @ 0.333 = 10.003320 under the pre-a4707d1be model;
    # venue_submit_amount_precision_error's current immediate-BUY maker/taker
    # decimal-precision check -- new in a4707d1be -- rejects that exact
    # combination before ever reaching collateral preflight, so the specific
    # numbers changed; the theme -- quantized notional exceeds a target-sized
    # balance -- is unchanged.)
    ledger.set_snapshot(_snapshot(pusd=10_000_000, pusd_allowance=10_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - assertion tripwire
            raise AssertionError("quantized-notional preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        # Contract since a4707d1be (2026-05-21, "harden side-effect and risk
        # boundaries" #271, provenance-verified, predates this rebuild base):
        # execute_intent/ExecutionIntent is LEGACY_EXECUTION_INTENT_LIVE_BLOCKED
        # under get_mode()=='live' (hardcoded live-only now, src/config.py).
        # Live entry submission goes through FinalExecutionIntent +
        # execute_final_intent instead; both still funnel through the SAME
        # _live_order collateral preflight this test exercises. The quantized
        # share count is supplied directly as submitted_shares --
        # execute_final_intent takes it verbatim, no internal re-quantization.
        final_intent = _final_buy_intent(
            conn, submitted_shares="20.02", final_limit_price="0.50", min_tick_size="0.01",
        )
        result = execute_final_intent(
            final_intent, conn=conn, decision_id="z4-quantized-notional",
        )
        assert result.status == "rejected"
        assert "pusd_insufficient" in (result.reason or "")
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        configure_global_ledger(None)


def test_executor_sell_preflight_blocks_before_command_persistence(conn, monkeypatch):
    from src.execution import executor as executor_module
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 0}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        executor_module,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda conn, *, token_id, shares: {
            "component": "collateral_snapshot_refresh",
            "allowed": True,
            "reason": "test_noop_refresh",
        },
    )

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


def test_executor_exit_refreshes_ctf_snapshot_before_sell_preflight(conn, monkeypatch):
    from src.execution import executor as executor_module
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 0}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)

    def refresh_exit_collateral(conn, *, token_id, shares):
        assert token_id == YES_TOKEN
        assert shares == pytest.approx(5.0)
        ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 5}))
        return {
            "component": "collateral_snapshot_refresh",
            "allowed": True,
            "reason": "refreshed_before_exit_submit",
            "token_id": token_id,
        }

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            return _fake_submit_result(self.bound_envelope, order_id="exit-refresh-order-1", success=True)

    monkeypatch.setattr(executor_module, "_refresh_exit_collateral_snapshot_for_submit", refresh_exit_collateral)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    intent = create_exit_order_intent(
        trade_id="z4-sell-refresh-before-preflight",
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
            decision_id="z4-sell-refresh-before-preflight",
        )
        assert result.status == "pending"
        assert "pre_submit_collateral_reservation_failed" not in (result.reason or "")
        assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(5)
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
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )

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
    from src.execution.executor import execute_final_intent
    from src.state.collateral_ledger import configure_global_ledger
    from src.state.db import init_schema

    init_schema(conn)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000, ctf={YES_TOKEN: 100}))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )

    class FakeClient:
        def v2_preflight(self):
            return None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, **kwargs):
            assert kwargs["size"] == 20.02
            assert kwargs["price"] == 0.50
            return _fake_submit_result(self.bound_envelope, order_id="entry-order-quantized", success=True)

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        # Same a4707d1be migration and quantized-values rationale as
        # test_executor_buy_preflight_uses_quantized_submitted_notional above
        # (execute_intent/ExecutionIntent -> execute_final_intent/FinalExecutionIntent;
        # 30.04 shares @ 0.333 -> 20.02 shares @ 0.50 to clear the new
        # immediate-BUY maker/taker decimal-precision gate).
        final_intent = _final_buy_intent(
            conn, submitted_shares="20.02", final_limit_price="0.50", min_tick_size="0.01",
        )
        result = execute_final_intent(
            final_intent, conn=conn, decision_id="z4-buy-reserve-quantized",
        )
        assert result.status == "pending"
        assert ledger.snapshot().reserved_pusd_for_buys_micro == 10_010_000
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
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )
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
        # Contract since a4707d1be (2026-05-21, "harden side-effect and risk
        # boundaries" #271, provenance-verified, predates this rebuild base):
        # when the SDK call itself succeeds (venue side effect may have
        # happened) but the SUBMIT_REJECTED journaling append then fails, the
        # command can no longer be honestly reported "rejected" -- the venue
        # truth is unconfirmed. _mark_post_submit_persistence_failure rolls
        # back the failed append and durably writes REVIEW_REQUIRED instead
        # (a recovery-owned quasi-terminal state, not TERMINAL_STATES), and
        # _live_order returns status="unknown_side_effect".
        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert result.reason == "terminal_rejection_persistence_failed_after_side_effect"
        # The theme this test's name asserts: collateral release requires a
        # SUCCESSFUL terminal append. REVIEW_REQUIRED is not in
        # TERMINAL_STATES, so no release/conversion path fired -- the pUSD
        # reservation committed at command-persistence time is still intact.
        assert ledger.snapshot().reserved_pusd_for_buys_micro == 10_000_000
        row = conn.execute(
            "SELECT state FROM venue_commands WHERE position_id = ?",
            ("z4-buy-terminal-append-fails",),
        ).fetchone()
        assert row[0] == "REVIEW_REQUIRED"
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
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        # Not the subject under test (collateral preflight is); same idiom as
        # the cutover/heartbeat bypasses above and tests/test_unknown_side_effect.py.
        lambda *args, **kwargs: {"component": "entry_taker_quality", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: ({"component": "entry_actionable_certificate", "allowed": True, "reason": "allowed"}, None),
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_economics_component",
        lambda *args, **kwargs: {
            "component": "entry_economics",
            "allowed": True,
            "reason": "allowed",
            # ENTRY SUBMIT_REQUESTED validation (venue_command_repo._validate_entry_submit_payload)
            # requires a populated details dict for this component -- placeholder values, matching
            # the established minimal-payload convention (tests/test_venue_command_repo.py's
            # _valid_execution_capability_payload). Collateral preflight is the subject under test,
            # not economics; validation here is presence-only, never checked against these values.
            "details": {
                "q_live": 0.7, "q_lcb_5pct": 0.6, "expected_edge": 0.1, "min_entry_price": 0.01,
                "limit_price": 0.5, "submit_edge": 0.1, "expected_profit_usd": 1.0,
                "min_expected_profit_usd": 0.01, "submit_edge_density": 0.1,
                "min_submit_edge_density": 0.01, "shares": 10.0, "qkernel_side": "buy_yes",
            },
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {"component": "entries_pause_control_override", "allowed": True, "reason": "not_paused"},
    )
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
        # Same a4707d1be contract as the buy-side sibling test above: SDK
        # succeeded, SUBMIT_REJECTED journaling failed -> REVIEW_REQUIRED +
        # status="unknown_side_effect", never a false "rejected".
        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert result.reason == "terminal_rejection_persistence_failed_after_side_effect"
        # Theme preserved: no successful terminal append -> no release. The
        # CTF reservation committed at command-persistence time is intact.
        assert ledger.snapshot().reserved_tokens_for_sells[YES_TOKEN] == _ctf_units(5)
        row = conn.execute(
            "SELECT state FROM venue_commands WHERE position_id = ?",
            ("z4-sell-terminal-append-fails",),
        ).fetchone()
        assert row[0] == "REVIEW_REQUIRED"
    finally:
        configure_global_ledger(None)



def test_polymarket_client_get_wallet_balance_prefers_direct_adapter_balance(monkeypatch):
    from src.data.polymarket_client import PolymarketClient

    class DirectBalanceAdapter:
        def get_pusd_balance_micro(self):
            return 8_000_000

        def get_collateral_payload(self):
            raise AssertionError("direct balance path should not load collateral payload")

    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", lambda self: DirectBalanceAdapter())
    assert PolymarketClient().get_wallet_balance() == 8.0


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


def test_v2_adapter_redeem_forbidden_without_sdk_contact(monkeypatch):
    """Operator law 2026-06-10 (redeem submission FORBIDDEN): adapter.redeem()
    raises REDEEM_SUBMISSION_FORBIDDEN before ANY construction — strictly
    stronger than the former REDEEM_DEFERRED_TO_R1 stub (no SDK client, no RPC).
    Restores the real guard (conftest installs a session no-op patch)."""
    import src.execution.settlement_commands as _sc
    from src.execution.settlement_commands import RedeemSubmissionAbandonedError
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    monkeypatch.setattr(_sc, "assert_redeem_submission_allowed", _REAL_ASSERT_REDEEM)

    def client_factory(**kwargs):  # pragma: no cover - assertion tripwire
        raise AssertionError("Z4 adapter redeem must not construct SDK client")

    adapter = PolymarketV2Adapter(
        funder_address="0xabc",
        signer_key="key",
        q1_egress_evidence_path=None,
        client_factory=client_factory,
    )

    with pytest.raises(RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_FORBIDDEN"):
        adapter.redeem("condition-1")


# ---------------------------------------------------------------------------
# SCH-W1.1-CAS-LEDGER: CAS reservation ledger, convert-on-fill, type-aware A4
# identity acceptance tests.
# ---------------------------------------------------------------------------


def _insert_test_command(
    conn,
    command_id: str,
    *,
    # NOTE: intent_kind defaults to "EXIT" (not "ENTRY") even for BUY-side
    # fixtures in this section — _validate_entry_submit_payload requires a
    # full execution_capability/entry_economics proof payload for
    # ENTRY+SUBMIT_REQUESTED that is orthogonal to what these collateral-seam
    # tests exercise. intent_kind does not affect reservation/conversion
    # logic (which reads reservation_type, size, price — not intent_kind).
    intent_kind: str = "EXIT",
    side: str = "BUY",
    size: float = 10.0,
    price: float = 0.5,
    token_id: str = YES_TOKEN,
) -> None:
    """Minimal direct venue_commands row for collateral-ledger unit tests.

    Bypasses insert_command's U1/U2 snapshot+envelope gates (no FK enforcement
    on those two columns per the DDL) so tests can focus on the collateral
    seam without standing up executable-market-snapshot/envelope fixtures.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'INTENT_CREATED', ?, ?)
        """,
        (
            command_id, f"snap-{command_id}", f"env-{command_id}", f"pos-{command_id}",
            f"dec-{command_id}", f"idem-{command_id}", intent_kind, "z4-market",
            token_id, side, size, price, now, now,
        ),
    )


def _walk_to_acked(conn, command_id: str) -> None:
    from src.state.venue_command_repo import append_event

    now = datetime.now(timezone.utc).isoformat()
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=now,
        payload={
            "execution_capability": {
                "allowed": True,
                "components": [
                    {"component": "entry_economics", "allowed": True},
                    {"component": "entry_actionable_certificate", "allowed": True},
                ],
            }
        },
    )
    append_event(conn, command_id=command_id, event_type="SUBMIT_ACKED", occurred_at=now)


class _RaisingConn:
    """Minimal conn stand-in exposing only .execute(), for unit-testing the
    trigger-IntegrityError -> CollateralInsufficient mapping without racing."""

    def execute(self, *args, **kwargs):
        raise sqlite3.IntegrityError("CHECK constraint failed: COLLATERAL_OVERRESERVE")


def test_cas_insert_pusd_reservation_maps_trigger_integrity_error_to_collateral_insufficient():
    """Critic ruling 7b: RAISE(ABORT,'COLLATERAL_OVERRESERVE') (IntegrityError)
    must never leak past the ledger API as a raw sqlite3 exception."""
    with pytest.raises(CollateralInsufficient, match="pusd_insufficient_trigger"):
        CollateralLedger._cas_insert_pusd_reservation(
            _RaisingConn(), "cmd-x", 1_000_000, datetime.now(timezone.utc).isoformat()
        )


def test_cas_trigger_raises_on_raw_bypass_insert(conn):
    """Belt-and-braces: the AFTER INSERT trigger independently enforces
    non-overreserve even for a write path that bypasses the guarded CAS
    WHERE clause (defense in depth for a future/other writer)."""
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=5_000_000))

    with pytest.raises(sqlite3.IntegrityError, match="COLLATERAL_OVERRESERVE"):
        conn.execute(
            """
            INSERT INTO collateral_reservations
              (command_id, reservation_type, token_id, amount, converted_amount, created_at)
            VALUES (?, 'PUSD_BUY', NULL, ?, 0, ?)
            """,
            ("bypass-cmd", 10_000_000, datetime.now(timezone.utc).isoformat()),
        )


def test_cas_reserve_pusd_for_buy_blocks_second_racer_via_cas_not_preflight(conn):
    """The CAS INSERT itself — not just buy_preflight — is the enforcement
    authority: directly exercising the CAS bypassing preflight proves the
    guarded statement alone closes the over-reserve window."""
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=10_000_000))
    now = datetime.now(timezone.utc).isoformat()

    CollateralLedger._cas_insert_pusd_reservation(conn, "cmd-a", 6_000_000, now)
    with pytest.raises(CollateralInsufficient, match="pusd_insufficient_cas"):
        CollateralLedger._cas_insert_pusd_reservation(conn, "cmd-b", 6_000_000, now)

    total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations WHERE released_at IS NULL"
    ).fetchone()[0]
    assert total == 6_000_000


@pytest.mark.parametrize("mode", ["memory", "caller_conn_file", "db_path"])
def test_cas_reserve_correct_under_all_three_connection_modes(tmp_path, mode):
    """tests_required: 'three connection modes: CAS correct under in-memory,
    caller-owned-conn (insert_command-first pattern), and db_path modes.'"""
    if mode == "memory":
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        init_collateral_schema(raw)
        ledger = CollateralLedger(raw)
    elif mode == "caller_conn_file":
        db_path = tmp_path / "caller.db"
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        init_collateral_schema(raw)
        ledger = CollateralLedger(raw)
    else:
        db_path = tmp_path / "owned.db"
        setup_conn = sqlite3.connect(db_path)
        init_collateral_schema(setup_conn)
        setup_conn.commit()
        setup_conn.close()
        ledger = CollateralLedger(db_path=db_path)

    ledger.set_snapshot(_snapshot(pusd=10_000_000))
    ledger.reserve_pusd_for_buy("cmd-a", 6_000_000)
    with pytest.raises(CollateralInsufficient):
        ledger.reserve_pusd_for_buy("cmd-b", 6_000_000)

    assert ledger.snapshot().reserved_pusd_for_buys_micro == 6_000_000
    ledger.close()


def test_convert_reservation_on_fill_converts_filled_portion_and_releases_remainder(conn):
    """PUSD_BUY FILLED with a partial cumulative fill: the filled fraction
    converts to an unsettled OUTGOING_DEDUCTION row; the remainder releases.
    Derivation uses MAX(matched_size) over the fact stream (critic ruling 1)."""
    from src.state.db import init_schema
    from src.state.venue_command_repo import append_event, append_order_fact

    init_schema(conn)
    command_id = "cmd-convert-fill"
    _insert_test_command(conn, command_id, size=10.0, price=0.5)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000))
    ledger.reserve_pusd_for_buy(command_id, 5_000_000)
    _walk_to_acked(conn, command_id)

    now = datetime.now(timezone.utc)
    append_order_fact(
        conn,
        venue_order_id="vo-convert-fill",
        command_id=command_id,
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="WS_USER",
        observed_at=now,
        raw_payload_hash="a" * 64,
    )
    append_order_fact(
        conn,
        venue_order_id="vo-convert-fill",
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size="10",
        source="WS_USER",
        observed_at=now,
        raw_payload_hash="b" * 64,
    )
    append_event(conn, command_id=command_id, event_type="FILL_CONFIRMED", occurred_at=now.isoformat())

    row = conn.execute(
        "SELECT amount, converted_amount, released_at, release_reason "
        "FROM collateral_reservations WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row[0] == 5_000_000  # amount is immutable
    assert row[1] == 5_000_000  # fully matched (10/10) -> fully converted
    assert row[2] is not None
    assert row[3] == "CONVERTED_ON_FILL"

    unsettled = conn.execute(
        "SELECT direction, reservation_type, token_id, amount_micro, settled_at "
        "FROM collateral_unsettled_proceeds WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert tuple(unsettled) == ("OUTGOING_DEDUCTION", "PUSD_BUY", None, 5_000_000, None)

    # spendable does NOT increase at conversion time (still reduced by the
    # unsettled OUTGOING_DEDUCTION even though released_at is now set).
    assert ledger.snapshot().reserved_pusd_for_buys_micro == 0
    spendable = 100_000_000 - 0 - 5_000_000
    assert spendable == 95_000_000


def test_convert_reservation_on_fill_idempotent_on_replayed_terminal_event(conn):
    """Single idempotent terminal write guarded by WHERE released_at IS NULL:
    a re-delivered terminal dispatch call is a safe no-op, never double-inserts
    into collateral_unsettled_proceeds."""
    from src.state.db import init_schema
    from src.state.collateral_ledger import convert_reservation_on_fill
    from src.state.venue_command_repo import append_order_fact

    init_schema(conn)
    command_id = "cmd-idempotent-terminal"
    _insert_test_command(conn, command_id, size=10.0, price=0.5)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000))
    ledger.reserve_pusd_for_buy(command_id, 5_000_000)
    _walk_to_acked(conn, command_id)

    now = datetime.now(timezone.utc)
    append_order_fact(
        conn,
        venue_order_id="vo-idem",
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size="10",
        source="WS_USER",
        observed_at=now,
        raw_payload_hash="c" * 64,
    )

    assert convert_reservation_on_fill(conn, command_id, "FILLED") is True
    assert convert_reservation_on_fill(conn, command_id, "FILLED") is False

    unsettled_count = conn.execute(
        "SELECT COUNT(*) FROM collateral_unsettled_proceeds WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    assert unsettled_count == 1
    converted_amount = conn.execute(
        "SELECT converted_amount FROM collateral_reservations WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    assert converted_amount == 5_000_000


def test_partial_then_cancel_converts_filled_releases_remainder(conn):
    """tests_required: partial-then-cancel — matched>0 then CANCELLED: the
    filled-notional portion converts, the unfilled remainder releases, ONE
    idempotent terminal write."""
    from src.state.db import init_schema
    from src.state.venue_command_repo import append_event, append_order_fact

    init_schema(conn)
    command_id = "cmd-partial-cancel"
    _insert_test_command(conn, command_id, size=10.0, price=0.5)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000))
    ledger.reserve_pusd_for_buy(command_id, 5_000_000)
    _walk_to_acked(conn, command_id)

    now = datetime.now(timezone.utc)
    append_order_fact(
        conn,
        venue_order_id="vo-partial-cancel",
        command_id=command_id,
        state="PARTIALLY_MATCHED",
        remaining_size="6",
        matched_size="4",
        source="WS_USER",
        observed_at=now,
        raw_payload_hash="d" * 64,
    )
    append_event(conn, command_id=command_id, event_type="PARTIAL_FILL_OBSERVED", occurred_at=now.isoformat())
    append_event(conn, command_id=command_id, event_type="CANCEL_REQUESTED", occurred_at=now.isoformat())
    append_event(conn, command_id=command_id, event_type="CANCEL_ACKED", occurred_at=now.isoformat())

    row = conn.execute(
        "SELECT amount, converted_amount, released_at, release_reason "
        "FROM collateral_reservations WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row[0] == 5_000_000
    assert row[1] == 2_000_000  # floor(5_000_000 * 4/10)
    assert row[2] is not None
    assert row[3] == "CONVERTED_ON_FILL"

    unsettled = conn.execute(
        "SELECT amount_micro FROM collateral_unsettled_proceeds WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert unsettled[0] == 2_000_000

    state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?", (command_id,)
    ).fetchone()[0]
    assert state == "CANCELLED"


def test_type_aware_identity_ctf_sell_proceeds_never_reduce_spendable_pusd(conn):
    """CTF_SELL proceeds are INCOMING, recorded as INCOMING_PROCEEDS, and
    tracked for the identity but never part of spendable_pusd while unsettled
    — never uniform subtraction."""
    from src.state.db import init_schema
    from src.state.venue_command_repo import append_event, append_order_fact

    init_schema(conn)
    command_id = "cmd-sell-fill"
    _insert_test_command(
        conn, command_id, intent_kind="EXIT", side="SELL", size=10.0, price=0.6, token_id=YES_TOKEN
    )
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=50_000_000, ctf={YES_TOKEN: 10}))
    ledger.reserve_tokens_for_sell(command_id, YES_TOKEN, 10)
    _walk_to_acked(conn, command_id)

    now = datetime.now(timezone.utc)
    append_order_fact(
        conn,
        venue_order_id="vo-sell-fill",
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size="10",
        source="WS_USER",
        observed_at=now,
        raw_payload_hash="e" * 64,
    )
    append_event(conn, command_id=command_id, event_type="FILL_CONFIRMED", occurred_at=now.isoformat())

    unsettled = conn.execute(
        "SELECT direction, reservation_type, token_id, amount_micro FROM collateral_unsettled_proceeds "
        "WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert unsettled[0] == "INCOMING_PROCEEDS"
    assert unsettled[1] == "CTF_SELL"
    assert unsettled[2] == YES_TOKEN
    assert unsettled[3] == 6_000_000  # 10 shares * 0.6 price * 1e6

    # spendable_pusd formula excludes INCOMING_PROCEEDS entirely.
    live_buy = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations "
        "WHERE reservation_type='PUSD_BUY' AND released_at IS NULL"
    ).fetchone()[0]
    unsettled_outgoing = conn.execute(
        "SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds "
        "WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL"
    ).fetchone()[0]
    spendable_pusd = 50_000_000 - live_buy - unsettled_outgoing
    assert spendable_pusd == 50_000_000  # unaffected by the sell proceeds

    # CTF token reservation itself released (tokens left the wallet, sold).
    assert ledger.snapshot().reserved_tokens_for_sells == {}


def test_clearing_settles_matured_unsettled_rows_inside_refresh_transaction(conn):
    """Settlement-coordinated clearing (critic ruling 4): a balance snapshot
    captured after converted_at + CLOCK_SKEW settles the row inside the same
    write transaction as the new snapshot insert."""
    from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS

    old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    conn.execute(
        """
        INSERT INTO collateral_unsettled_proceeds
          (command_id, direction, reservation_type, token_id, amount_micro, created_at)
        VALUES ('cmd-clear', 'OUTGOING_DEDUCTION', 'PUSD_BUY', NULL, 3_000_000, ?)
        """,
        (old_time.isoformat(),),
    )
    conn.commit()

    ledger = CollateralLedger(conn)
    fresh_time = old_time + timedelta(seconds=COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS + 30)
    ledger.set_snapshot(_snapshot(pusd=50_000_000, captured_at=fresh_time))

    row = conn.execute(
        "SELECT settled_at, settle_reason FROM collateral_unsettled_proceeds WHERE command_id = 'cmd-clear'"
    ).fetchone()
    assert row[0] is not None
    assert row[1] == "BALANCE_REFRESH_OBSERVED"


def test_clearing_does_not_settle_row_within_clock_skew_tolerance(conn):
    """Rows younger than converted_at + CLOCK_SKEW must NOT settle yet — the
    venue has not had the chance to reflect the deduction."""
    old_time = datetime.now(timezone.utc) - timedelta(seconds=2)
    conn.execute(
        """
        INSERT INTO collateral_unsettled_proceeds
          (command_id, direction, reservation_type, token_id, amount_micro, created_at)
        VALUES ('cmd-too-fresh', 'OUTGOING_DEDUCTION', 'PUSD_BUY', NULL, 3_000_000, ?)
        """,
        (old_time.isoformat(),),
    )
    conn.commit()

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=50_000_000, captured_at=datetime.now(timezone.utc)))

    row = conn.execute(
        "SELECT settled_at FROM collateral_unsettled_proceeds WHERE command_id = 'cmd-too-fresh'"
    ).fetchone()
    assert row[0] is None


def test_idempotent_derivation_replay_duplicate_partial_fact_stream(conn):
    """tests_required: replay the same PARTIALLY_MATCHED fact stream (WS +
    reconcile + recovery duplicates) — derived live remaining invariant under
    replay; ZERO ledger writes on partial facts."""
    from src.state.db import init_schema
    from src.state.collateral_ledger import _max_matched_size
    from src.state.venue_command_repo import append_order_fact

    init_schema(conn)
    command_id = "cmd-replay-partial"
    _insert_test_command(conn, command_id, size=10.0, price=0.5)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=100_000_000))
    ledger.reserve_pusd_for_buy(command_id, 5_000_000)
    _walk_to_acked(conn, command_id)

    now = datetime.now(timezone.utc)
    for source in ("WS_USER", "REST", "OPERATOR"):  # WS + reconcile + recovery duplicate delivery
        append_order_fact(
            conn,
            venue_order_id="vo-replay",
            command_id=command_id,
            state="PARTIALLY_MATCHED",
            remaining_size="4",
            matched_size="6",
            source=source,
            observed_at=now,
            raw_payload_hash=hashlib.sha256(source.encode()).hexdigest(),
        )

    assert _max_matched_size(conn, command_id) == Decimal("6")

    row = conn.execute(
        "SELECT amount, converted_amount, released_at FROM collateral_reservations WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row[0] == 5_000_000
    assert row[1] == 0
    assert row[2] is None
    unsettled_count = conn.execute("SELECT COUNT(*) FROM collateral_unsettled_proceeds").fetchone()[0]
    assert unsettled_count == 0


def test_cas_concurrent_reserve_stress_zero_overreserve(tmp_path):
    """CONCURRENCY PROOF (acceptance, LIVE topology per critic ruling 6): each
    of >=20 threads on its OWN connection performs a command-row INSERT
    (write lock) THEN the CAS reserve on the SAME conn, against one shared
    bounded balance. Zero over-reserve at any commit point; every contended
    failure surfaces as CollateralInsufficient, NEVER OperationalError.
    """
    import threading

    from src.state.db import init_schema

    db_path = tmp_path / "cas_stress.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row
    init_schema(setup_conn)
    init_collateral_schema(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    ledger_seed = CollateralLedger(db_path=db_path)
    ledger_seed.set_snapshot(_snapshot(pusd=100_000_000))
    ledger_seed.close()

    n_threads = 25
    reserve_amount = 5_000_000  # exactly 20 of 25 can succeed against 100M

    results: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        conn_t = sqlite3.connect(db_path, timeout=30)
        conn_t.row_factory = sqlite3.Row
        conn_t.execute("PRAGMA journal_mode=WAL")
        conn_t.execute("PRAGMA busy_timeout=30000")
        command_id = f"stress-cmd-{i}"
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn_t.execute(
                """
                INSERT INTO venue_commands (
                    command_id, snapshot_id, envelope_id, position_id, decision_id,
                    idempotency_key, intent_kind, market_id, token_id, side, size, price,
                    state, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'INTENT_CREATED',?,?)
                """,
                (
                    command_id, f"snap-{i}", f"env-{i}", f"pos-{i}", f"dec-{i}", f"idem-{i}",
                    "ENTRY", "z4-market", YES_TOKEN, "BUY", 10.0, 0.5, now, now,
                ),
            )
            try:
                CollateralLedger._cas_insert_pusd_reservation(conn_t, command_id, reserve_amount, now)
                conn_t.commit()
                with lock:
                    results.append(("ok", command_id))
            except CollateralInsufficient:
                conn_t.rollback()
                with lock:
                    results.append(("insufficient", command_id))
        except Exception as exc:  # noqa: BLE001 — captured for the assertion below
            with lock:
                errors.append((type(exc).__name__, str(exc)))
        finally:
            conn_t.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"non-CollateralInsufficient errors under contention: {errors}"
    ok_count = sum(1 for outcome, _ in results if outcome == "ok")
    insufficient_count = sum(1 for outcome, _ in results if outcome == "insufficient")
    assert ok_count + insufficient_count == n_threads
    assert ok_count == 20, f"expected exactly 20 successes (100M/5M), got {ok_count}"

    verify_conn = sqlite3.connect(db_path)
    total_reserved = verify_conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations WHERE released_at IS NULL"
    ).fetchone()[0]
    live_count = verify_conn.execute(
        "SELECT COUNT(*) FROM collateral_reservations WHERE released_at IS NULL"
    ).fetchone()[0]
    verify_conn.close()
    assert total_reserved == ok_count * reserve_amount
    assert total_reserved <= 100_000_000
    assert live_count == ok_count


def test_cas_concurrent_reserve_fill_cancel_settle_holds_a4_identity(tmp_path):
    """R0-b acceptance (order-engine W1 / design doc A4 tripwire, §4 table):

        free + reserved + holdings_basis + unsettled == bankroll
        (never negative free under pending races)

    LIVE topology (own sqlite3 connection per thread, per the 25-thread
    reserve-only stress test above) but exercising the FULL lifecycle mix —
    reserve / fill / partial-then-cancel / zero-fill-cancel — against ONE
    shared bankroll, then checked at TWO quiescent points (round 1 join,
    round 2 join) plus a post-settle third point, rather than a single final
    snapshot.

    holdings_basis == 0 throughout (PUSD_BUY side only, no CTF legs), so the
    identity collapses to: bankroll == free + reserved_open + unsettled_outgoing.

    NOTE (honest pre-change disclosure): this worktree's base HEAD already
    contains commit c7e095ee1 ("CAS reservation ledger with convert-on-fill
    and type-aware A4 identity", 2026-07-02), which closed the check-then-
    insert TOCTOU this test targets. This test therefore PASSES on both the
    worktree's pre-change and post-change HEAD — it cannot be made to FAIL
    pre-change because the fix predates this packet's starting point. The
    TOCTOU characterization (SELECT preflight then a separate INSERT with no
    wrapping transaction, so concurrent connections could both pass preflight
    and both insert) is preserved verbatim in that commit's message and in
    the superseded parent revision (818a88e44) of reserve_pusd_for_buy.
    """
    import random
    import threading

    from src.state.db import init_schema
    from src.state.collateral_ledger import (
        convert_reservation_on_fill,
        release_reservation_for_command_state,
    )

    db_path = tmp_path / "cas_lifecycle_stress.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row
    init_schema(setup_conn)
    init_collateral_schema(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    BANKROLL_MICRO = 300_000_000  # 300 pUSD
    RESERVE_MICRO = 5_000_000  # 5 pUSD/command -> up to 60 concurrent grants
    ORDER_SIZE = 10.0
    ORDER_PRICE = 0.5  # notional = 5_000_000 micro, matches RESERVE_MICRO exactly

    ledger_seed = CollateralLedger(db_path=db_path)
    ledger_seed.set_snapshot(_snapshot(pusd=BANKROLL_MICRO))
    ledger_seed.close()

    def _quiescent_identity(bankroll_micro: int) -> dict[str, int]:
        vconn = sqlite3.connect(db_path)
        reserved_open = vconn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations "
            "WHERE reservation_type='PUSD_BUY' AND released_at IS NULL"
        ).fetchone()[0]
        unsettled_outgoing = vconn.execute(
            "SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds "
            "WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL"
        ).fetchone()[0]
        vconn.close()
        free = bankroll_micro - reserved_open - unsettled_outgoing
        return {
            "reserved_open": int(reserved_open),
            "unsettled_outgoing": int(unsettled_outgoing),
            "free": int(free),
        }

    def _run_round(round_no: int, n_threads: int) -> None:
        outcomes = ["fill", "partial_cancel", "zero_cancel"]
        results: list[str] = []
        errors: list[tuple[str, str]] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            command_id = f"lifecycle-r{round_no}-{i}"
            outcome = outcomes[i % len(outcomes)]
            conn_t = sqlite3.connect(db_path, timeout=30)
            conn_t.row_factory = sqlite3.Row
            conn_t.execute("PRAGMA journal_mode=WAL")
            conn_t.execute("PRAGMA busy_timeout=30000")
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn_t.execute(
                    """
                    INSERT INTO venue_commands (
                        command_id, snapshot_id, envelope_id, position_id, decision_id,
                        idempotency_key, intent_kind, market_id, token_id, side, size, price,
                        state, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'INTENT_CREATED',?,?)
                    """,
                    (
                        command_id, f"snap-{command_id}", f"env-{command_id}", f"pos-{command_id}",
                        f"dec-{command_id}", f"idem-{command_id}", "ENTRY", "z4-market",
                        YES_TOKEN, "BUY", ORDER_SIZE, ORDER_PRICE, now, now,
                    ),
                )
                try:
                    CollateralLedger._cas_insert_pusd_reservation(conn_t, command_id, RESERVE_MICRO, now)
                except CollateralInsufficient:
                    conn_t.rollback()
                    with lock:
                        results.append("insufficient")
                    return
                conn_t.commit()

                if outcome == "fill":
                    conn_t.execute(
                        """
                        INSERT INTO venue_order_facts (
                            venue_order_id, command_id, state, remaining_size, matched_size,
                            source, observed_at, local_sequence, raw_payload_hash
                        ) VALUES (?, ?, 'MATCHED', '0', ?, 'WS_USER', ?, 0, ?)
                        """,
                        (
                            f"vo-{command_id}", command_id, str(ORDER_SIZE), now,
                            hashlib.sha256(command_id.encode()).hexdigest(),
                        ),
                    )
                    convert_reservation_on_fill(conn_t, command_id, "FILLED")
                elif outcome == "partial_cancel":
                    matched = ORDER_SIZE * 0.4
                    conn_t.execute(
                        """
                        INSERT INTO venue_order_facts (
                            venue_order_id, command_id, state, remaining_size, matched_size,
                            source, observed_at, local_sequence, raw_payload_hash
                        ) VALUES (?, ?, 'PARTIALLY_MATCHED', ?, ?, 'WS_USER', ?, 0, ?)
                        """,
                        (
                            f"vo-{command_id}", command_id, str(ORDER_SIZE - matched), str(matched), now,
                            hashlib.sha256((command_id + "p").encode()).hexdigest(),
                        ),
                    )
                    convert_reservation_on_fill(conn_t, command_id, "CANCELLED")
                else:
                    release_reservation_for_command_state(conn_t, command_id, "REJECTED")
                conn_t.commit()
                with lock:
                    results.append(outcome)
            except Exception as exc:  # noqa: BLE001 — captured for the assertion below
                with lock:
                    errors.append((type(exc).__name__, str(exc)))
            finally:
                conn_t.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        random.Random(round_no).shuffle(threads)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"round {round_no}: non-CollateralInsufficient errors under contention: {errors}"
        assert len(results) == n_threads

    # Round 1: 30 threads racing against the 300M bankroll (60 grants possible
    # at 5M each) mixed with fill/partial-cancel/zero-cancel outcomes.
    _run_round(1, 30)
    q1 = _quiescent_identity(BANKROLL_MICRO)
    assert q1["free"] >= 0, f"round 1 quiescent free went negative: {q1}"
    assert q1["reserved_open"] == 0, (
        "every round-1 command reached a terminal outcome (fill/cancel), so no "
        f"open reservations should remain: {q1}"
    )
    # A4 identity (holdings_basis == 0, cash-only slice):
    assert BANKROLL_MICRO == q1["free"] + q1["reserved_open"] + q1["unsettled_outgoing"]

    # Round 2: reuse the SAME bankroll snapshot (still unsettled from round 1)
    # — proves capital released/converted in round 1 is correctly reflected
    # (not leaked, not double-counted) before any balance-refresh settle.
    _run_round(2, 30)
    q2 = _quiescent_identity(BANKROLL_MICRO)
    assert q2["free"] >= 0, f"round 2 quiescent free went negative: {q2}"
    assert q2["reserved_open"] == 0
    assert BANKROLL_MICRO == q2["free"] + q2["reserved_open"] + q2["unsettled_outgoing"]
    # Round 2's unsettled_outgoing must be >= round 1's — conversions only
    # accumulate until a balance-refresh settle clears matured rows.
    assert q2["unsettled_outgoing"] >= q1["unsettled_outgoing"]

    # Settle: simulate the venue balance catching up to every converted fill
    # (a balance-refresh snapshot captured after CLOCK_SKEW clears matured
    # unsettled rows, per _clear_matured_unsettled_proceeds).
    settle_conn = sqlite3.connect(db_path)
    settle_conn.row_factory = sqlite3.Row
    total_converted = settle_conn.execute(
        "SELECT COALESCE(SUM(converted_amount),0) FROM collateral_reservations"
    ).fetchone()[0]
    settle_conn.close()
    new_bankroll = BANKROLL_MICRO - int(total_converted)
    from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS

    future = datetime.now(timezone.utc) + timedelta(seconds=COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS + 300)
    ledger_settle = CollateralLedger(db_path=db_path)
    ledger_settle.set_snapshot(_snapshot(pusd=new_bankroll, captured_at=future))
    ledger_settle.close()

    q3 = _quiescent_identity(new_bankroll)
    assert q3["unsettled_outgoing"] == 0, f"settle should clear all matured unsettled rows: {q3}"
    assert q3["free"] >= 0
    assert new_bankroll == q3["free"] + q3["reserved_open"] + q3["unsettled_outgoing"]
    assert q3["free"] == new_bankroll  # nothing left in flight after settle
