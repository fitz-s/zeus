# Created: 2026-09-25
# Last reused/audited: 2026-09-25
# Authority basis: root AGENTS.md INV-47 (SCOPE/DRAIN/RESET);
#   docs/operations/current/plans/auction_collapse_repair_design_2026-08-24.md
#   §2 gate 4 (JIT GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED re-derives q).
"""A committed Day0 fact cancels a global cut only inside that cut's scope.

The live adapter's cancellation closures are exercised the same way production
calls them: ``process_current_global_batch`` is captured, the real
``src.runtime.reactor_wake`` queue is written under the test state root, and the
captured ``day0_scope_observer`` stands in for the runtime's publications.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.event_reactor_adapter as era
import src.engine.global_batch_runtime as global_batch_runtime
import src.runtime.reactor_wake as reactor_wake
from src.contracts.strategy_capital_allocation import (
    StrategyCapitalAllocationWitness,
)
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)

_TARGET = "2026-07-11"
_IN = ("Dallas", _TARGET, "high")
_OUT = ("Moscow", _TARGET, "high")
_IN_KEY = weather_family_id(city=_IN[0], target_date=_IN[1], metric=_IN[2])
_OUT_KEY = weather_family_id(city=_OUT[0], target_date=_OUT[1], metric=_OUT[2])


def _forecast_event(city: str):
    captured_at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date=_TARGET,
        metric="high",
        source_id="replacement_0_1",
        source_run_id=f"run-{city}",
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id=f"rmf-{city}|{_TARGET}|high|2026-07-10",
        snapshot_hash=f"run-{city}",
        captured_at=captured_at,
        available_at=captured_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=3,
        min_members_floor=3,
        completeness_status="COMPLETE",
        required_steps=[],
        observed_steps=[],
        expected_members=3,
        source_run_status="COMPLETE",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    payload_json = json.loads(json.dumps(payload, default=lambda o: o.__dict__))
    payload_json["city_timezone"] = "UTC"
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{_TARGET}|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload_json,
        causal_snapshot_id=payload.snapshot_id,
    )


@pytest.fixture
def cut(monkeypatch, tmp_path):
    """Build one live adapter cut; return its captured runtime callbacks."""

    return _build_cut(monkeypatch, tmp_path)


def _build_cut(monkeypatch, tmp_path, **adapter_kwargs):

    wake_path = tmp_path / "state" / "edli-reactor-wake.json"
    monkeypatch.setattr(
        reactor_wake, "_wake_path", lambda _path=None: wake_path
    )
    captured: dict = {}
    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        lambda events, **kwargs: captured.update(kwargs)
        or SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={}),
    )
    monkeypatch.setattr(
        era, "_entry_global_submit_suppression_reason", lambda: None
    )
    monkeypatch.setattr(era, "_entry_pause_blocks_live_submit", lambda _conn: None)

    class CapacityAuthority:
        def capacity_usd(self, **_kwargs):
            return Decimal("17")

    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        portfolio_state_provider=lambda: None,
        auction_capital_authority=CapacityAuthority(),
        **adapter_kwargs,
    )
    adapter.process_global_batch(
        (_forecast_event("Dallas"),),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    def publish_day0(*families):
        reactor_wake.publish_reactor_wake(
            source="day0_metar_source_clock",
            reason="day0_extreme_event_committed",
            path=wake_path,
            forecast_families=families,
        )

    return SimpleNamespace(captured=captured, publish_day0=publish_day0)


def test_out_of_scope_day0_commit_does_not_cancel_the_cut(cut):
    cut.captured["day0_scope_observer"](frozenset({_IN_KEY}))
    cut.publish_day0(_OUT)

    assert cut.captured["selection_cancelled"]() is False
    assert cut.captured["final_actuation_cancelled"]() is False
    assert cut.captured["epoch_superseded"]() is False


def test_in_scope_day0_commit_cancels_the_cut(cut):
    cut.captured["day0_scope_observer"](frozenset({_IN_KEY}))
    cut.publish_day0(_IN)

    assert cut.captured["selection_cancelled"]() is True
    assert cut.captured["final_actuation_cancelled"]() is True
    assert cut.captured["epoch_superseded"]() is True


def test_day0_commit_before_the_scope_is_known_cancels(cut):
    """Until the runtime publishes a scope, every Day0 fact keeps its veto."""

    cut.captured["day0_scope_observer"](None)
    cut.publish_day0(_OUT)

    assert cut.captured["selection_cancelled"]() is True


def test_day0_wake_without_families_cancels(cut):
    cut.captured["day0_scope_observer"](frozenset({_IN_KEY}))
    cut.publish_day0()

    assert cut.captured["selection_cancelled"]() is True


def test_receipt_stage_keeps_selection_for_out_of_scope_day0(cut):
    """After selection narrows scope to the winner, other families' facts wait.

    The scope scan covered both families; once the winner (Dallas) is frozen,
    a Moscow Day0 commit no longer reaches this cut's receipt or actuation.
    """

    observe = cut.captured["day0_scope_observer"]
    observe(frozenset({_IN_KEY, _OUT_KEY}))
    observe(frozenset({_IN_KEY}))
    cut.publish_day0(_OUT)

    assert cut.captured["selection_cancelled"]() is False
    assert cut.captured["final_actuation_cancelled"]() is False
    cut.publish_day0(_IN)
    assert cut.captured["final_actuation_cancelled"]() is True


def test_widened_scope_rereads_an_absorbed_day0_wake(cut):
    """A fallthrough winner in a family whose fact was absorbed must cancel."""

    observe = cut.captured["day0_scope_observer"]
    observe(frozenset({_IN_KEY}))
    cut.publish_day0(_OUT)
    assert cut.captured["selection_cancelled"]() is False

    observe(frozenset({_IN_KEY, _OUT_KEY}))

    assert cut.captured["selection_cancelled"]() is True


@pytest.mark.parametrize("family", (_IN, _OUT))
def test_reserved_completion_cut_scopes_its_day0_supersession(
    monkeypatch, tmp_path, family
):
    """A fairness-reserved cut keeps its reservation only for in-scope Day0."""

    reserved = _build_cut(
        monkeypatch, tmp_path, selection_completion_fairness_reserved=True
    )
    reserved.captured["day0_scope_observer"](frozenset({_IN_KEY}))
    reserved.publish_day0(family)

    assert reserved.captured["epoch_superseded"]() is (family == _IN)


def test_runtime_publishes_scan_scope_then_winner_scope(monkeypatch):
    """The runtime narrows the Day0 scope to the winner once selection freezes."""

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    events = (_forecast_event("Dallas"), _forecast_event("Moscow"))
    scope = global_batch_runtime.current_global_auction_scope_from_events(
        events, captured_at_utc=decision_at
    )
    family_by_event = {
        event.event_id: family_key for family_key, event in scope.events_by_family
    }
    winner = next(e for e in events if family_by_event[e.event_id] == _IN_KEY)
    selected = SimpleNamespace(
        decision=SimpleNamespace(
            candidate=SimpleNamespace(family_key=_IN_KEY),
            no_trade_reason=None,
        ),
        winner_event_id=winner.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth-1",
        ),
    )
    published: list[frozenset[str] | None] = []

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-1",
            economic_identity="wealth-economics-1",
            strategy_capital_allocation=StrategyCapitalAllocationWitness.build(
                capital_basis_usd=Decimal("10"),
                committed_capital_usd=Decimal("0"),
                venue_spendable_cash_usd=Decimal("10"),
                allocation={"mode": "wallet_total"},
            ),
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_args, **_kwargs: selected,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_args, **_kwargs: published.append("receipt") or None,
    )

    def prepared(event):
        run_id = json.loads(event.payload_json)["source_run_id"]
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=family_by_event[event.event_id],
                captured_at_utc=decision_at,
                posterior_identity_hash=run_id,
                witness_identity=f"q-{run_id}",
            )
        )

    global_batch_runtime.process_current_global_batch(
        events,
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: era.EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared(current),
        ),
        actuate_winner=lambda *_: pytest.fail("no actuation in this test"),
        preflight_winner=lambda *_: global_batch_runtime.GlobalWinnerPreflight(
            status="CANDIDATE_BLOCKED", reason="test-stop"
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            SimpleNamespace(
                witness_identity="book",
                captured_at_utc=decision_at,
                max_age=_dt.timedelta(seconds=30),
                assets=(),
            ),
        ),
        day0_scope_observer=published.append,
    )

    assert published[0] is None
    assert published[1] == frozenset({_IN_KEY, _OUT_KEY})
    assert published[2] == frozenset({_IN_KEY})
    assert published[3] == "receipt"
