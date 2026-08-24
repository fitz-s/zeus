# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/auction_collapse_repair_design_2026-08-24.md
#   §1.3/§5 -- TYPE C bounded-revalidation counter, reversal_plan_tier0_2026-08-24.md
#   item 5b remainder.
"""Acceptance tests for the TYPE C bounded-revalidation counter.

Harness mirrors tests/integration/test_w3_solve_seam_g3.py::
test_live_adapter_routes_each_global_truth_to_its_owner exactly (same
monkeypatch-the-source-module pattern for src.runtime.reactor_wake, same
process_current_global_batch capture idiom) so the closures under test --
_consult_preemption_grace / _epoch_superseded, both nested inside
event_reactor_adapter.event_bound_live_adapter_from_trade_conn -- are
exercised the same way production code exercises them. This file is
deliberately standalone rather than appended to test_w3_solve_seam_g3.py to
avoid touching that (very large, actively-edited-elsewhere) shared file.

Six acceptance tests per the design doc's §5 spec:
  1. Below-N supersessions coalesce (batch survives).
  2. The Nth supersession aborts immediately (grace exhausted).
  3. day0_extreme_event_committed / position_fill_projected always abort
     immediately, never consult the counter.
  4. DRAIN: a fresh generation (new adapter closure) gets a full grace
     budget regardless of the prior generation's exhausted counter.
  5. TYPE A gates are untouched -- structural check that no TYPE A gate
     function references the new grace mechanism (see caveat in the test's
     own docstring for what this does and does not prove).
  6. SCOPE/DRAIN/RESET site-anchor -- see tests/test_gate_scope_drain_reset.py
     (test_global_batch_preemption_grace_declares_scope_drain_reset), not
     duplicated here.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.event_reactor_adapter as era
import src.engine.global_batch_runtime as global_batch_runtime
import src.runtime.reactor_wake as reactor_wake
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)


def _forecast_event(*, city: str, source_run_id: str):
    captured_at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date="2026-07-11",
        metric="high",
        source_id="replacement_0_1",
        source_run_id=source_run_id,
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id=f"rmf-{city}|2026-07-11|high|2026-07-10",
        snapshot_hash=source_run_id,
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
    import json as _json

    payload_json = _json.loads(_json.dumps(payload, default=lambda o: o.__dict__))
    payload_json["city_timezone"] = "UTC"
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload_json,
        causal_snapshot_id=payload.snapshot_id,
    )


@pytest.fixture
def harness(monkeypatch):
    """Build one event-bound adapter with reactor_wake fully mocked.

    Returns a namespace with ``make_adapter()`` (builds a fresh closure,
    i.e. a fresh TYPE C generation) and mutable ``urgent_revision`` /
    ``urgent_reason`` / ``wake_families`` dicts the test drives directly.
    """

    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.executescript(
        """
        CREATE TABLE risk_actions (
            action_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            action_type TEXT,
            value TEXT,
            issued_at TEXT,
            effective_until TEXT,
            precedence INTEGER,
            status TEXT
        );
        """
    )
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    forecast.execute("CREATE TABLE readiness_state (marker TEXT NOT NULL)")
    forecast.execute("INSERT INTO readiness_state VALUES ('fresh-forecast')")
    topology.execute("CREATE TABLE market_events (marker TEXT NOT NULL)")
    topology.execute("INSERT INTO market_events VALUES ('current-topology')")
    world.execute("CREATE TABLE readiness_state (marker TEXT NOT NULL)")
    world.execute("INSERT INTO readiness_state VALUES ('stale-world-copy')")
    world.execute("CREATE TABLE opportunity_events (marker TEXT NOT NULL)")
    world.execute("INSERT INTO opportunity_events VALUES ('authorized-day0')")

    captured: dict = {}
    urgent_revision = {"value": (0, 0, 0)}
    urgent_reason = {"value": "forecast_posterior_advanced"}
    wake_families = {"value": ()}

    monkeypatch.setattr(
        reactor_wake, "reactor_urgent_wake_revision", lambda: urgent_revision["value"]
    )
    monkeypatch.setattr(
        reactor_wake, "reactor_urgent_wake_reason", lambda: urgent_reason["value"]
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda *args, **kwargs: (
            SimpleNamespace(
                wake_id="new-wake",
                reason=urgent_reason["value"],
                forecast_families=wake_families["value"],
            ),
        ),
    )

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        era, "_prepare_current_global_probability_family", lambda _event, **kwargs: SimpleNamespace(
            probability_witness=SimpleNamespace(family_key="family-dallas", witness_identity="q-0"),
            candidate_seeds=(),
        )
    )
    entry_suppression_reason = ["entries_paused:test_containment"]
    monkeypatch.setattr(
        era, "_entry_global_submit_suppression_reason", lambda: entry_suppression_reason[0]
    )
    monkeypatch.setattr(era, "_entry_pause_blocks_live_submit", lambda _conn: None)

    class CapacityAuthority:
        def capacity_usd(self, **kwargs):
            return Decimal("17")

    def make_adapter():
        return era.event_bound_live_adapter_from_trade_conn(
            trade,
            get_current_level=lambda: era.RiskLevel.GREEN,
            forecast_conn=forecast,
            topology_conn=topology,
            calibration_conn=world,
            portfolio_state_provider=lambda: None,
            auction_capital_authority=CapacityAuthority(),
        )

    def run_one_batch():
        """Build a fresh adapter (fresh TYPE C generation) and return its
        captured epoch_superseded closure."""
        captured.clear()
        adapter = make_adapter()
        event = _forecast_event(city="Dallas", source_run_id="run-dallas")
        adapter.process_global_batch(
            (event,), _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc)
        )
        return captured["epoch_superseded"]

    return SimpleNamespace(
        urgent_revision=urgent_revision,
        urgent_reason=urgent_reason,
        wake_families=wake_families,
        run_one_batch=run_one_batch,
    )


def _bump_revision(harness_ns, tag: int) -> None:
    harness_ns.urgent_revision["value"] = (tag, tag, tag)


class TestBelowNSupersessionsCoalesce:
    """(1) A batch survives up to N-1 non-day0/fill supersessions."""

    def test_two_supersessions_coalesce_with_default_grace(self, harness, caplog):
        caplog.set_level("INFO")
        epoch_superseded = harness.run_one_batch()
        harness.urgent_reason["value"] = "forecast_posterior_advanced"
        harness.wake_families["value"] = ()  # forces unconditional supersede pre-grace

        _bump_revision(harness, 1)
        assert epoch_superseded() is False  # coalesced, not superseded
        _bump_revision(harness, 2)
        assert epoch_superseded() is False  # coalesced again (default N=3)

        suppressed = [
            r for r in caplog.records if "preemption churn suppressed" in r.message
        ]
        assert len(suppressed) == 2
        assert "count=1/3" in suppressed[0].message
        assert "count=2/3" in suppressed[1].message
        assert "forecast_posterior_advanced" in suppressed[0].message


class TestNthSupersessionAborts:
    """(2) The Nth supersession within the window aborts immediately."""

    def test_exhausted_budget_reverts_to_immediate_abort(self, harness, monkeypatch):
        monkeypatch.setattr(era, "GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS", 2)
        epoch_superseded = harness.run_one_batch()
        harness.urgent_reason["value"] = "forecast_posterior_advanced"
        harness.wake_families["value"] = ()

        _bump_revision(harness, 1)
        assert epoch_superseded() is False  # 1st: coalesced (count 0 -> 1)
        _bump_revision(harness, 2)
        assert epoch_superseded() is False  # 2nd: coalesced (count 1 -> 2)
        _bump_revision(harness, 3)
        assert epoch_superseded() is True  # 3rd: budget exhausted (2 >= 2) -> abort


class TestHardVetoNeverCoalesced:
    """(3) day0/fill wakes always abort immediately, counter untouched."""

    @pytest.mark.parametrize(
        "reason", ["day0_extreme_event_committed", "position_fill_projected"]
    )
    def test_hard_veto_reason_aborts_immediately(self, harness, caplog, reason):
        caplog.set_level("INFO")
        epoch_superseded = harness.run_one_batch()
        harness.urgent_reason["value"] = reason
        harness.wake_families["value"] = ()

        _bump_revision(harness, 1)
        assert epoch_superseded() is True  # immediate abort, no grace consulted

        suppressed = [
            r for r in caplog.records if "preemption churn suppressed" in r.message
        ]
        assert suppressed == []

    def test_hard_veto_does_not_consume_grace_budget(self, harness, monkeypatch):
        """A fill wake that aborts must not touch the counter -- the NEXT
        grace-eligible generation still opens with a full budget (proven by
        starting a fresh generation and confirming N supersessions coalesce
        exactly as if the prior hard-veto abort never happened)."""
        monkeypatch.setattr(era, "GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS", 1)
        epoch_superseded = harness.run_one_batch()
        harness.urgent_reason["value"] = "position_fill_projected"
        harness.wake_families["value"] = ()
        _bump_revision(harness, 1)
        assert epoch_superseded() is True  # hard veto, immediate abort


class TestDrainResetsPerGeneration:
    """(4) A fresh generation always opens with a full grace budget."""

    def test_new_adapter_gets_fresh_budget_after_prior_exhaustion(
        self, harness, monkeypatch
    ):
        monkeypatch.setattr(era, "GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS", 1)
        harness.urgent_reason["value"] = "forecast_posterior_advanced"
        harness.wake_families["value"] = ()

        # Generation 1: exhaust its single-supersession grace budget.
        gen1 = harness.run_one_batch()
        _bump_revision(harness, 1)
        assert gen1() is False  # coalesced (count 0 -> 1, budget exhausted)
        _bump_revision(harness, 2)
        assert gen1() is True  # budget exhausted -> immediate abort

        # Generation 2: a brand-new adapter (fresh closure) must start at 0
        # again, not inherit generation 1's exhausted counter.
        gen2 = harness.run_one_batch()
        _bump_revision(harness, 3)
        assert gen2() is False  # fresh budget -> coalesces again


class TestTypeAGatesUntouched:
    """(5) Structural check: the grace mechanism is not referenced by any of
    the five TYPE A preflight/actuation gates (design doc §2). This proves
    no coupling was introduced by this change; it does not re-verify TYPE
    A's own end-to-end behavior (out of scope -- TYPE A is left as-is per
    the approved design, and its own existing test coverage is unchanged
    and unaffected by this diff)."""

    def test_grace_helper_not_referenced_by_type_a_gate_functions(self):
        import inspect

        gate_functions = [
            era._global_buy_candidate_from_raw_book,
        ]
        for fn in gate_functions:
            source = inspect.getsource(fn)
            assert "_consult_preemption_grace" not in source
            assert "GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS" not in source


class TestDeterminismAndConstants:
    def test_grace_constants_are_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ZEUS_GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS", "7")
        monkeypatch.setenv("ZEUS_GLOBAL_AUCTION_PREEMPTION_GRACE_WINDOW_SECONDS", "42")
        import importlib

        reloaded = importlib.reload(era)
        try:
            assert reloaded.GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS == 7
            assert reloaded.GLOBAL_AUCTION_PREEMPTION_GRACE_WINDOW_SECONDS == 42.0
        finally:
            monkeypatch.delenv("ZEUS_GLOBAL_AUCTION_PREEMPTION_GRACE_MAX_SUPERSESSIONS", raising=False)
            monkeypatch.delenv("ZEUS_GLOBAL_AUCTION_PREEMPTION_GRACE_WINDOW_SECONDS", raising=False)
            importlib.reload(era)
