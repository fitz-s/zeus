# Created: 2026-09-25
# Last reused/audited: 2026-10-04
# Authority basis: root AGENTS.md INV-47 (SCOPE/DRAIN/RESET);
#   docs/operations/current/plans/auction_collapse_repair_design_2026-08-24.md
#   §2 gate 4 (JIT GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED re-derives q).
"""A family-scoped fact cancels a global cut only through its frozen winner.

The live adapter's cancellation closures are exercised the same way production
calls them: ``process_current_global_batch`` is captured, the real
``src.runtime.reactor_wake`` queue is written under the test state root, and the
captured ``cut_scope_observer`` stands in for the runtime's publications
(``CutScope``); ``src.runtime.reactor_wake.cut_invalidating_wakes`` judges.
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


_DECISION_AT = _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc)


def _build_cut(monkeypatch, tmp_path, *, decision_at=_DECISION_AT, **adapter_kwargs):

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
    adapter.process_global_batch((_forecast_event("Dallas"),), decision_at)

    def publish(reason, *families, published_at=None):
        reactor_wake.publish_reactor_wake(
            source="test-producer",
            reason=reason,
            path=wake_path,
            forecast_families=families,
            published_at=published_at,
        )

    def publish_day0(*families, published_at=None):
        publish("day0_extreme_event_committed", *families, published_at=published_at)

    def freeze(winner_key, held=frozenset()):
        captured["cut_scope_observer"](
            reactor_wake.CutScope(
                winner_family_key=winner_key, held_family_keys=frozenset(held)
            )
        )

    def reset():
        captured["cut_scope_observer"](None)

    return SimpleNamespace(
        captured=captured,
        publish=publish,
        publish_day0=publish_day0,
        freeze=freeze,
        reset=reset,
    )


_DAY0 = "wake:day0_extreme_event_committed"
_PRINT = "wake:current_temperature_print_committed"
_HK = ("Hong Kong", _TARGET, "high")
_HK_KEY = weather_family_id(city=_HK[0], target_date=_HK[1], metric=_HK[2])


def _probes(cut):
    return (
        cut.captured["selection_cancelled"],
        cut.captured["epoch_superseded"],
        cut.captured["final_actuation_cancelled"],
    )


def _reasons(label):
    """``wake:r[kind]#id@families,...`` -> ``wake:r,...``; other values as-is."""

    if not isinstance(label, str) or not label.startswith("wake:"):
        return label
    return "wake:" + ",".join(
        part.split("[", 1)[0] for part in label[len("wake:"):].split(",")
    )


def test_unrelated_city_print_never_cancels_a_cut(cut):
    """A print for another city changes no input the cut acts on."""

    cut.publish("current_temperature_print_committed", _HK)
    assert [probe() for probe in _probes(cut)] == [False, False, False]
    cut.freeze(_IN_KEY)
    assert [probe() for probe in _probes(cut)] == [False, False, False]


@pytest.mark.parametrize(
    ("reason", "label"),
    (
        ("current_temperature_print_committed", _PRINT),
        ("forecast_posterior_advanced", "wake:forecast_posterior_advanced"),
        ("day0_extreme_event_committed", _DAY0),
    ),
)
def test_a_fact_for_the_frozen_winners_family_cancels_through_actuation(
    cut, reason, label
):
    """The JIT replays the winner's q at the selection instant, so a newer
    fact for its family must end the cut at every checkpoint."""

    cut.freeze(_IN_KEY)
    cut.publish(reason, _IN)
    assert [_reasons(probe()) for probe in _probes(cut)] == [label, label, label]


def test_a_family_fact_before_the_freeze_defers_then_cancels_at_the_freeze(cut):
    """Re-judgement at the freeze reads every wake since the cut's decision
    time in its original identity: a winner-family print published mid-prepare
    still ends the cut, an unrelated one never does."""

    cut.publish("current_temperature_print_committed", _HK)
    cut.publish("current_temperature_print_committed", _IN)
    cut.publish_day0(_OUT)
    assert [probe() for probe in _probes(cut)] == [False, False, False]

    cut.freeze(_IN_KEY)
    assert [_reasons(probe()) for probe in _probes(cut)] == [_PRINT, _PRINT, _PRINT]


def test_a_hard_fact_for_a_holding_cancels_but_its_belief_does_not(cut):
    cut.freeze(_IN_KEY, held={_OUT_KEY})
    cut.publish("current_temperature_print_committed", _OUT)
    assert [probe() for probe in _probes(cut)] == [False, False, False]
    cut.publish_day0(_OUT)
    assert [_reasons(probe()) for probe in _probes(cut)] == [_DAY0, _DAY0, _DAY0]


@pytest.mark.parametrize(
    "reason", ("position_fill_projected", "an_unknown_producer_fact")
)
def test_capital_and_unknown_wakes_still_supersede_every_cut(cut, reason):
    assert cut.captured["epoch_superseded"]() is False
    cut.publish(reason)
    assert _reasons(cut.captured["epoch_superseded"]()) == f"wake:{reason}"


@pytest.mark.parametrize(
    "reason", ("day0_extreme_event_committed", "current_temperature_print_committed")
)
def test_a_family_fact_naming_no_family_cancels_even_before_the_freeze(cut, reason):
    cut.publish(reason)
    assert _reasons(cut.captured["selection_cancelled"]()) == f"wake:{reason}"


def test_a_new_cut_rejudges_from_none(cut):
    """RESET: a recursive or re-auction cut republishes None; a fact absorbed
    for a previous winner is re-judged against the next one."""

    cut.freeze(_IN_KEY)
    cut.publish_day0(_OUT)
    assert cut.captured["selection_cancelled"]() is False
    cut.reset()
    assert cut.captured["selection_cancelled"]() is False
    cut.freeze(_OUT_KEY)
    assert _reasons(cut.captured["selection_cancelled"]()) == _DAY0


def test_frozen_cut_ignores_routine_monitor_fairness(monkeypatch, tmp_path):
    """(e) Once the winner is selected, routine selection pressure no longer
    preempts the cut; a fact its winner rests on still does."""

    routine = ["monitor_handoff"]
    cut = _build_cut(monkeypatch, tmp_path, selection_cancelled=lambda: routine[0])
    assert cut.captured["selection_cancelled"]() == "monitor_handoff"

    cut.freeze(_IN_KEY)
    assert cut.captured["selection_cancelled"]() is False
    cut.publish_day0(_IN)
    assert _reasons(cut.captured["selection_cancelled"]()) == _DAY0


def test_cutoff_is_the_cuts_own_decision_time(monkeypatch, tmp_path):
    """(b) A fact published after the producer wake but before this cut's
    decision time is truth the cut reads, never an invalidation of it."""

    decision_at = _dt.datetime.now(_dt.timezone.utc)
    cut = _build_cut(
        monkeypatch,
        tmp_path,
        decision_at=decision_at,
        producer_wake_published_at=(
            decision_at - _dt.timedelta(minutes=5)
        ).isoformat(),
    )
    cut.freeze(_IN_KEY)
    cut.publish_day0(_IN, published_at=decision_at - _dt.timedelta(seconds=1))
    assert cut.captured["selection_cancelled"]() is False

    cut.publish_day0(_IN, published_at=decision_at + _dt.timedelta(seconds=1))
    assert _reasons(cut.captured["selection_cancelled"]()) == _DAY0


@pytest.mark.parametrize("family", (_IN, _OUT))
def test_family_scoped_held_completion_follows_the_same_law(
    monkeypatch, tmp_path, family
):
    """A generic held completion cut values its whole portfolio, yet acts
    on one winner: only that winner's (or a holding's hard) fact ends it."""

    monkeypatch.setattr(
        reactor_wake, "exact_held_sell_completion_wake_ids", lambda **_kw: ()
    )
    held = _build_cut(
        monkeypatch,
        tmp_path,
        family_scoped_held_completion=True,
        selection_completion_reserved=True,
        required_held_family_keys=frozenset({_IN_KEY}),
    )
    held.publish("current_temperature_print_committed", family)
    assert held.captured["final_actuation_cancelled"]() is False
    held.freeze(_IN_KEY)
    assert _reasons(held.captured["final_actuation_cancelled"]()) == (
        _PRINT if family == _IN else False
    )


def test_runtime_publishes_only_the_frozen_winner(monkeypatch):
    """The runtime publishes no family scope until selection freezes a winner."""

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
    published: list[object] = []

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
        cut_scope_observer=published.append,
    )

    assert published[0] is None
    assert published[1] == reactor_wake.CutScope(winner_family_key=_IN_KEY)
    assert published[2] == "receipt"
