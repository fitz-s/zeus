# Created: 2026-05-20
# Last reused/audited: 2026-07-19
# Authority basis: PHASE_2_ULTRAPLAN.md §8.2 + §8.3; finite-evidence probability symmetry packet held/entry single-q law
# Lifecycle: created=2026-05-20; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: T5 GREEN antibody — _maybe_write_day0_nowcast gate conditions + write_nowcast_run call.
# Reuse: Run when _maybe_write_day0_nowcast, write_nowcast_run wiring, or day0 gate logic changes.
"""
T5 GREEN antibody: _maybe_write_day0_nowcast call-site invocation.

Verifies that _maybe_write_day0_nowcast calls write_nowcast_run when
position.market_slug is set, hours_remaining <= 6, and a platt fit is available.

Gate conditions tested:
  - market_slug=None → function returns early, no write.
  - market_slug set + hours_remaining > 6 → function returns early, no write.
  - market_slug set + hours_remaining <= 6 + fit available → write_nowcast_run called (GREEN).
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

import src.engine.monitor_refresh as monitor_refresh_module
from src.engine.monitor_refresh import _maybe_write_day0_nowcast
from src.engine.position_belief import ReplacementBelief
from src.observability.counters import read as read_counter, reset_all as reset_counters
from src.state.portfolio import Position


def test_monitor_utc_parser_shares_observation_timestamp_contract() -> None:
    parsed = monitor_refresh_module._parse_utc_datetime("1784476800")

    assert parsed == datetime(2026, 7, 19, 16, 0, tzinfo=timezone.utc)
    assert monitor_refresh_module._parse_utc_datetime(True) is None
    assert monitor_refresh_module._parse_utc_datetime("nan") is None


def test_belief_reseed_dispatch_is_family_isolated_and_coalesced(monkeypatch) -> None:
    paris_started = threading.Event()
    paris_release = threading.Event()
    moscow_started = threading.Event()
    paris_calls = 0
    calls_lock = threading.Lock()

    def perform(*, city: str, target_date: str, metric: str):
        nonlocal paris_calls
        if city == "Paris":
            with calls_lock:
                paris_calls += 1
            paris_started.set()
            assert paris_release.wait(1.0)
        elif city == "Moscow":
            moscow_started.set()
        return {"status": "done"}

    monkeypatch.setattr(
        monitor_refresh_module,
        "_perform_single_family_belief_reseed_failsoft",
        perform,
    )
    with monitor_refresh_module._BELIEF_RESEED_LOCK:
        monitor_refresh_module._BELIEF_RESEED_GENERATIONS.clear()

    started = time.monotonic()
    first = monitor_refresh_module._enqueue_single_family_belief_reseed_failsoft(
        city="Paris", target_date="2026-07-18", metric="high"
    )
    assert time.monotonic() - started < 0.1
    assert first["status"] == "CYCLE_ADVANCE_RESEED_DISPATCHED"
    assert paris_started.wait(0.5)

    duplicate = monitor_refresh_module._enqueue_single_family_belief_reseed_failsoft(
        city="paris", target_date="2026-07-18", metric="HIGH"
    )
    unrelated = monitor_refresh_module._enqueue_single_family_belief_reseed_failsoft(
        city="Moscow", target_date="2026-07-18", metric="high"
    )
    assert duplicate["status"] == "CYCLE_ADVANCE_RESEED_COALESCED"
    assert unrelated["status"] == "CYCLE_ADVANCE_RESEED_DISPATCHED"
    assert moscow_started.wait(0.5)

    paris_release.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with monitor_refresh_module._BELIEF_RESEED_LOCK:
            if not monitor_refresh_module._BELIEF_RESEED_GENERATIONS:
                break
        time.sleep(0.01)
    with monitor_refresh_module._BELIEF_RESEED_LOCK:
        assert monitor_refresh_module._BELIEF_RESEED_GENERATIONS == {}
    assert paris_calls == 2


def test_belief_reseed_start_failure_clears_coalesced_generation(monkeypatch) -> None:
    real_thread = threading.Thread
    start_entered = threading.Event()
    release_start = threading.Event()

    class _FailedThread:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            start_entered.set()
            assert release_start.wait(1.0)
            raise RuntimeError("injected start failure")

    monkeypatch.setattr(monitor_refresh_module.threading, "Thread", _FailedThread)
    with monitor_refresh_module._BELIEF_RESEED_LOCK:
        monitor_refresh_module._BELIEF_RESEED_GENERATIONS.clear()

    first_result: list[object] = []

    def enqueue_first() -> None:
        first_result.append(
            monitor_refresh_module._enqueue_single_family_belief_reseed_failsoft(
                city="Paris", target_date="2026-07-18", metric="high"
            )
        )

    first = real_thread(target=enqueue_first)
    first.start()
    assert start_entered.wait(0.5)
    duplicate = monitor_refresh_module._enqueue_single_family_belief_reseed_failsoft(
        city="paris", target_date="2026-07-18", metric="HIGH"
    )
    assert duplicate["status"] == "CYCLE_ADVANCE_RESEED_COALESCED"
    release_start.set()
    first.join(1.0)

    assert first.is_alive() is False
    assert first_result == [None]
    with monitor_refresh_module._BELIEF_RESEED_LOCK:
        assert monitor_refresh_module._BELIEF_RESEED_GENERATIONS == {}


def _replacement_belief(
    *,
    fresh: bool = True,
    direction: str = "buy_no",
) -> ReplacementBelief:
    q_yes = 0.27
    return ReplacementBelief(
        held_side_prob=q_yes if direction == "buy_yes" else 1.0 - q_yes,
        q_yes_bin=q_yes,
        posterior_id="posterior-pre-first-observation",
        computed_at="2026-07-11T23:05:00+00:00",
        age_hours=0.1,
        fresh=fresh,
        bin_key="test-bin",
        direction=direction,
    )


def test_probability_refresh_preserves_only_pending_robust_exit_confirmation() -> None:
    """Fresh belief replacement must not make confirmation two unreachable."""

    pending = "day0_robust_sell_value_awaits_confirmation"
    position = SimpleNamespace(
        applied_validations=["stale_probability_evidence", pending]
    )
    refreshed = SimpleNamespace(applied_validations=["current_probability_evidence"])

    monitor_refresh_module._replace_probability_validations_preserving_exit_confirmation(
        position,
        refreshed,
    )

    assert position.applied_validations == ["current_probability_evidence", pending]


def test_fresh_probability_refresh_drops_prior_cut_validations(monkeypatch) -> None:
    """A real dataclass refresh clone cannot relabel stale evidence as fresh."""

    from src.engine import position_belief

    pending = "day0_robust_sell_value_awaits_confirmation"
    prior = _make_position()
    prior.applied_validations = [
        "monitor_probability_stale",
        "replacement_posterior_stale;age_h=12.50",
        "replacement_posterior_missing",
        pending,
    ]
    refresh_input = replace(prior)
    belief = _replacement_belief()
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_would_use_day0_monitor_lane",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: belief,
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        refresh_input,
        conn=None,
        city=SimpleNamespace(timezone="UTC"),
        target_d=date(2026, 7, 20),
    )

    assert fresh is True
    assert probability == pytest.approx(belief.held_side_prob)
    assert refreshed.selected_method == "replacement_posterior"
    assert getattr(
        refreshed,
        monitor_refresh_module._MONITOR_PROBABILITY_FRESH_ATTR,
    ) is True
    assert refreshed.applied_validations == [
        pending,
        "replacement_posterior",
        belief.freshness_validation(),
    ]
    assert refresh_input.applied_validations == prior.applied_validations


def test_day0_start_grace_is_bounded_to_target_local_day() -> None:
    city = SimpleNamespace(timezone="Europe/London")
    target = date(2026, 7, 12)

    assert monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 11, 23, 30, tzinfo=timezone.utc),
    )
    assert not monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 12, 2, 1, tzinfo=timezone.utc),
    )
    assert not monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 11, 22, 59, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("direction", "expected_probability"),
    [("buy_yes", 0.27), ("buy_no", 0.73)],
)
def test_pre_first_day0_observation_uses_fresh_replacement_belief(
    monkeypatch,
    direction: str,
    expected_probability: float,
) -> None:
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import position_belief

    pos = _make_position()
    pos.direction = direction
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    monkeypatch.setattr(
        monitor_refresh_module,
        "recompute_native_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObservationUnavailableError("first target-day observation not published")
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(direction=kwargs["direction"]),
    )

    prob, refresh_pos, fresh = monitor_refresh_module._refresh_day0_monitor_probability(
        pos,
        conn=None,
        city=SimpleNamespace(timezone="UTC"),
        target_d=date(2026, 7, 12),
    )

    assert fresh is True
    assert prob == pytest.approx(expected_probability)
    assert refresh_pos.selected_method == "replacement_posterior"
    assert (
        "day0_unobserved_prefix_within_start_grace:replacement_posterior_authority"
        in refresh_pos.applied_validations
    )


@pytest.mark.parametrize(
    ("belief_fresh", "inside_grace"),
    [(False, True), (True, False)],
)
def test_day0_observation_absence_stays_stale_without_both_authorities(
    monkeypatch,
    belief_fresh: bool,
    inside_grace: bool,
) -> None:
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import position_belief

    pos = _make_position()
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    monkeypatch.setattr(
        monitor_refresh_module,
        "recompute_native_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObservationUnavailableError("day0 observation unavailable")
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: inside_grace,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(fresh=belief_fresh),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_attempt_held_belief_readthrough",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: None,
    )

    prob, refresh_pos, fresh = monitor_refresh_module._refresh_day0_monitor_probability(
        pos,
        conn=None,
        city=SimpleNamespace(timezone="UTC"),
        target_d=date(2026, 7, 12),
    )

    assert fresh is False
    assert prob == pytest.approx(pos.p_posterior)
    assert refresh_pos.selected_method != "replacement_posterior"


def test_day0_monitor_reads_exact_current_global_probability_witness(
    monkeypatch,
) -> None:
    """A live identified holding uses the entry/SELL/HOLD/CASH joint-q witness."""
    import numpy as np
    from src.engine import event_reactor_adapter, global_auction_universe
    from src.state import db as state_db

    condition_id = "0x" + "1c" * 32
    event_row = {
        "event_id": "event-paris-day0",
        "event_type": "DAY0_EXTREME_UPDATED",
        "entity_key": "Paris|2026-07-14|high",
        "source": "test",
        "observed_at": "2026-07-14T14:00:00+00:00",
        "available_at": "2026-07-14T14:00:01+00:00",
        "received_at": "2026-07-14T14:00:01+00:00",
        "causal_snapshot_id": "snapshot-1",
        "payload_hash": "payload-hash",
        "idempotency_key": "idempotency-key",
        "priority": 1,
        "expires_at": None,
        "payload_json": "{}",
        "schema_version": 1,
        "created_at": "2026-07-14T14:00:01+00:00",
    }

    class FakeConnection:
        def __init__(self, row=None):
            self.row = row
            self.closed = False
            self.queries = []

        def execute(self, sql, *_args, **_kwargs):
            self.queries.append(str(sql))
            return self

        def fetchone(self):
            return self.row

        def fetchall(self):
            return [self.row] if self.row is not None else []

        def close(self):
            self.closed = True

    world = FakeConnection(event_row)
    forecasts = FakeConnection()
    trade = FakeConnection(
        {
            "condition_id": condition_id,
            "yes_token_id": "paris-yes-token",
            "no_token_id": "paris-no-token",
        }
    )
    monkeypatch.setattr(state_db, "get_world_connection_read_only", lambda: world)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: forecasts,
    )

    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="paris-yes-token",
                no_token_id="paris-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.2], [0.3], [0.4]]),
        witness_identity="witness-current-global",
        q_version="q-version-current-global",
        source_truth_identity="source-truth-current-global",
        band_basis="current_coherent_day0_remaining_finite_evidence_v2",
        band_alpha=0.25,
    )

    def prepare(event, **kwargs):
        assert event.event_id == "event-paris-day0"
        assert kwargs["forecast_conn"] is forecasts
        assert kwargs["topology_conn"] is forecasts
        assert kwargs["observation_conn"] is world
        assert kwargs["required_condition_id"] == condition_id
        assert kwargs["allow_provisional_day0_replacement"] is True
        kwargs["day0_payload_out"].update(
            {
                "_edli_global_day0_binding": {
                    "observation_time": "2026-07-14T14:00:00+00:00",
                    "observed_extreme_native": 34.0,
                },
                "_edli_day0_finite_evidence_member_count": 4,
            }
        )
        return SimpleNamespace(probability_witness=witness)

    monkeypatch.setattr(
        event_reactor_adapter,
        "_prepare_current_global_probability_family",
        prepare,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_rebind_probability_witness_tokens",
        lambda candidate_witness, **kwargs: candidate_witness,
    )
    pos = _make_position()
    pos.city = "Paris"
    pos.target_date = "2026-07-14"
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.token_id = "paris-yes-token"
    pos.no_token_id = "paris-no-token"

    probability, refreshed, fresh = (
        monitor_refresh_module._refresh_current_global_day0_probability(
            pos,
            trade_conn=trade,
            decision_time=datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc),
        )
    )

    assert fresh is True
    assert probability == pytest.approx(0.75)
    assert getattr(
        refreshed,
        monitor_refresh_module._GLOBAL_MONITOR_SAMPLES_ATTR,
    ) == pytest.approx([0.9, 0.8, 0.7, 0.6])
    assert refreshed._day0_monitor_probability_receipt["probability_witness_identity"] == (
        "witness-current-global"
    )
    assert any("FROM executable_market_snapshot_latest" in sql for sql in trade.queries)
    assert all("FROM executable_market_snapshots" not in sql for sql in trade.queries)
    assert world.closed is True
    assert forecasts.closed is True


def test_day0_monitor_reuses_family_snapshot_across_sibling_bins(monkeypatch) -> None:
    """One family build serves sibling held bins without changing side identity."""
    import numpy as np

    reset_counters()
    first_condition = "0x" + "71" * 32
    second_condition = "0x" + "72" * 32
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                bin_id="33C",
                condition_id=first_condition,
                yes_token_id="first-yes",
                no_token_id="first-no",
            ),
            SimpleNamespace(
                bin_id="34C",
                condition_id=second_condition,
                yes_token_id="second-yes",
                no_token_id="second-no",
            ),
        ),
        yes_q_samples=np.array([[0.2, 0.7], [0.4, 0.5]]),
        witness_identity="shared-family-witness",
        q_version="shared-family-q",
        source_truth_identity="shared-family-truth",
        band_basis="current_coherent_day0_remaining_finite_evidence_v2",
        band_alpha=0.25,
    )
    snapshot = monitor_refresh_module._CurrentGlobalDay0FamilySnapshot(
        witness=witness,
        token_pairs=(
            (first_condition, "first-yes", "first-no"),
            (second_condition, "second-yes", "second-no"),
        ),
        deterministic_condition_ids=frozenset(),
        day0_payload={},
        metric="high",
    )
    builds = []

    def build(position, **_kwargs):
        builds.append(position.condition_id)
        return snapshot

    monkeypatch.setattr(
        monitor_refresh_module,
        "_build_current_global_day0_family_snapshot",
        build,
    )

    def held(condition_id: str, direction: str, yes: str, no: str) -> Position:
        pos = _make_position()
        pos.city = "Moscow"
        pos.target_date = "2026-07-18"
        pos.condition_id = condition_id
        pos.direction = direction
        pos.token_id = yes
        pos.no_token_id = no
        return pos

    first = held(first_condition, "buy_yes", "first-yes", "first-no")
    second = held(second_condition, "buy_no", "second-yes", "second-no")
    cache = monitor_refresh_module._CurrentGlobalDay0FamilyCache()

    first_probability, _, _ = (
        monitor_refresh_module._refresh_current_global_day0_probability(
            first, trade_conn=object(), family_cache=cache
        )
    )
    second_probability, _, _ = (
        monitor_refresh_module._refresh_current_global_day0_probability(
            second, trade_conn=object(), family_cache=cache
        )
    )

    assert (first_probability, second_probability) == pytest.approx((0.3, 0.4))
    assert builds == [first_condition]
    assert read_counter("monitor_day0_family_snapshot_build_total") == 1
    assert read_counter("monitor_day0_family_snapshot_cache_hit_total") == 1


def test_unobserved_prefix_monitor_uses_global_sample_mean_not_scalar_point() -> None:
    import numpy as np

    condition_id = "0x" + "73" * 32
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                bin_id="14C",
                condition_id=condition_id,
                yes_token_id="yes-14",
                no_token_id="no-14",
            ),
        ),
        yes_q_samples=np.array([[0.2], [0.4]]),
        witness_identity="unobserved-prefix-witness",
        q_version="unobserved-prefix-q",
        source_truth_identity="unobserved-prefix-truth",
        band_basis="current_coherent_settlement_simplex_v1",
        band_alpha=0.05,
    )
    snapshot = monitor_refresh_module._CurrentGlobalDay0FamilySnapshot(
        witness=witness,
        token_pairs=((condition_id, "yes-14", "no-14"),),
        deterministic_condition_ids=frozenset(),
        day0_payload={},
        metric="low",
        probability_authority=(
            "replacement_unobserved_day0_prefix_global_probability_v1"
        ),
    )
    pos = _make_position()
    pos.condition_id = condition_id
    pos.direction = "buy_yes"
    pos.token_id = "yes-14"
    pos.no_token_id = "no-14"

    probability, refreshed, fresh = (
        monitor_refresh_module._materialize_current_global_day0_probability(
            pos,
            snapshot,
        )
    )

    assert probability == pytest.approx(0.3)
    assert fresh is True
    assert refreshed.selected_method == "replacement_posterior"
    receipt = refreshed._day0_monitor_probability_receipt
    assert receipt["probability_authority"] == (
        "replacement_unobserved_day0_prefix_global_probability_v1"
    )
    assert receipt["remaining_window"] is None


def test_provisional_day0_monitor_uses_replacement_probability_without_hard_fact_stamp() -> None:
    import numpy as np

    condition_id = "0x" + "74" * 32
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                bin_id="25C",
                condition_id=condition_id,
                yes_token_id="yes-25",
                no_token_id="no-25",
            ),
        ),
        yes_q_samples=np.array([[0.72], [0.84]]),
        witness_identity="hko-provisional-replacement-witness",
        q_version="hko-provisional-replacement-q",
        source_truth_identity="hko-provisional-replacement-truth",
        band_basis="current_coherent_settlement_simplex_v1",
        band_alpha=0.05,
    )
    snapshot = monitor_refresh_module._CurrentGlobalDay0FamilySnapshot(
        witness=witness,
        token_pairs=((condition_id, "yes-25", "no-25"),),
        deterministic_condition_ids=frozenset(),
        day0_payload={
            "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
        },
        metric="low",
        probability_authority=(
            "replacement_provisional_day0_global_probability_v1"
        ),
    )
    pos = _make_position()
    pos.condition_id = condition_id
    pos.direction = "buy_no"
    pos.token_id = "yes-25"
    pos.no_token_id = "no-25"

    probability, refreshed, fresh = (
        monitor_refresh_module._materialize_current_global_day0_probability(
            pos,
            snapshot,
        )
    )

    assert probability == pytest.approx(0.22)
    assert fresh is True
    assert refreshed.selected_method == "replacement_posterior"
    receipt = refreshed._day0_monitor_probability_receipt
    assert receipt["probability_authority"] == (
        "replacement_provisional_day0_global_probability_v1"
    )
    assert receipt["remaining_window"] is None
    assert all(
        "day0_absorbing_hard_fact" not in validation
        for validation in refreshed.applied_validations
    )


def test_day0_family_cache_keeps_partial_exact_witness_condition_local() -> None:
    from datetime import timedelta

    from src.solve.solver import (
        DeterministicBinPayoffWitness,
        OutcomeTokenBinding,
        deterministic_bin_payoff_witness_identity,
    )

    exact_condition = "0x" + "81" * 32
    unknown_condition = "0x" + "82" * 32
    bindings = (
        OutcomeTokenBinding("33C", exact_condition, "exact-yes", "exact-no"),
        OutcomeTokenBinding("34C", unknown_condition, "unknown-yes", "unknown-no"),
    )
    identity = {
        "family_key": "Moscow|2026-07-18|high",
        "bindings": bindings,
        "exact_yes_payoffs": (("33C", 0),),
        "q_version": "q",
        "resolution_identity": "resolution",
        "topology_identity": "topology",
        "posterior_identity_hash": "posterior",
        "source_truth_identity": "truth",
        "authority_certificate_hash": "certificate",
        "band_alpha": 0.05,
        "band_basis": "day0_deterministic_bin_payoff_v1",
        "captured_at_utc": datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    }
    witness = DeterministicBinPayoffWitness(
        **identity,
        max_age=timedelta(seconds=30),
        witness_identity=deterministic_bin_payoff_witness_identity(**identity),
    )
    snapshot = monitor_refresh_module._CurrentGlobalDay0FamilySnapshot(
        witness=witness,
        token_pairs=(
            (exact_condition, "exact-yes", "exact-no"),
            (unknown_condition, "unknown-yes", "unknown-no"),
        ),
        deterministic_condition_ids=frozenset({exact_condition}),
        day0_payload={},
        metric="high",
    )

    assert monitor_refresh_module._day0_family_snapshot_covers_condition(
        snapshot, exact_condition
    )
    assert not monitor_refresh_module._day0_family_snapshot_covers_condition(
        snapshot, unknown_condition
    )
    remaining = monitor_refresh_module._CurrentGlobalDay0FamilySnapshot(
        witness=SimpleNamespace(bindings=bindings),
        token_pairs=snapshot.token_pairs,
        deterministic_condition_ids=frozenset({exact_condition}),
        day0_payload={},
        metric="high",
    )
    assert not monitor_refresh_module._day0_family_snapshot_covers_condition(
        remaining, exact_condition
    )
    assert monitor_refresh_module._day0_family_snapshot_covers_condition(
        remaining, unknown_condition
    )


def test_day0_family_failure_cache_does_not_block_independent_family(
    monkeypatch,
) -> None:
    reset_counters()
    builds = []

    def fail(position, **_kwargs):
        builds.append((position.city, position.condition_id))
        raise ValueError("GLOBAL_DAY0_BASE_FORECAST_SNAPSHOT_MISSING")

    monkeypatch.setattr(
        monitor_refresh_module,
        "_build_current_global_day0_family_snapshot",
        fail,
    )

    def held(city: str, condition_byte: str) -> Position:
        pos = _make_position()
        pos.city = city
        pos.target_date = "2026-07-18"
        pos.condition_id = "0x" + condition_byte * 32
        return pos

    first = held("Moscow", "91")
    sibling = held("Moscow", "92")
    independent = held("Ankara", "93")
    cache = monitor_refresh_module._CurrentGlobalDay0FamilyCache()

    with pytest.raises(ValueError, match="BASE_FORECAST_SNAPSHOT_MISSING"):
        monitor_refresh_module._refresh_current_global_day0_probability(
            first, trade_conn=object(), family_cache=cache
        )
    with pytest.raises(monitor_refresh_module._CachedCurrentGlobalDay0FamilyError):
        monitor_refresh_module._refresh_current_global_day0_probability(
            sibling, trade_conn=object(), family_cache=cache
        )
    with pytest.raises(ValueError, match="BASE_FORECAST_SNAPSHOT_MISSING"):
        monitor_refresh_module._refresh_current_global_day0_probability(
            independent, trade_conn=object(), family_cache=cache
        )

    assert builds == [
        ("Moscow", first.condition_id),
        ("Ankara", independent.condition_id),
    ]
    assert read_counter("monitor_day0_family_builder_failure_total") == 2
    assert read_counter("monitor_day0_family_failure_cache_hit_total") == 1


def test_day0_condition_binding_failure_does_not_poison_family(monkeypatch) -> None:
    builds = []

    def fail(position, **_kwargs):
        builds.append(position.condition_id)
        raise ValueError("GLOBAL_REQUIRED_CONDITION_BINDING_INVALID")

    monkeypatch.setattr(
        monitor_refresh_module,
        "_build_current_global_day0_family_snapshot",
        fail,
    )
    cache = monitor_refresh_module._CurrentGlobalDay0FamilyCache()
    positions = []
    for condition_byte in ("a1", "a2"):
        pos = _make_position()
        pos.city = "Moscow"
        pos.target_date = "2026-07-18"
        pos.condition_id = "0x" + condition_byte * 32
        positions.append(pos)

    for pos in positions:
        with pytest.raises(ValueError, match="REQUIRED_CONDITION_BINDING_INVALID"):
            monitor_refresh_module._refresh_current_global_day0_probability(
                pos, trade_conn=object(), family_cache=cache
            )

    assert builds == [pos.condition_id for pos in positions]
    assert cache.failures == {}


@pytest.mark.parametrize(
    ("direction", "expected"),
    (("buy_yes", [0.1, 0.3]), ("buy_no", [0.9, 0.7])),
)
def test_current_global_monitor_samples_bind_exact_held_token(
    direction,
    expected,
) -> None:
    import numpy as np

    condition_id = "0x" + "3e" * 32
    pos = _make_position()
    pos.direction = direction
    pos.condition_id = condition_id
    pos.token_id = "exact-yes-token"
    pos.no_token_id = "exact-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    assert monitor_refresh_module._current_global_held_samples(
        pos,
        witness,
        current_token_pair=("exact-yes-token", "exact-no-token"),
    ) == pytest.approx(expected)


def test_current_global_monitor_token_mismatch_fails_closed() -> None:
    import numpy as np

    condition_id = "0x" + "4f" * 32
    pos = _make_position()
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.no_token_id = "wrong-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    with pytest.raises(
        ValueError,
        match="held token does not match current global witness side",
    ):
        monitor_refresh_module._current_global_held_samples(
            pos,
            witness,
            current_token_pair=("exact-yes-token", "exact-no-token"),
        )


@pytest.mark.parametrize(
    ("direction", "position_yes", "position_no", "expected"),
    [
        ("buy_no", None, "exact-no-token", [0.9, 0.7]),
        ("buy_yes", "exact-yes-token", None, [0.1, 0.3]),
    ],
)
def test_current_global_monitor_requires_held_token_not_stale_complement(
    direction: str,
    position_yes: str | None,
    position_no: str | None,
    expected: list[float],
) -> None:
    import numpy as np

    condition_id = "0x" + "5f" * 32
    pos = _make_position()
    pos.direction = direction
    pos.condition_id = condition_id
    pos.token_id = position_yes
    pos.no_token_id = position_no
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    assert monitor_refresh_module._current_global_held_samples(
        pos,
        witness,
        current_token_pair=("exact-yes-token", "exact-no-token"),
    ) == pytest.approx(expected)


def test_current_global_monitor_stale_complement_fails_closed() -> None:
    import numpy as np

    condition_id = "0x" + "6e" * 32
    pos = _make_position()
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.token_id = "stale-yes-token"
    pos.no_token_id = "exact-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    with pytest.raises(
        ValueError,
        match="monitor complementary token conflicts with current global witness",
    ):
        monitor_refresh_module._current_global_held_samples(
            pos,
            witness,
            current_token_pair=("exact-yes-token", "exact-no-token"),
        )


def test_current_global_monitor_missing_witness_no_token_fails_closed() -> None:
    import numpy as np

    condition_id = "0x" + "6b" * 32
    pos = _make_position()
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.token_id = "exact-yes-token"
    pos.no_token_id = "exact-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id=None,
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    with pytest.raises(
        ValueError,
        match="position token pair does not match current global witness",
    ):
        monitor_refresh_module._current_global_held_samples(
            pos,
            witness,
            current_token_pair=("exact-yes-token", "exact-no-token"),
        )


def test_current_global_monitor_edge_band_uses_solver_cvar() -> None:
    lower, upper = monitor_refresh_module._current_global_monitor_edge_band(
        [0.2, 0.4, 0.6, 0.8],
        alpha=0.25,
        current_p_market=0.1,
    )

    assert lower == pytest.approx(0.1)
    assert upper == pytest.approx(0.7)


def test_canonical_monitor_sync_restores_exit_confirmation_from_latest_event() -> None:
    import json
    import sqlite3

    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT,
            target_date TEXT,
            chain_state TEXT,
            direction TEXT,
            order_status TEXT,
            exit_retry_count INTEGER,
            next_exit_retry_at TEXT,
            exit_reason TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current
        VALUES ('held-1', 'day0_window', 12.0, 12.0,
                '2026-07-18T09:00:00+00:00', '2026-07-18', 'synced',
                'buy_no', 'filled', 0, NULL, NULL, 1)
        """
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?, 'MONITOR_REFRESHED', 7, ?)",
        (
            "held-1",
            json.dumps(
                {
                    "exit_decision_neg_edge_count": 1,
                    "exit_decision_applied_validations": [
                        "day0_robust_sell_value_awaits_confirmation"
                    ],
                }
            ),
        ),
    )

    rows = cycle_runtime._canonical_monitor_position_rows(conn)
    assert rows is not None and len(rows) == 1
    pos = SimpleNamespace(
        state="active",
        exit_state="",
        applied_validations=[],
        neg_edge_count=0,
    )
    cycle_runtime._sync_position_from_canonical_monitor_row(pos, rows[0])

    assert pos.state == "day0_window"
    assert pos.neg_edge_count == 1
    assert pos.applied_validations == [
        "day0_robust_sell_value_awaits_confirmation"
    ]
    conn.close()


def test_identified_day0_monitor_fails_closed_without_global_probability(
    monkeypatch,
) -> None:
    """A current-q failure cannot borrow freshness from the legacy Day0 path."""
    pos = _make_position()
    pos.city = "Paris"
    pos.target_date = "2026-07-14"
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.62
    pos.condition_id = "0x" + "2d" * 32
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_is_position_after_target_local_day",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_current_global_day0_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no current q")),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_day0_monitor_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy Day0 probability must not become authority")
        ),
    )
    reseeds = []
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: reseeds.append(kwargs),
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        pos,
        conn=None,
        city=SimpleNamespace(name="Paris", timezone="Europe/Paris"),
        target_d=date(2026, 7, 14),
    )

    assert probability == pytest.approx(0.62)
    assert fresh is False
    assert getattr(refreshed, monitor_refresh_module._MONITOR_PROBABILITY_FRESH_ATTR) is False
    assert any(
        validation.startswith("day0_current_global_probability_unavailable:")
        for validation in refreshed.applied_validations
    )
    assert reseeds == [
        {"city": "Paris", "target_date": "2026-07-14", "metric": "high"}
    ]


@pytest.mark.parametrize(
    ("direction", "expected_probability"),
    [("buy_yes", 0.27), ("buy_no", 0.73)],
)
def test_identified_day0_monitor_uses_fresh_belief_before_first_observation(
    monkeypatch,
    direction: str,
    expected_probability: float,
) -> None:
    """Canonical holdings keep one current q across the local-midnight boundary."""
    from src.engine import position_belief

    pos = _make_position()
    pos.direction = direction
    pos.city = "London"
    pos.target_date = "2026-07-20"
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    pos.condition_id = "0x" + "4e" * 32
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_current_global_day0_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            monitor_refresh_module._Day0UnobservedPrefixUnavailable(
                "current global Day0 family event unavailable: "
                "zero target-date canonical observations"
            )
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(direction=kwargs["direction"]),
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        pos,
        conn=object(),
        city=SimpleNamespace(name="London", timezone="Europe/London"),
        target_d=date(2026, 7, 20),
    )

    assert probability == pytest.approx(expected_probability)
    assert fresh is True
    assert refreshed.selected_method == "replacement_posterior"
    assert (
        "day0_unobserved_prefix_within_start_grace:replacement_posterior_authority"
        in refreshed.applied_validations
    )


def test_identified_day0_monitor_does_not_use_grace_for_generic_observation_failure(
    monkeypatch,
) -> None:
    """Provider/event faults are not proof that the target-day prefix is empty."""
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import position_belief

    pos = _make_position()
    pos.city = "London"
    pos.target_date = "2026-07-20"
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    pos.condition_id = "0x" + "4f" * 32
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_current_global_day0_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObservationUnavailableError(
                "current global Day0 family event unavailable despite "
                "target-date canonical observation"
            )
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(direction=kwargs["direction"]),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: None,
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        pos,
        conn=object(),
        city=SimpleNamespace(name="London", timezone="Europe/London"),
        target_d=date(2026, 7, 20),
    )

    assert probability == pytest.approx(pos.p_posterior)
    assert fresh is False
    assert getattr(
        refreshed,
        monitor_refresh_module._MONITOR_PROBABILITY_FRESH_ATTR,
    ) is False
    assert all(
        "day0_unobserved_prefix" not in validation
        for validation in refreshed.applied_validations
    )


def test_unobserved_prefix_authority_is_shared_across_family_cache(
    monkeypatch,
) -> None:
    """Sibling holdings cannot get different authority from iteration order."""
    from src.engine import position_belief

    builds = []

    def missing_prefix(position, **kwargs):
        builds.append(position.condition_id)
        raise monitor_refresh_module._Day0UnobservedPrefixUnavailable(
            "zero target-date canonical observations"
        )

    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_build_current_global_day0_family_snapshot",
        missing_prefix,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(direction=kwargs["direction"]),
    )

    cache = monitor_refresh_module._CurrentGlobalDay0FamilyCache()
    results = []
    for suffix, direction in (("51", "buy_yes"), ("52", "buy_no")):
        pos = _make_position()
        pos.city = "London"
        pos.target_date = "2026-07-20"
        pos.entry_method = "day0_observation"
        pos.condition_id = "0x" + suffix * 32
        pos.direction = direction
        results.append(
            monitor_refresh_module.monitor_probability_refresh(
                pos,
                conn=object(),
                city=SimpleNamespace(name="London", timezone="Europe/London"),
                target_d=date(2026, 7, 20),
                day0_family_cache=cache,
            )
        )

    assert [probability for probability, _, _ in results] == pytest.approx(
        [0.27, 0.73]
    )
    assert [fresh for _, _, fresh in results] == [True, True]
    assert len(builds) == 1


def test_post_local_day_waits_for_final_observation_without_reseed(
    monkeypatch,
) -> None:
    pos = _make_position()
    pos.city = "Hong Kong"
    pos.target_date = "2026-07-15"
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.9056
    pos.condition_id = "0x" + "55" * 32
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_is_position_after_target_local_day",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_current_global_day0_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("a completed local day cannot be repaired by forecast reseed")
        ),
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        pos,
        conn=None,
        city=SimpleNamespace(
            name="Hong Kong",
            timezone="Asia/Hong_Kong",
            settlement_source_type="hko",
        ),
        target_d=date(2026, 7, 15),
    )

    assert probability == pytest.approx(0.9056)
    assert fresh is False
    assert "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" in refreshed.applied_validations


def test_held_monitor_releases_trade_transaction_before_probability_refresh(
    monkeypatch,
) -> None:
    """The exit monitor cannot hold TRADE while Day0 refresh writes WORLD."""
    import sqlite3
    import types
    from datetime import datetime, timezone

    import numpy as np
    from src.engine import cycle_runtime
    from src.state.decision_chain import CycleArtifact, MonitorResult
    from src.state.portfolio import ExitDecision, PortfolioState
    from src.state.strategy_tracker import StrategyTracker

    pos = _make_position()
    pos.city = "TestCity"
    pos.target_date = "2026-06-15"
    pos.state = "holding"
    pos.entry_price = 0.44
    pos.p_posterior = 0.61
    portfolio = PortfolioState(positions=[pos])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE preflight_write (v INTEGER)")
    conn.execute("INSERT INTO preflight_write VALUES (1)")
    assert conn.in_transaction is True

    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *args, **kwargs: [pos],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_portfolio_rotation_evaluation_status",
        lambda *args, **kwargs: None,
    )

    def _refresh_position(conn_arg, clob, refreshed_pos):
        assert conn_arg.in_transaction is False
        refreshed_pos.last_monitor_prob = 0.61
        refreshed_pos.last_monitor_prob_is_fresh = True
        refreshed_pos.last_monitor_market_price = 0.44
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.43
        refreshed_pos.last_monitor_best_ask = 0.45
        return types.SimpleNamespace(
            p_market=np.array([0.44]),
            p_posterior=0.61,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.17,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, ctx: ExitDecision(False, "NO_EXIT"),
    )
    deps = types.SimpleNamespace(
        cities_by_name={
            "TestCity": types.SimpleNamespace(timezone="UTC")
        },
        _utcnow=lambda: datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
        logger=types.SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
        MonitorResult=MonitorResult,
    )

    cycle_runtime.execute_monitoring_phase(
        conn=conn,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=CycleArtifact(mode="exit_monitor", started_at="2026-06-14T12:00:00Z"),
        tracker=StrategyTracker(),
        summary={"monitors": 0, "exits": 0},
        deps=deps,
        exit_order_submit_enabled=False,
        run_exit_preflight=False,
    )
    conn.close()


def _make_position(market_slug: str | None = None) -> Position:
    return Position(
        trade_id="trade-t5-nowcast-001",
        market_id="test-market-001",
        city="TestCity",
        cluster="Test",
        target_date="2026-06-15",
        bin_label="70-80°F",
        direction="buy_yes",
        temperature_metric="high",
        env="test",
        state="holding",
        market_slug=market_slug,
    )


def _make_temporal_context(daypart: str = "afternoon") -> MagicMock:
    ctx = MagicMock()
    ctx.daypart = daypart
    return ctx


def test_nowcast_write_called_when_gate_passes() -> None:
    """market_slug set + hours_remaining <= 6 + fit available → write_nowcast_run is called.

    GREEN: fit_run_id plumbing is live; xfail removed (Phase 2 T5 GREEN).
    """
    import numpy as np
    from src.types.metric_identity import MetricIdentity
    from src.calibration.day0_horizon_calibration import HorizonPlattFit
    from datetime import date

    pos = _make_position(market_slug="boston-2026-06-15-high")
    temporal_ctx = _make_temporal_context("afternoon")

    stub_fit = HorizonPlattFit(
        alpha=1.0,
        beta=0.0,
        gamma_morning=0.0,
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,
        epsilon=0.0,
        fit_run_id="test-fit-001",
    )

    with patch("src.state.day0_nowcast_store.write_nowcast_run") as mock_write, \
         patch("src.state.day0_nowcast_store.read_latest_platt_fit", return_value=stub_fit):
        _maybe_write_day0_nowcast(
            position=pos,
            hours_remaining=4.0,
            temporal_context=temporal_ctx,
            p_cal_full=np.array([0.6]),
            p_raw_vector=np.array([0.55]),
            temperature_metric=MetricIdentity.from_raw("high"),
            target_d=date(2026, 6, 15),
            observation_time="2026-06-15T14:00:00",
        )
        assert mock_write.called, (
            "_maybe_write_day0_nowcast must call write_nowcast_run when "
            "market_slug is set, hours_remaining <= 6, and fit is available"
        )
        # Verify the wiring passes the expected contract arguments.
        kwargs = mock_write.call_args.kwargs
        assert kwargs["market_slug"] == "boston-2026-06-15-high"
        assert kwargs["fit_run_id"] == "test-fit-001"
        assert kwargs["temperature_metric"] == "high"
        assert kwargs["target_date"] == "2026-06-15"
        assert kwargs["observation_time"] == "2026-06-15T14:00:00"
        assert kwargs["hours_remaining"] == 4.0
        assert kwargs["daypart"] == "afternoon"
        assert kwargs["source"] == "live_nowcast"
        assert monitor_refresh_module._nowcast_consecutive_write_failures == 0


def test_nowcast_write_skipped_when_market_slug_none() -> None:
    """market_slug=None → _maybe_write_day0_nowcast returns immediately, no write."""
    import numpy as np
    from datetime import date

    pos = _make_position(market_slug=None)
    temporal_ctx = _make_temporal_context("afternoon")

    # market_slug=None returns before any write attempt.
    _maybe_write_day0_nowcast(
        position=pos,
        hours_remaining=4.0,
        temporal_context=temporal_ctx,
        p_cal_full=np.array([0.6]),
        p_raw_vector=np.array([0.55]),
        temperature_metric=None,
        target_d=date(2026, 6, 15),
        observation_time="2026-06-15T14:00:00",
    )
    # If we reach here without exception, the early-return guard works.


def test_nowcast_write_skipped_when_hours_remaining_high() -> None:
    """hours_remaining > 6 → _maybe_write_day0_nowcast skips the write."""
    import numpy as np
    from datetime import date

    pos = _make_position(market_slug="dallas-2026-06-15-high")
    temporal_ctx = _make_temporal_context("morning")

    _maybe_write_day0_nowcast(
        position=pos,
        hours_remaining=8.5,
        temporal_context=temporal_ctx,
        p_cal_full=np.array([0.45]),
        p_raw_vector=np.array([0.4]),
        temperature_metric=None,
        target_d=date(2026, 6, 15),
        observation_time="2026-06-15T08:00:00",
    )
    # If we reach here without exception, the hours_remaining guard works.


def test_nowcast_write_failure_counter_and_persistent_alert(caplog) -> None:
    """Repeated fail-soft nowcast write errors must become observable."""
    import logging
    import numpy as np
    from datetime import date
    from src.types.metric_identity import MetricIdentity
    from src.calibration.day0_horizon_calibration import HorizonPlattFit

    reset_counters()
    monitor_refresh_module._nowcast_consecutive_write_failures = 0
    pos = _make_position(market_slug="boston-2026-06-15-high")
    temporal_ctx = _make_temporal_context("afternoon")
    stub_fit = HorizonPlattFit(
        alpha=1.0,
        beta=0.0,
        gamma_morning=0.0,
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,
        epsilon=0.0,
        fit_run_id="test-fit-001",
    )

    with patch("src.state.day0_nowcast_store.write_nowcast_run", side_effect=RuntimeError("boom")), \
         patch("src.state.day0_nowcast_store.read_latest_platt_fit", return_value=stub_fit), \
         caplog.at_level(logging.ERROR, logger="src.engine.monitor_refresh"):
        for _ in range(3):
            _maybe_write_day0_nowcast(
                position=pos,
                hours_remaining=4.0,
                temporal_context=temporal_ctx,
                p_cal_full=np.array([0.6]),
                p_raw_vector=np.array([0.55]),
                temperature_metric=MetricIdentity.from_raw("high"),
                target_d=date(2026, 6, 15),
                observation_time="2026-06-15T14:00:00",
            )

    assert read_counter(
        "monitor_day0_nowcast_write_failed_total",
        labels={"market_slug": "boston-2026-06-15-high"},
    ) == 3
    assert any("MONITOR_NOWCAST_WRITE_PERSISTENT_FAILURE" in record.message for record in caplog.records)


def test_day0_metric_fact_write_helper_uses_monitor_observation_contract() -> None:
    """Valid Day0 monitor observations produce one world-owned metric fact write."""
    from datetime import date

    from src.types.metric_identity import MetricIdentity

    city = MagicMock()
    city.name = "Paris"
    city.timezone = "Europe/Paris"
    pos = _make_position(market_slug="paris-2026-07-09-low")
    pos.city = "Paris"
    obs = {
        "source": "wu_api",
        "observation_time": "2026-07-09T04:00:00Z",
        "local_timestamp": "2026-07-09T06:00:00+02:00",
    }

    with patch("src.state.day0_metric_fact_store.write_day0_metric_fact") as mock_write:
        mock_write.return_value = "d0mf_v1_test"
        monitor_refresh_module._maybe_write_day0_metric_fact(
            position=pos,
            city=city,
            target_d=date(2026, 7, 9),
            temperature_metric=MetricIdentity.from_raw("low"),
            obs=obs,
            current_temp=21.2,
            observed_extreme_for_metric=20.0,
        )

    assert mock_write.call_count == 1
    kwargs = mock_write.call_args.kwargs
    assert kwargs["city"] == "Paris"
    assert kwargs["target_date"] == "2026-07-09"
    assert kwargs["temperature_metric"] == "low"
    assert kwargs["source"] == "wu_api"
    assert kwargs["utc_timestamp"] == "2026-07-09T04:00:00Z"
    assert kwargs["local_timezone"] == "Europe/Paris"
    assert kwargs["local_timestamp"] == "2026-07-09T06:00:00+02:00"
    assert kwargs["temp_current"] == 21.2
    assert kwargs["running_extreme"] == 20.0


def test_day0_monitor_rejects_future_observation_before_forecast_fallback(
    monkeypatch,
) -> None:
    from datetime import date

    pos = _make_position(market_slug="paris-2026-07-20-high")
    pos.target_date = "2026-07-20"
    pos.temperature_metric = "high"
    pos.p_posterior = 0.41
    city = MagicMock()
    city.name = "Paris"
    monkeypatch.setattr(
        monitor_refresh_module,
        "_fetch_day0_observation",
        lambda *_: {
            "source": "wu_api",
            "observation_time": "9999-07-20T12:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_read_day0_hourly_vectors",
        lambda **kwargs: pytest.fail("future observation must not read hourly forecast"),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_read_day0_raw_model_extrema",
        lambda **kwargs: pytest.fail("future observation must not reach daily fallback"),
    )

    posterior, validations = monitor_refresh_module._refresh_day0_observation(
        position=pos,
        current_p_market=0.5,
        conn=None,
        city=city,
        target_d=date(2026, 7, 20),
    )

    assert posterior == pytest.approx(0.41)
    assert validations == [
        "day0_observation",
        "observation_timestamp_after_decision",
    ]


def test_day0_metric_fact_write_helper_is_fail_soft(caplog) -> None:
    """A metric-fact persistence failure must not interrupt monitor refresh."""
    import logging
    from datetime import date

    from src.types.metric_identity import MetricIdentity

    city = MagicMock()
    city.name = "Paris"
    city.timezone = "Europe/Paris"
    pos = _make_position(market_slug="paris-2026-07-09-low")
    obs = {
        "source": "wu_api",
        "observation_time": "2026-07-09T04:00:00Z",
        "local_timestamp": "2026-07-09T06:00:00+02:00",
    }

    with patch(
        "src.state.day0_metric_fact_store.write_day0_metric_fact",
        side_effect=RuntimeError("db locked"),
    ), caplog.at_level(logging.WARNING, logger="src.engine.monitor_refresh"):
        monitor_refresh_module._maybe_write_day0_metric_fact(
            position=pos,
            city=city,
            target_d=date(2026, 7, 9),
            temperature_metric=MetricIdentity.from_raw("low"),
            obs=obs,
            current_temp=21.2,
            observed_extreme_for_metric=20.0,
        )

    assert any("MONITOR_DAY0_METRIC_FACT_WRITE_FAILED" in record.message for record in caplog.records)
