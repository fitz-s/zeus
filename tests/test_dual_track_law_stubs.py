# Created: 2026-04-16
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Enforcement stubs for Dual-Track Metric Spine invariants (INV-18..INV-22)
and negative constraints (NC-11..NC-15).

Each test is a skeleton that skips with a message indicating which Phase will
activate the real enforcement. When the enforcement work lands, replace the
pytest.skip() body with the actual assertion.
"""
from __future__ import annotations

import pytest


# NC-11 / INV-14
def test_settlements_metric_identity_requires_non_null_and_unique_per_metric():
    """Settlements require explicit metric identity and unique city/date/metric.

    Verifies:
    1. apply_v2_schema creates the v2 tables in a fresh :memory: DB.
    2. Missing temperature_metric is rejected.
    3. Duplicate high rows for one city/date are rejected, while a low row for
       the same city/date remains representable under the dual-track spine.
    """
    import sqlite3
    from src.state.schema.v2_schema import apply_v2_schema
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply legacy + v2 schema
    init_schema(conn)

    # V2 tables must exist
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for v2_table in [
        "settlements_v2", "market_events_v2", "ensemble_snapshots_v2",
        "calibration_pairs_v2", "platt_models_v2", "observation_instants_v2",
        "historical_forecasts_v2", "day0_metric_fact",
    ]:
        assert v2_table in tables, f"v2 table {v2_table!r} must exist after init_schema"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority) "
            "VALUES ('NYC', '2026-04-16', 'UNVERIFIED')"
        )

    conn.execute(
        """
        INSERT INTO settlements
        (city, target_date, authority, temperature_metric,
         physical_quantity, observation_field, data_version)
        VALUES ('NYC', '2026-04-16', 'UNVERIFIED', 'high',
                'mx2t6_local_calendar_day_max', 'high_temp',
                'tigge_mx2t6_local_calendar_day_max_v1')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO settlements
            (city, target_date, authority, temperature_metric,
             physical_quantity, observation_field, data_version)
            VALUES ('NYC', '2026-04-16', 'UNVERIFIED', 'high',
                    'mx2t6_local_calendar_day_max', 'high_temp',
                    'tigge_mx2t6_local_calendar_day_max_v1')
            """
        )

    conn.execute(
        """
        INSERT INTO settlements
        (city, target_date, authority, temperature_metric,
         physical_quantity, observation_field, data_version)
        VALUES ('NYC', '2026-04-16', 'UNVERIFIED', 'low',
                'mn2t6_local_calendar_day_min', 'low_temp',
                'tigge_mn2t6_local_calendar_day_min_v1')
        """
    )


# NC-12 / INV-16
def test_no_high_low_mix_in_platt_or_bins():
    """NC-12: No mixing of high and low rows in Platt model, calibration pair set, bin lookup, or settlement identity."""
    pytest.skip("pending: enforced in Phase 7 rebuild")


# NC-13 / INV-17
def test_json_export_after_db_commit():
    """NC-13 / INV-17: JSON export writes must occur only after the corresponding DB commit returns.

    Uses commit_then_export to verify:
    1. Normal path: db_op fires, commit happens, json_export fires in order.
    2. Crash path: db_op raises → json_export never fires, DB has no partial row.
    """
    import sqlite3
    from src.state.canonical_write import commit_then_export

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE test_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn.execute("CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)")
    conn.commit()

    call_log: list[str] = []

    def db_op():
        conn.execute("INSERT INTO test_artifacts (v) VALUES ('x')")
        call_log.append("db_op")

    def json_export():
        call_log.append("json_export")

    commit_then_export(conn, db_op=db_op, json_exports=[json_export])

    assert "db_op" in call_log
    assert "json_export" in call_log
    assert call_log.index("db_op") < call_log.index("json_export"), (
        "json_export must be called AFTER db_op (NC-13 / INV-17)"
    )

    # Crash path: db_op raises → no json_export, no partial row
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE test_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn2.commit()

    json_called = []

    def bad_db_op():
        conn2.execute("INSERT INTO test_artifacts (v) VALUES ('partial')")
        raise RuntimeError("simulated failure")

    try:
        commit_then_export(conn2, db_op=bad_db_op, json_exports=[lambda: json_called.append(True)])
    except RuntimeError:
        pass

    assert not json_called, "json_export must NOT fire when db_op raises (NC-13)"
    (count,) = conn2.execute("SELECT COUNT(*) FROM test_artifacts").fetchone()
    assert count == 0, "DB must have no partial row after db_op failure"


# NC-14 / INV-21 / DT#5 — STRICT Phase 10E (R-BW flipped)
def test_kelly_input_carries_distributional_info():
    """NC-14 / INV-21 / DT#5: kelly_size() STRICTLY requires ExecutionPrice.

    Phase 10E (STRICT 2026-04-20):
      - kelly_size() entry_price MUST be a typed ExecutionPrice;
      - bare float is rejected (TypeError — no bare-float path exists);
      - non-compliant ExecutionPrice raises ExecutionPriceContractError;
      - compliant ExecutionPrice returns a positive Kelly size.

    Antibody shape: BC path now raises TypeError (strict-reject antibody).
    """
    from src.strategy.kelly import kelly_size
    from src.contracts.execution_price import (
        ExecutionPrice,
        ExecutionPriceContractError,
    )

    # (a) Strict: bare float is REJECTED — no bare-float path survives P10E
    with pytest.raises((TypeError, AttributeError), match="assert_kelly_safe|NoneType|float"):
        kelly_size(
            p_posterior=0.60,
            entry_price=0.40,
            bankroll=1000.0,
            kelly_mult=0.25,
        )

    # (b) Non-compliant ExecutionPrice: implied_probability (DT#5 law violation)
    unsafe = ExecutionPrice(
        value=0.50,
        price_type="implied_probability",
        fee_deducted=False,
        currency="probability_units",
    )
    with pytest.raises(ExecutionPriceContractError):
        kelly_size(
            p_posterior=0.60,
            entry_price=unsafe,
            bankroll=1000.0,
            kelly_mult=0.25,
        )

    # (c) Compliant ExecutionPrice: fee_adjusted + fee_deducted + probability_units
    safe = ExecutionPrice(
        value=0.40,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )
    safe_size = kelly_size(
        p_posterior=0.60,
        entry_price=safe,
        bankroll=1000.0,
        kelly_mult=0.25,
    )
    assert safe_size > 0.0, (
        f"R-BW (c): compliant ExecutionPrice must produce positive Kelly size; "
        f"got {safe_size}"
    )


# NC-15 / INV-22
def test_fdr_family_key_is_canonical():
    """NC-15 / INV-22: Phase 1 enforcement — scope-aware FDR family grammar.

    make_hypothesis_family_id and make_edge_family_id produce distinct IDs for
    the same candidate inputs, and both are deterministic within their scope.
    This prevents BH discovery budgets from silently merging across scopes.
    """
    from src.strategy.selection_family import (
        make_hypothesis_family_id,
        make_edge_family_id,
    )

    cand = dict(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-04-01",
        temperature_metric="high",
        discovery_mode="opening_hunt",
        decision_snapshot_id="snap-1",
    )
    h_id = make_hypothesis_family_id(**cand)
    e_id = make_edge_family_id(**cand, strategy_key="center_buy")

    # Scope separation: same candidate inputs must produce different IDs
    assert h_id != e_id, "hypothesis and edge family IDs must differ for same candidate inputs"

    # Determinism within each scope
    assert h_id == make_hypothesis_family_id(**cand), "hypothesis family ID must be deterministic"
    assert e_id == make_edge_family_id(**cand, strategy_key="center_buy"), "edge family ID must be deterministic"

    # R-CO.1 S4 R9 P10B: metric-discriminating assertion — same other args, different metric → different ID
    cand_low = dict(cand, temperature_metric="low")
    h_id_high = make_hypothesis_family_id(**cand)
    h_id_low = make_hypothesis_family_id(**cand_low)
    assert h_id_high != h_id_low, "family_id must discriminate by metric: HIGH != LOW"


# INV-19 / DT#2 — ACTIVATED Phase 9B (R-BV)
def test_red_triggers_active_position_sweep(monkeypatch):
    """INV-19 / DT#2: RED risk level must sweep active positions toward exit;
    entry-block-only RED scope is forbidden.

    Phase 9B (ACTIVATED 2026-04-18):
      - cycle_runner._execute_force_exit_sweep(portfolio) marks all active,
        non-terminal, not-already-exiting positions with
        exit_reason="red_force_exit";
      - exit_lifecycle machinery picks up on next monitor_refresh cycle and
        posts sell orders (not in-cycle — low-risk + testable sweep).

    This antibody pair tests:
      (positive) 3 active positions → 3 marked with "red_force_exit"
      (negative) terminal positions skipped; already-exiting preserved
    """
    from src.engine.cycle_runner import _execute_force_exit_sweep
    from src.state.portfolio import PortfolioState, Position

    # Build a portfolio with mixed states
    active_pos_1 = Position(
        trade_id="t1", market_id="m1", city="NYC", cluster="US-Northeast",
        target_date="2026-04-10", bin_label="50-51°F", direction="buy_yes",
        unit="F", state="holding", exit_reason="",
    )
    active_pos_2 = Position(
        trade_id="t2", market_id="m2", city="LA", cluster="US-West",
        target_date="2026-04-11", bin_label="70-71°F", direction="buy_yes",
        unit="F", state="holding", exit_reason="",
    )
    already_exiting = Position(
        trade_id="t3", market_id="m3", city="CHI", cluster="US-Midwest",
        target_date="2026-04-12", bin_label="40-41°F", direction="buy_yes",
        unit="F", state="holding",
        exit_reason="SLO_TRIGGER",  # different flow; must NOT override
    )
    settled_pos = Position(
        trade_id="t4", market_id="m4", city="SF", cluster="US-West",
        target_date="2026-04-09", bin_label="60-61°F", direction="buy_yes",
        unit="F", state="settled", exit_reason="SETTLEMENT",
    )

    portfolio = PortfolioState(
        positions=[active_pos_1, active_pos_2, already_exiting, settled_pos],
    )

    # (positive + negative combined) Sweep runs once, reports accurate counts
    result = _execute_force_exit_sweep(portfolio)

    assert result["attempted"] == 2, (
        f"R-BV: two active positions without existing exit flow should be "
        f"swept; got {result!r}"
    )
    assert result["already_exiting"] == 1, (
        f"R-BV: one position with pre-existing exit_reason should be "
        f"preserved (not overwritten); got {result!r}"
    )
    assert result["skipped_terminal"] == 1, (
        f"R-BV: settled position should be skipped; got {result!r}"
    )

    # Verify actual state mutations (not just the summary)
    assert active_pos_1.exit_reason == "red_force_exit", (
        f"R-BV: active_pos_1 must carry sweep exit_reason; got "
        f"{active_pos_1.exit_reason!r}"
    )
    assert active_pos_2.exit_reason == "red_force_exit"
    assert already_exiting.exit_reason == "SLO_TRIGGER", (
        f"R-BV: already_exiting.exit_reason must NOT be overwritten by sweep; "
        f"got {already_exiting.exit_reason!r}"
    )
    assert settled_pos.exit_reason == "SETTLEMENT", (
        f"R-BV: settled_pos.exit_reason must NOT be overwritten; got "
        f"{settled_pos.exit_reason!r}"
    )


# DT#2 — Phase 9B ITERATE resolution (R-BY relationship antibody)
def test_red_force_exit_marker_drives_evaluate_exit_to_exit():
    """DT#2 / INV-19 relationship antibody (R-BY, critic-carol cycle-3 fix):

    R-BV (sweep mechanism) only verified that `_execute_force_exit_sweep`
    writes `exit_reason="red_force_exit"` on positions. Critic-carol cycle 3
    surfaced CRITICAL-1: the marker was INERT — no runtime consumer read it.
    P9B ITERATE-fix wires the marker into `evaluate_exit`: when a position
    carries exit_reason="red_force_exit" AND exit_context is NOT
    day0_active, evaluate_exit short-circuits normal edge evaluation and
    returns ExitDecision(should_exit=True, trigger="RED_FORCE_EXIT").

    This is a RELATIONSHIP antibody per Fitz methodology: tests the
    cross-module invariant that cycle_runner's sweep output flows correctly
    into portfolio.evaluate_exit's decision surface. Function-level tests
    alone could not detect the inert-marker pathology (critic-carol cycle-3
    L9 runtime-probe learning).
    """
    from src.state.portfolio import Position, ExitContext

    # GIVEN a sweep-marked position (as left by _execute_force_exit_sweep)
    marked_pos = Position(
        trade_id="t1", market_id="m1", city="NYC", cluster="US-Northeast",
        target_date="2026-04-15", bin_label="50-51°F", direction="buy_yes",
        unit="F", state="holding",
        exit_reason="red_force_exit",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60, shares=25.0,
        cost_basis_usd=10.0,
    )

    # AND a HEALTHY ExitContext (not day0-active; all authority fields
    # present) — pre-fix, the marker was silently ignored
    healthy_context = ExitContext(
        fresh_prob=0.60,
        fresh_prob_is_fresh=True,
        current_market_price=0.40,
        current_market_price_is_fresh=True,
        best_bid=0.39,
        best_ask=0.41,
        market_vig=0.02,
        hours_to_settlement=12.0,
        position_state="holding",
        day0_active=False,
        whale_toxicity=False,
        chain_is_fresh=True,
    )

    # WHEN evaluate_exit runs
    decision = marked_pos.evaluate_exit(healthy_context)

    # THEN should_exit=True with RED_FORCE_EXIT trigger
    assert decision.should_exit is True, (
        f"R-BY: sweep-marked position + healthy ExitContext must yield "
        f"should_exit=True. Got should_exit={decision.should_exit}, "
        f"reason={decision.reason!r}, trigger={decision.trigger!r}. "
        f"Pre-fix: marker was inert (decision.should_exit=False because "
        f"normal edge evaluation ran and found no exit trigger). Post-fix: "
        f"evaluate_exit short-circuits on exit_reason='red_force_exit'."
    )
    assert decision.trigger == "RED_FORCE_EXIT", (
        f"R-BY: trigger must be RED_FORCE_EXIT; got {decision.trigger!r}"
    )
    assert decision.urgency == "immediate", (
        f"R-BY: urgency must be 'immediate' on RED force exit; "
        f"got {decision.urgency!r}"
    )
    assert "dt2_red_force_exit_sweep_actuated" in decision.applied_validations, (
        f"R-BY: applied_validations must trace the DT#2 actuator; got "
        f"{decision.applied_validations!r}"
    )


# DT#2 — Day0 exclusion antibody (R-BY.2 pair-negative)
def test_red_force_exit_marker_does_not_override_day0_evaluation():
    """DT#2 boundary clause (R-BY.2): Day0 positions have their own risk-
    containment path (nowcast + causality) and must NOT be short-circuited
    by the RED force-exit branch. Critic-carol cycle-3 fix preserves Day0
    evaluator semantics by gating the short-circuit on `not day0_active`.

    Pair-negative antibody to R-BY (critic-carol cycle-1 L7 / cycle-2
    paired-antibody pattern): both false-negative (marker fails to fire
    when it should) AND false-positive (marker fires when it shouldn't)
    must be locked.
    """
    from src.state.portfolio import Position, ExitContext

    marked_pos = Position(
        trade_id="t2", market_id="m2", city="LA", cluster="US-West",
        target_date="2026-04-16", bin_label="70-71°F", direction="buy_yes",
        unit="F", state="holding",
        exit_reason="red_force_exit",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60, shares=25.0,
        cost_basis_usd=10.0,
    )

    day0_context = ExitContext(
        fresh_prob=0.60,
        fresh_prob_is_fresh=True,
        current_market_price=0.40,
        current_market_price_is_fresh=True,
        best_bid=0.39,
        best_ask=0.41,
        market_vig=0.02,
        hours_to_settlement=2.0,
        position_state="holding",
        day0_active=True,  # <-- Day0 path; must NOT be short-circuited
        whale_toxicity=False,
        chain_is_fresh=True,
    )

    decision = marked_pos.evaluate_exit(day0_context)

    # Day0 position SHOULD NOT short-circuit on the RED marker — its own
    # nowcast/causality logic must run. The decision may or may not exit
    # depending on Day0 logic, but the trigger must NOT be RED_FORCE_EXIT.
    assert decision.trigger != "RED_FORCE_EXIT", (
        f"R-BY.2: Day0 position must NOT be short-circuited by RED marker; "
        f"got trigger={decision.trigger!r}. The day0_active gate on the "
        f"DT#2 branch has regressed."
    )
    assert "dt2_red_force_exit_sweep_actuated" not in decision.applied_validations


# DT#2 — Phase 9C strengthening of R-BY.2 (critic-carol cycle-3 L15 asymmetric
# discrimination observation): the original R-BY.2 fixture had an asymmetric
# blind spot — it caught Day0→RED misrouting but did NOT catch the inverse
# case where a Day0 position WITHOUT the red_force_exit marker should run
# its own Day0 logic unchanged. This paired-negative extension locks it.
def test_day0_without_red_marker_runs_day0_logic_normally():
    """R-BY.2 strengthening (Phase 9C C2): Day0 position without the red
    marker must go through the normal Day0 evaluator path (not short-
    circuited to RED_FORCE_EXIT, not stuck returning None). Completes the
    paired antibody symmetry per critic-carol cycle-3 L15 + cycle-1 L7
    paired-antibody pattern."""
    from src.state.portfolio import Position, ExitContext

    # Position WITHOUT red marker (exit_reason="") — normal active position
    normal_pos = Position(
        trade_id="t3", market_id="m3", city="Boston", cluster="US-Northeast",
        target_date="2026-04-17", bin_label="45-46°F", direction="buy_yes",
        unit="F", state="holding",
        exit_reason="",  # NO red marker — must go through normal Day0 logic
        size_usd=10.0, entry_price=0.40, p_posterior=0.60, shares=25.0,
        cost_basis_usd=10.0,
    )
    # Day0-active ExitContext with all fields populated
    day0_context = ExitContext(
        fresh_prob=0.60,
        fresh_prob_is_fresh=True,
        current_market_price=0.40,
        current_market_price_is_fresh=True,
        best_bid=0.39,
        best_ask=0.41,
        market_vig=0.02,
        hours_to_settlement=2.0,
        position_state="holding",
        day0_active=True,
        whale_toxicity=False,
        chain_is_fresh=True,
    )
    decision = normal_pos.evaluate_exit(day0_context)
    # Must NOT short-circuit to RED — no marker was set
    assert decision.trigger != "RED_FORCE_EXIT", (
        f"R-BY.2 strengthened: without red marker, Day0 position must not "
        f"short-circuit to RED_FORCE_EXIT; got trigger={decision.trigger!r}"
    )
    # Day0 path must have executed (observable via applied_validations)
    assert "day0_observation_authority" in decision.applied_validations, (
        f"R-BY.2 strengthened: Day0 path must execute for unmarked day0 "
        f"position; applied_validations={decision.applied_validations!r}"
    )


# DT#7 — NEW Phase 9B (R-BX)
def test_boundary_ambiguous_refuses_signal_contract():
    """DT#7 clause 3: boundary_ambiguous_refuses_signal() must return True
    when the snapshot is flagged boundary-ambiguous; callers use the return
    to refuse the candidate as confirmatory signal.

    Phase 9B scope delivers the contract function at
    src/contracts/boundary_policy.py; evaluator wiring is P9C (blocks on
    monitor_refresh LOW data flow per critic-carol cycle-2 forward-log).

    Law: docs/authority/zeus_current_architecture.md §22 +
    docs/authority/zeus_dual_track_architecture.md §DT#7.
    """
    from src.contracts.boundary_policy import boundary_ambiguous_refuses_signal

    # (positive) flagged snapshot → refuse
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": True}) is True, (
        "R-BX: boundary_ambiguous=True snapshot must refuse signal"
    )

    # (negative) unflagged snapshot → permit
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": False}) is False, (
        "R-BX: boundary_ambiguous=False snapshot must permit signal"
    )

    # (absence-is-permissive) missing key → permit (safe fallback during
    # transition when boundary_ambiguous plumbing may be partial)
    assert boundary_ambiguous_refuses_signal({}) is False, (
        "R-BX: absent boundary_ambiguous key must permit (safe default)"
    )

    # (bool coercion) truthy non-bool values refuse
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": 1}) is True
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": "truthy"}) is True

    # (bool coercion) falsy non-bool values permit
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": 0}) is False
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": ""}) is False
    assert boundary_ambiguous_refuses_signal({"boundary_ambiguous": None}) is False


# INV-18
def test_chain_reconciliation_three_state_machine():
    """INV-18: Chain reconciliation state is three-valued (CHAIN_SYNCED / CHAIN_EMPTY / CHAIN_UNKNOWN); void decisions require CHAIN_EMPTY, not CHAIN_UNKNOWN."""
    from dataclasses import dataclass, field as dc_field
    from datetime import datetime, timezone, timedelta
    from typing import List
    from src.state.chain_state import ChainState, classify_chain_state

    @dataclass
    class _Pos:
        state: str = "holding"
        chain_verified_at: str = ""
        token_id: str = "tok-1"
        no_token_id: str = ""
        direction: str = "buy_yes"

    @dataclass
    class _Portfolio:
        positions: List[_Pos] = dc_field(default_factory=list)

    fresh = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    # Row 1: non-empty chain → SYNCED
    class _CP:
        token_id = "tok-1"

    result = classify_chain_state(
        fetched_at=fresh, chain_positions=[_CP()], portfolio=_Portfolio()
    )
    assert result == ChainState.CHAIN_SYNCED

    # Row 2: empty chain + stale verified_at → EMPTY (void allowed)
    result = classify_chain_state(
        fetched_at=fresh,
        chain_positions=[],
        portfolio=_Portfolio(positions=[_Pos(chain_verified_at=stale)]),
    )
    assert result == ChainState.CHAIN_EMPTY
    assert result != ChainState.CHAIN_UNKNOWN, "CHAIN_EMPTY must open the void gate"

    # Row 3: empty chain + recent verified_at → UNKNOWN (void blocked)
    result = classify_chain_state(
        fetched_at=fresh,
        chain_positions=[],
        portfolio=_Portfolio(positions=[_Pos(chain_verified_at=recent)]),
    )
    assert result == ChainState.CHAIN_UNKNOWN
    assert result != ChainState.CHAIN_EMPTY, "CHAIN_UNKNOWN must keep void gate closed"

    # Row 4: no fetched_at → UNKNOWN regardless
    result = classify_chain_state(
        fetched_at=None,
        chain_positions=[_CP()],
        portfolio=_Portfolio(),
    )
    assert result == ChainState.CHAIN_UNKNOWN


# INV-20
def test_load_portfolio_degrades_gracefully_on_authority_loss(monkeypatch):
    """INV-20: Authority-loss must preserve monitor/exit/reconciliation paths in read-only mode; RuntimeError that kills the full cycle on authority-loss is forbidden."""
    import sqlite3
    import src.state.db as db_module
    from src.state import portfolio as portfolio_module
    from src.state.portfolio_loader_policy import LoaderPolicyDecision

    def _degraded_policy(snapshot_status, **kwargs):
        # Simulate auth-loss: return a non-canonical truth source policy
        return LoaderPolicyDecision(
            source="json_fallback",
            reason="test: authority-loss simulation (INV-20)",
            escalate=True,
        )

    monkeypatch.setattr(portfolio_module, "choose_portfolio_truth_source", _degraded_policy)

    # Provide an in-memory DB so DB connection succeeds, but query returns degraded snapshot
    _mem_conn = sqlite3.connect(":memory:")
    _mem_conn.row_factory = sqlite3.Row

    def _fake_get_connection(*args, **kwargs):
        return _mem_conn

    def _fake_get_trade_connection_with_world(*args, **kwargs):
        return _mem_conn

    def _fake_query_portfolio_loader_view(conn, **kwargs):
        return {"status": "degraded_test", "positions": [], "table": "position_current"}

    def _fake_query_token_suppression_tokens(conn):
        return []

    def _fake_query_chain_only_quarantine_rows(conn, **kwargs):
        return []

    monkeypatch.setattr(db_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(db_module, "get_trade_connection_with_world", _fake_get_trade_connection_with_world)
    monkeypatch.setattr(db_module, "query_portfolio_loader_view", _fake_query_portfolio_loader_view)
    monkeypatch.setattr(db_module, "query_token_suppression_tokens", _fake_query_token_suppression_tokens)
    monkeypatch.setattr(db_module, "query_chain_only_quarantine_rows", _fake_query_chain_only_quarantine_rows)
    monkeypatch.setattr(portfolio_module, "_guard_deprecated_portfolio_json", lambda p: None)

    state = portfolio_module.load_portfolio()
    assert state.authority in ("degraded", "unverified"), (
        f"INV-20: load_portfolio must degrade not raise on auth-loss. "
        f"Got authority={state.authority!r}"
    )
