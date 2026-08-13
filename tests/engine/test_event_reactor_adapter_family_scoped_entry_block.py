# Created: 2026-07-25
# Last reused or audited: 2026-08-13
# Lifecycle: created=2026-07-25; last_reviewed=2026-08-13; last_reused=2026-08-13
# Authority basis: 7-day production block-event audit -- one stuck EDLI order
#   was blocking new-entry BUY admission for every family (32,763 blocking
#   instances, 20.97h/7d). This narrows the adapter-level gate
#   (event_bound_live_adapter_from_trade_conn's entry_submit_block_reason
#   check) to per-family.
"""Adapter-level per-family BUY entry block.

``event_bound_live_adapter_from_trade_conn`` gained a new
``entry_submit_family_block_reasons: Mapping[str, str]`` parameter alongside
the pre-existing (still-global) ``entry_submit_block_reason``. These tests
confirm: a BUY event whose own weather family_id (derived from its payload
city/target_date/metric via the same ``weather_family_id`` identity persisted
on SubmitPlanBuilt) is in the blocked map is refused; an unrelated family's
BUY event is NOT blocked by that map; and a SELL/exit candidate keeps its
pre-existing bypass ahead of both block checks.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.engine.event_reactor_adapter as era
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

FAMILY_A = weather_family_id(city="Dallas", target_date="2026-07-25", metric="high")
FAMILY_B = weather_family_id(city="Miami", target_date="2026-07-25", metric="low")


def _make_event(
    *,
    city: str,
    target_date: str,
    metric: str,
    event_id: str = "evt-1",
    event_type: str = "FORECAST_SNAPSHOT_READY",
) -> OpportunityEvent:
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
    }
    return OpportunityEvent(
        event_id=event_id,
        event_type=event_type,
        entity_key=f"{city}:{target_date}:{metric}",
        source="test",
        observed_at=NOW.isoformat(),
        available_at=NOW.isoformat(),
        received_at=NOW.isoformat(),
        causal_snapshot_id="snap-1",
        payload_hash="payload-hash",
        idempotency_key="idem-1",
        priority=0,
        expires_at=None,
        payload_json=json.dumps(payload),
        schema_version=1,
        created_at=NOW.isoformat(),
    )


def _make_adapter(*, entry_submit_block_reason=None, entry_submit_family_block_reasons=None):
    trade_conn = sqlite3.connect(":memory:")
    return era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
        entry_submit_block_reason=entry_submit_block_reason,
        entry_submit_family_block_reasons=entry_submit_family_block_reasons,
    )


def test_family_block_reasons_blocks_matching_family_buy_event():
    adapter = _make_adapter(
        entry_submit_family_block_reasons={
            FAMILY_A: "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1"
        }
    )
    event = _make_event(city="Dallas", target_date="2026-07-25", metric="high")

    receipt = adapter(event, NOW)

    assert receipt.submitted is False
    assert receipt.reason == (
        "LIVE_ENTRY_BLOCKED:entry_readiness_family:"
        "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1"
    )


def test_family_block_reasons_does_not_block_unrelated_family_buy_event():
    adapter = _make_adapter(
        entry_submit_family_block_reasons={
            FAMILY_A: "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1",
        }
    )
    event = _make_event(city="Miami", target_date="2026-07-25", metric="low")
    assert weather_family_id(city="Miami", target_date="2026-07-25", metric="low") == FAMILY_B
    assert FAMILY_B not in {FAMILY_A}

    receipt = adapter(event, NOW)

    # Family B is not in the blocked map, so it must fall through this gate
    # entirely and hit the next boundary check (no executor_submit configured
    # in this minimal adapter) rather than the family-block reason.
    assert receipt.submitted is False
    assert receipt.reason == "EXECUTOR_BOUNDARY_MISSING"


def test_global_block_reason_still_blocks_every_family():
    adapter = _make_adapter(
        entry_submit_block_reason="entry_readiness:EDLI_STAGE_SOURCE_HEALTH_STALE:5s",
        entry_submit_family_block_reasons={},
    )
    event = _make_event(city="Miami", target_date="2026-07-25", metric="low")

    receipt = adapter(event, NOW)

    assert receipt.submitted is False
    assert receipt.reason == "LIVE_ENTRY_BLOCKED:entry_readiness:EDLI_STAGE_SOURCE_HEALTH_STALE:5s"


def test_global_sell_candidate_check_precedes_family_block_in_source_order():
    """SELL/exit candidates must keep bypassing BOTH block checks.

    The public per-event ``_submit(event, decision_time)`` seam never carries
    a ``global_actuation`` (only the batch/global-auction path does), so a
    SELL candidate cannot be driven through this narrow adapter surface
    without reconstructing that whole pipeline. This diff does not touch the
    SELL branch or its position: ``_global_sell_candidate(...)`` is still
    checked, and returns via ``_submit_global_sell(...)``, strictly before
    both ``entry_submit_block_reason`` and the new
    ``entry_submit_family_block_reasons`` check. Assert that ordering
    directly from source so a future edit that reorders these checks fails
    this test.
    """
    import inspect

    source = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    sell_at = source.index("_global_sell_candidate(global_actuation) is not None")
    global_block_at = source.index("if entry_submit_block_reason is not None:")
    family_block_at = source.index("if entry_submit_family_block_reasons:")

    assert sell_at < global_block_at < family_block_at


def test_family_blocked_buy_is_removed_before_global_capital_ranking():
    blocked = {
        FAMILY_A: "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1",
    }
    matching_buy = SimpleNamespace(
        action="BUY",
        family_key=FAMILY_A,
    )
    sibling_buy = SimpleNamespace(
        action="BUY",
        family_key=FAMILY_B,
    )
    matching_sell = SimpleNamespace(
        action="SELL",
        family_key=FAMILY_A,
    )

    assert era._entry_family_blocked_candidate_reason(
        matching_buy,
        blocked,
    ) == (
        "LIVE_ENTRY_BLOCKED:entry_readiness_family:"
        "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"
    )
    assert era._entry_family_blocked_candidate_reason(sibling_buy, blocked) is None
    assert era._entry_family_blocked_candidate_reason(matching_sell, blocked) is None


def test_unresolved_day0_statistical_buy_is_removed_before_capital_ranking():
    reason = era._day0_unresolved_entry_probability_rejection_reason(
        day0_payoff_truth="unresolved",
    )

    assert reason == "GLOBAL_DAY0_UNRESOLVED_ENTRY_PROBABILITY_UNCALIBRATED"


def test_day0_entry_containment_preserves_hard_facts_and_non_day0_candidates():
    assert era._day0_unresolved_entry_probability_rejection_reason(
        day0_payoff_truth="locked",
    ) is None


def test_current_statistical_buy_modes_reach_strategy_and_capital_checks():
    import inspect

    source = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    strategy_at = source.index("_event_bound_strategy_key(")
    capital_at = source.index(
        "_global_current_entry_feasibility_rejection_reason(",
        strategy_at,
    )

    assert strategy_at < capital_at
    assert "GLOBAL_STATISTICAL_BUY_MAKER_FILL_ADVANTAGE_UNPROVEN" not in source
    assert (
        "GLOBAL_CURRENT_REGIME_SETTLEMENT_GRADED_CAPITAL_ADVANTAGE_UNPROVEN"
        not in source
    )
    assert era._day0_unresolved_entry_probability_rejection_reason(
        day0_payoff_truth="refuted",
    ) is None
    assert era._day0_unresolved_entry_probability_rejection_reason(
        day0_payoff_truth=None,
    ) is None


def test_current_statistical_maker_reaches_capital_policy(monkeypatch):
    from src.engine import global_batch_runtime

    captured = {}

    def fake_prepare(_event, **_kwargs):
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=FAMILY_A,
                witness_identity="current-statistical-q",
            ),
            day0_payoff_truth_by_bin_side=(),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        receipt = kwargs["prepare_event"](events[0], NOW)
        assert receipt.prepared_global_family is not None
        captured["reason"] = kwargs["candidate_policy_rejection_resolver"](
            SimpleNamespace(
                action="BUY",
                execution_mode="MAKER_REST",
                family_key=FAMILY_A,
                bin_id="bin-a",
                side="YES",
            )
        )
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        fake_prepare,
    )
    monkeypatch.setattr(
        era,
        "_entry_global_submit_suppression_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        era,
        "_edli_forecast_lane_phase_evidence",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(era, "_forecast_lane_phase_admits", lambda _evidence: True)
    monkeypatch.setattr(era, "_event_bound_strategy_key", lambda **_kwargs: "test")

    def capital_policy(candidate, **_kwargs):
        captured["candidate"] = candidate
        return None

    monkeypatch.setattr(
        era,
        "_global_current_entry_feasibility_rejection_reason",
        capital_policy,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
    )

    adapter.process_global_batch(
        (_make_event(city="Dallas", target_date="2026-07-25", metric="high"),),
        NOW,
    )

    assert captured["reason"] is None
    assert captured["candidate"].execution_mode == "MAKER_REST"


def test_unresolved_day0_containment_precedes_strategy_and_capital_checks():
    import inspect

    source = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    containment_at = source.index(
        "_day0_unresolved_entry_probability_rejection_reason("
    )
    strategy_at = source.index("_event_bound_strategy_key(", containment_at)
    capital_at = source.index(
        "_global_current_entry_feasibility_rejection_reason(",
        containment_at,
    )

    assert containment_at < strategy_at < capital_at


@pytest.mark.parametrize(
    "carrier_event_type",
    ("DAY0_EXTREME_UPDATED", "FORECAST_SNAPSHOT_READY"),
)
def test_live_global_policy_reads_prepared_day0_truth_before_selection(
    monkeypatch,
    carrier_event_type,
):
    from src.engine import global_batch_runtime

    captured = {}

    def fake_prepare(_event, **_kwargs):
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=FAMILY_A,
                witness_identity="current-day0-q",
            ),
            day0_payoff_truth_by_bin_side=(("bin-a", "YES", "unresolved"),),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        receipt = kwargs["prepare_event"](events[0], NOW)
        assert receipt.prepared_global_family is not None
        captured["reason"] = kwargs["candidate_policy_rejection_resolver"](
            SimpleNamespace(
                action="BUY",
                family_key=FAMILY_A,
                bin_id="bin-a",
                side="YES",
            )
        )
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        fake_prepare,
    )
    monkeypatch.setattr(
        era,
        "_entry_global_submit_suppression_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        era,
        "_edli_forecast_lane_phase_evidence",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        era,
        "_forecast_lane_phase_admits",
        lambda _evidence: True,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
    )

    adapter.process_global_batch(
        (
            _make_event(
                city="Dallas",
                target_date="2026-07-25",
                metric="high",
                event_type=carrier_event_type,
            ),
        ),
        NOW,
    )

    assert captured["reason"] == (
        "GLOBAL_DAY0_UNRESOLVED_ENTRY_PROBABILITY_UNCALIBRATED"
    )


def test_live_global_batch_wires_family_block_into_selection_policy(monkeypatch):
    from src.engine import global_batch_runtime

    captured = {}

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            events=tuple(events),
            winner_event_id=None,
            receipts={},
        )

    monkeypatch.setattr(
        era,
        "_entry_global_submit_suppression_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    trade_conn = sqlite3.connect(":memory:")
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
        entry_submit_family_block_reasons={
            FAMILY_A: "EDLI_STAGE_LIVE_CAP_RESERVED:1",
        },
    )
    event = _make_event(
        city="Dallas",
        target_date="2026-07-25",
        metric="high",
    )

    adapter.process_global_batch((event,), NOW)
    policy = captured["candidate_policy_rejection_resolver"]

    assert policy(SimpleNamespace(action="BUY", family_key=FAMILY_A)) == (
        "LIVE_ENTRY_BLOCKED:entry_readiness_family:"
        "EDLI_STAGE_LIVE_CAP_RESERVED:1"
    )
    assert policy(SimpleNamespace(action="SELL", family_key=FAMILY_A)) is None


def test_paused_held_batch_keeps_global_proof_scope_without_enabling_actual_buy(
    monkeypatch,
):
    from src.engine import global_batch_runtime

    captured = {}

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    monkeypatch.setattr(
        era,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    trade_conn = sqlite3.connect(":memory:")
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
        held_family_provider=lambda: frozenset({("Dallas", "2026-07-25", "high")}),
    )

    adapter.process_global_batch((_make_event(city="Dallas", target_date="2026-07-25", metric="high"),), NOW)

    assert captured["buy_candidates_enabled"] is False
    assert captured["restrict_to_family_keys"] is None
    assert callable(captured["proof_candidate_policy_rejection_resolver"])
    assert (
        captured["proof_candidate_policy_rejection_resolver"](
            SimpleNamespace(
                action="BUY",
                family_key=FAMILY_A,
                bin_id="70F",
                side="YES",
            )
        )
        != "GLOBAL_CURRENT_REGIME_SETTLEMENT_GRADED_CAPITAL_ADVANTAGE_UNPROVEN"
    )


def test_paused_zero_held_batch_uses_unrestricted_reduce_only_no_trade(monkeypatch):
    from src.engine import global_batch_runtime

    captured = {}

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        era,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
        held_family_provider=lambda: frozenset(),
    )

    adapter.process_global_batch(
        (_make_event(city="Dallas", target_date="2026-07-25", metric="high"),),
        NOW,
    )

    assert captured["buy_candidates_enabled"] is False
    assert captured["restrict_to_family_keys"] is None


def test_blocked_carrier_does_not_require_buy_allocator_authority(monkeypatch):
    from src.engine import global_batch_runtime
    import src.risk_allocator as risk_allocator

    captured = {}
    snapshot_calls = []

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    class CapacityAuthority:
        def capacity_usd(self, **_kwargs):
            return 17

    def snapshot_authority():
        snapshot_calls.append(True)
        return CapacityAuthority()

    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        era,
        "_entry_pause_blocks_live_submit",
        lambda _conn: None,
    )
    monkeypatch.setattr(
        risk_allocator,
        "snapshot_global_auction_capital_authority",
        snapshot_authority,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        held_family_provider=lambda: frozenset(),
        entry_submit_block_reason="paused_forecast_snapshot_completion",
    )

    adapter.process_global_batch(
        (_make_event(city="Dallas", target_date="2026-07-25", metric="high"),),
        NOW,
    )

    assert snapshot_calls == []
    assert captured["buy_candidates_enabled"] is False
    policy = captured["candidate_policy_rejection_resolver"]
    assert policy(SimpleNamespace(action="SELL")) is None
    assert policy(SimpleNamespace(action="BUY", family_key=FAMILY_A)) == (
        "paused_forecast_snapshot_completion"
    )
    assert captured["current_capital_limit_resolver"](
        SimpleNamespace(family_key=FAMILY_A),
        "gamma-market",
        "market-event",
        "owner-event",
    ) == 17
    assert snapshot_calls == [True]


def test_pause_race_final_submit_gate_has_zero_venue_side_effect(monkeypatch):
    called = []
    monkeypatch.setattr(
        era,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
        executor_submit=lambda *_args: called.append(True),
    )

    receipt = adapter(_make_event(city="Dallas", target_date="2026-07-25", metric="high"), NOW)

    assert receipt.submitted is False
    assert receipt.venue_call_started is False
    assert receipt.reason == "entries_paused:operator_pause"
    assert called == []
