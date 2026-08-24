# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6. Integration wiring for src/strategy/tier0_policy.py's admission
#   gate inside event_bound_live_adapter_from_trade_conn's
#   _current_entry_candidate_policy. Harness pattern copied from
#   test_event_reactor_adapter_family_scoped_entry_block.py::
#   test_current_statistical_maker_reaches_capital_policy (same monkeypatch
#   shape: fake process_current_global_batch calls the adapter's
#   candidate_policy_rejection_resolver directly and captures the reason).
"""Tier-0 research mode: the live candidate-policy gate rejects price>=0.25,
MAKER_REST, and occupied-cluster BUY candidates only when the flag is on;
flag off is a byte-identical NOOP (pre-Tier-0 behavior)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import src.engine.event_reactor_adapter as era
from src.engine import global_batch_runtime
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
FAMILY_DALLAS_HIGH = weather_family_id(city="Dallas", target_date="2026-08-25", metric="high")


def _make_event(*, city="Dallas", target_date="2026-08-25", metric="high", event_id="evt-1"):
    payload = {"city": city, "target_date": target_date, "metric": metric}
    return OpportunityEvent(
        event_id=event_id,
        event_type="FORECAST_SNAPSHOT_READY",
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


def _drive_candidate_policy(monkeypatch, candidate, *, held_families=()):
    """Wire a minimal adapter and return candidate_policy_rejection_resolver(candidate)."""

    captured = {}

    def fake_prepare(_event, **_kwargs):
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=FAMILY_DALLAS_HIGH,
                witness_identity="current-statistical-q",
            ),
            day0_payoff_truth_by_bin_side=(),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        receipt = kwargs["prepare_event"](events[0], NOW)
        assert receipt.prepared_global_family is not None
        captured["reason"] = kwargs["candidate_policy_rejection_resolver"](candidate)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", fake_prepare)
    monkeypatch.setattr(era, "_entry_global_submit_suppression_reason", lambda: None)
    monkeypatch.setattr(era, "_edli_forecast_lane_phase_evidence", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(era, "_forecast_lane_phase_admits", lambda _evidence: True)
    monkeypatch.setattr(era, "_event_bound_strategy_key", lambda **_kwargs: "test")
    monkeypatch.setattr(era, "_global_current_entry_feasibility_rejection_reason", lambda *_a, **_kw: None)
    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: tuple(held_families),
    )

    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
    )
    adapter.process_global_batch((_make_event(),), NOW)
    return captured["reason"]


def _candidate(*, execution_mode="TAKER_LIMIT", limit_price=0.15):
    return SimpleNamespace(
        action="BUY",
        execution_mode=execution_mode,
        family_key=FAMILY_DALLAS_HIGH,
        bin_id="bin-a",
        side="YES",
        limit_price=limit_price,
    )


def test_flag_off_maker_rest_reaches_capital_policy_unblocked(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: False)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="MAKER_REST", limit_price=0.90),
    )
    assert reason is None


def test_flag_on_maker_rest_rejected_typed(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="MAKER_REST", limit_price=0.15),
    )
    assert reason is not None
    assert reason.startswith("TIER0_MAKER_REST_DISALLOWED")


def test_flag_on_price_above_cap_rejected_typed(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.90),
    )
    assert reason is not None
    assert reason.startswith("TIER0_MAX_ENTRY_PRICE_EXCEEDED")


def test_flag_on_cheap_taker_unoccupied_cluster_reaches_capital_policy(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(),
    )
    assert reason is None


def test_flag_on_occupied_cluster_rejected_typed(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    # Held family is a DIFFERENT metric (low) for the SAME city/date — proves
    # the cluster key is (city, target_date), coarser than family_key.
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(("Dallas", "2026-08-25", "low"),),
    )
    assert reason is not None
    assert reason.startswith("TIER0_CLUSTER_OCCUPIED")


def test_flag_on_different_cluster_not_blocked(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(("Miami", "2026-08-25", "high"),),
    )
    assert reason is None


# Once-per-cycle start-equity seed / drawdown-kill hook (reversal_plan_tier0
# item 6 follow-up). Gating happens at adapter CONSTRUCTION time (a plain
# statement in event_bound_live_adapter_from_trade_conn's body), before any
# per-candidate closures run -- so building the adapter is enough to observe
# whether the hook fired, with no event/candidate drive needed.
def test_flag_off_start_equity_hook_not_called(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: False)
    calls = []
    monkeypatch.setattr(
        "src.engine.tier0_drawdown_hook.tier0_seed_and_check_drawdown_kill",
        lambda *a, **kw: calls.append((a, kw)),
    )

    era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
    )

    assert calls == []


def test_flag_on_start_equity_hook_called_once_per_adapter_construction(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(
        "src.engine.tier0_drawdown_hook.tier0_seed_and_check_drawdown_kill",
        lambda *a, **kw: calls.append((a, kw)),
    )
    trade_conn = sqlite3.connect(":memory:")

    era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
    )

    assert len(calls) == 1
    (args, kwargs) = calls[0]
    assert args == (trade_conn,)
    assert "bankroll_usd_provider" in kwargs
