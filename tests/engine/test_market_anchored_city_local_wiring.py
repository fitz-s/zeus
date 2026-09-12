"""City-local calendar identity tests for market-anchored correction."""

# Created: 2026-09-08
# Last reused or audited: 2026-09-11
# Authority basis: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import sqlite3
import threading
import time

import pytest
from types import SimpleNamespace

from src.calibration.market_anchored_live_fit import (
    MarketAnchoredFitProvider,
    _city_local_target_date,
    corrected_probability,
)
from src.calibration.market_anchored_residual import (
    CLIP_D,
    LEAD_BUCKETS,
    P_CLIP_HI,
    P_CLIP_LO,
    ResidualCalibratorArtifact,
    FitRow,
    fit,
)
from src.calibration.market_anchored_live_fit import (
    active_provider_scope,
    get_active_provider,
    load_fit_rows,
    register_active_provider,
)
from src.contracts.payoff_q_correction import (
    CalibrationFitScope, CalibrationPolicySpec, PayoffQCorrectionUnavailable,
)
from src.engine import event_reactor_adapter as adapter
from src.engine import cycle_runner
from src.engine import global_batch_runtime as runtime
from src.state import portfolio as portfolio_module
from src.state.portfolio import ExitContext, Position


def _entry_resolver(world_conn, *, target_context_by_family, **kwargs):
    return runtime._market_anchored_correction_resolver(
        world_conn,
        trade_conn=kwargs.pop("trade_conn", object()),
        forecast_conn=kwargs.pop("forecast_conn", object()),
        target_context_by_family=target_context_by_family,
        prepared_by_family=kwargs.pop("prepared_by_family", {
            family: object() for family in target_context_by_family
        }),
        calibration_scope_resolver=kwargs.pop("calibration_scope_resolver", lambda candidate, prepared: (
            adapter._global_entry_calibration_fit_scope(
                candidate, metric="high", raw_probability_revision="fixture-revision-v1",
            )
        )),
        **kwargs,
    )


def _artifact(*, snapshot, revision="city_local_target_date_v1"):
    return ResidualCalibratorArtifact(
        alpha={"day0": 0.10, "day1": 0.20, "day2": 0.30},
        beta=0.0,
        lambda_=1.0,
        clip_d=CLIP_D,
        p_clip=(P_CLIP_LO, P_CLIP_HI),
        lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-01-01T00:00:00Z",
        n_train=20,
        n_excluded=0,
        excluded_reasons={},
        param_hash="test",
        lead_calendar_revision=revision,
        city_timezone_snapshot=snapshot,
    )


def test_new_york_and_tokyo_use_city_local_target_date_for_serving():
    snapshot = (("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo"))
    artifact = _artifact(snapshot=snapshot)
    target = date(2026, 1, 2)
    ny = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)
    tokyo = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    assert _city_local_target_date(ny, "New York", snapshot) == date(2026, 1, 1)
    assert _city_local_target_date(tokyo, "Tokyo", snapshot) == target
    ny_result = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        city="New York",
        decision_at=ny,
        target_date=target,
        side="YES",
    )
    tokyo_result = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        city="Tokyo",
        decision_at=tokyo,
        target_date=target,
        side="YES",
    )
    assert ny_result and ny_result[1] == "day1"
    assert tokyo_result and tokyo_result[1] == "day0"


def test_training_uses_the_same_city_local_boundary_as_serving():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE settlement_attribution (
        q_in_bin REAL, market_in_bin_prob REAL, settled_in_bin INTEGER,
        decision_posterior_computed_at TEXT, target_date TEXT,
        settled_at TEXT, graded_at TEXT, city TEXT, temperature_metric TEXT,
        traded_bin_label TEXT, direction TEXT)"""
    )
    conn.executemany(
        "INSERT INTO settlement_attribution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                0.9,
                0.35,
                1,
                "2026-01-02T00:30:00+00:00",
                "2026-01-02",
                "2026-01-03T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
                "New York",
                "high",
                "bin-a",
                "buy_yes",
            ),
            (
                0.9,
                0.35,
                1,
                "2026-01-01T15:30:00+00:00",
                "2026-01-02",
                "2026-01-03T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
                "Tokyo",
                "high",
                "bin-b",
                "buy_yes",
            ),
        ],
    )
    rows = load_fit_rows(
        conn,
        training_cutoff=datetime(2026, 1, 4, tzinfo=timezone.utc),
        city_timezone_snapshot=(
            ("New York", "America/New_York"),
            ("Tokyo", "Asia/Tokyo"),
        ),
    )
    assert [row.lead_bucket for row in rows] == ["day1", "day0"]


def test_calendar_revision_and_snapshot_are_part_of_artifact_identity():
    rows = [FitRow(p0=0.3, q_raw=0.4, lead_bucket="day0", y=1)]
    old = fit(rows, lambda_=1.0, training_cutoff="2026-01-01T00:00:00Z")
    local = fit(
        rows,
        lambda_=1.0,
        training_cutoff="2026-01-01T00:00:00Z",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("Tokyo", "Asia/Tokyo"),),
    )
    changed = fit(
        rows,
        lambda_=1.0,
        training_cutoff="2026-01-01T00:00:00Z",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(
            ("Tokyo", "Asia/Tokyo"),
            ("New York", "America/New_York"),
        ),
    )
    assert old.param_hash != local.param_hash
    assert local.param_hash != changed.param_hash
    assert local.city_timezone_snapshot == (("Tokyo", "Asia/Tokyo"),)


def test_dst_and_unmodeled_leads_remain_exact_and_fail_closed():
    snapshot = (("New York", "America/New_York"),)
    assert _city_local_target_date(
        datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), "New York", snapshot
    ) == date(2026, 3, 8)
    artifact = _artifact(snapshot=snapshot)
    decision = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert (
        corrected_probability(
            artifact,
            p0=0.3,
            q_raw=0.4,
            city="New York",
            decision_at=decision,
            target_date=date(2026, 1, 4),
            side="YES",
        )
        is None
    )
    assert (
        corrected_probability(
            artifact,
            p0=0.3,
            q_raw=0.4,
            city="New York",
            decision_at=decision.replace(tzinfo=None),
            target_date=date(2026, 1, 1),
            side="YES",
        )
        is None
    )


def test_old_or_invalid_artifact_identity_cannot_apply():
    old = _artifact(snapshot=(), revision="UNBOUND")
    invalid = _artifact(snapshot=(("New York", "No/Such_Zone"),))
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    for artifact in (old, invalid):
        assert (
            corrected_probability(
                artifact,
                p0=0.3,
                q_raw=0.4,
                city="New York",
                decision_at=now,
                target_date=date(2026, 1, 1),
                side="YES",
            )
            is None
        )
    assert (
        MarketAnchoredFitProvider(
            lambda: None, city_timezones={"New York": "No/Such_Zone"}
        ).artifact(now=now)
        is None
    )


def test_provider_rejects_naive_artifact_time_without_using_cache():
    calls = []
    provider = MarketAnchoredFitProvider(
        lambda: calls.append(True), city_timezones={"Tokyo": "Asia/Tokyo"}
    )
    assert provider.artifact(now=datetime(2026, 1, 1, 12)) is None
    assert calls == []


def test_target_context_uses_exact_payload_city_and_date():
    event = SimpleNamespace()
    contexts = runtime._target_context_by_family(
        {"opaque-family-hash": event},
        payload_reader=lambda _event: {"city": "Tokyo", "target_date": "2026-01-02"},
    )
    assert contexts == {"opaque-family-hash": ("Tokyo", date(2026, 1, 2))}
    missing = runtime._target_context_by_family(
        {"opaque-family-hash": event},
        payload_reader=lambda _event: {"city": " Tokyo ", "target_date": "2026-01-02"},
    )
    assert missing == {"opaque-family-hash": (" Tokyo ", date(2026, 1, 2))}


def test_resolver_snapshots_all_runtime_cities(monkeypatch):
    class CapturingProvider:
        seen = None

        def __init__(self, connect, **kwargs):
            self.__class__.seen = kwargs["city_timezones"]

        def artifact(self, *, now, scope=None, deadline_monotonic=None):
            return None

    monkeypatch.setattr(
        runtime,
        "_market_anchored_correction_resolver",
        runtime._market_anchored_correction_resolver,
    )
    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        CapturingProvider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "Tokyo": SimpleNamespace(timezone="Asia/Tokyo"),
            "New York": SimpleNamespace(timezone="America/New_York"),
            "Broken": SimpleNamespace(timezone="No/Such_Zone"),
        },
    )
    resolver = _entry_resolver(
        object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))}
    )
    assert resolver is not None
    assert CapturingProvider.seen == {
        "Tokyo": "Asia/Tokyo",
        "New York": "America/New_York",
        "Broken": "No/Such_Zone",
    }


@pytest.mark.parametrize("with_manifest", [False, True])
def test_entry_resolver_and_held_exit_use_actual_city_local_callers(monkeypatch, with_manifest):
    artifact = _artifact(
        snapshot=(("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo"))
    )
    if with_manifest:
        from dataclasses import replace
        from src.contracts.payoff_q_correction import CanonicalTrainingManifest

        scope = CalibrationFitScope(
            "high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "fixture-revision-v1",
        )
        manifest = CanonicalTrainingManifest.build(
            scope_hash=scope.as_payload()["scope_hash"],
            corpus_revision="fixture-corpus-v1",
            training_cutoff=artifact.training_cutoff,
            row_count=artifact.n_train,
            event_count=artifact.n_train,
            weight_sum=float(artifact.n_train),
            max_fill_available_at="2025-12-30T00:00:00Z",
            max_label_available_at="2025-12-31T00:00:00Z",
            input_hash="a" * 64,
        )
        artifact = replace(artifact, training_manifest=manifest)

    class StubProvider:
        calibration_policy = CalibrationPolicySpec(
            algorithm_revision="test-algorithm-v1",
            input_revision="test-input-v1",
            metric_pooling="unfiltered_attribution_claims",
            lead_calendar_revision="city_local_target_date_v1",
            lambda_=1.0,
            min_train_weight=20,
            beta_bounds=(0.0, 0.12),
            logit_clip=3.0,
            probability_clip=(0.005, 0.995),
            refit_seconds=21600.0,
        )

        def __init__(self, connect, **kwargs):
            self.city_timezones = kwargs["city_timezones"]

        def artifact(self, *, now, scope=None, deadline_monotonic=None):
            return artifact

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        StubProvider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "New York": SimpleNamespace(timezone="America/New_York"),
            "Tokyo": SimpleNamespace(timezone="Asia/Tokyo"),
        },
    )
    resolver = _entry_resolver(
        object(),
        target_context_by_family={
            "ny": ("New York", date(2026, 1, 2)),
            "tokyo": ("Tokyo", date(2026, 1, 2)),
        },
    )
    ny = SimpleNamespace(family_key="ny", side="YES", bin_id="b", token_id="t", execution_mode="TAKER_LIMIT")
    tokyo = SimpleNamespace(family_key="tokyo", side="YES", bin_id="b", token_id="t", execution_mode="TAKER_LIMIT")
    ny_correction = resolver(
        ny, 0.9, 0.35, datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)
    )
    tokyo_correction = resolver(
        tokyo, 0.9, 0.35, datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    )
    assert ny_correction and ny_correction.lead_bucket == "day1"
    assert tokyo_correction and tokyo_correction.lead_bucket == "day0"
    assert ny_correction.fit_scope == CalibrationFitScope(
        "high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "fixture-revision-v1",
    )
    assert ny_correction.as_cert_fields()["fit_scope"] == ny_correction.fit_scope.as_payload()
    assert ny_correction.calibration_policy is StubProvider.calibration_policy
    assert ny_correction.as_cert_fields()["calibration_policy"] == (
        StubProvider.calibration_policy.as_payload()
    )
    assert ny_correction.training_manifest is artifact.training_manifest
    assert tokyo_correction.training_manifest is artifact.training_manifest
    assert ny_correction.as_cert_fields().get("training_manifest") == (
        artifact.training_manifest.as_payload() if with_manifest else None
    )

    fixed_now = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

    monkeypatch.setattr(portfolio_module, "datetime", FixedDateTime)
    provider = StubProvider(None, city_timezones={})
    monkeypatch.setattr(provider, "artifact", lambda *, now: artifact)
    register_active_provider(provider)
    context = ExitContext(
        fresh_prob=0.9,
        fresh_prob_is_fresh=True,
        current_market_price=0.35,
        current_market_price_is_fresh=True,
        best_bid=0.3,
        best_ask=0.4,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        current_ci=(0.05, 0.95),
        belief_available=True,
    )
    ny_position = Position(
        trade_id="ny",
        market_id="m",
        city="New York",
        cluster="x",
        target_date="2026-01-02",
        bin_label="b",
        direction="buy_yes",
    )
    tokyo_position = Position(
        trade_id="tokyo",
        market_id="m",
        city="Tokyo",
        cluster="x",
        target_date="2026-01-02",
        bin_label="b",
        direction="buy_yes",
    )
    ny_q = ny_position._exit_q_mean_and_source(context)
    tokyo_q = tokyo_position._exit_q_mean_and_source(context)
    assert ny_q[2] == tokyo_q[2] == "market_anchored"
    assert ny_q[0] != tokyo_q[0]
    register_active_provider(None)


def test_snapshot_failure_and_empty_context_leave_monitor_scope_untouched(monkeypatch, caplog):
    stale = object()
    register_active_provider(stale)
    empty = _entry_resolver(object(), target_context_by_family={})
    assert callable(empty)
    with pytest.raises(PayoffQCorrectionUnavailable, match="TARGET_CONTEXT_UNAVAILABLE"):
        empty(SimpleNamespace(family_key="missing"), .8, .4, datetime.now(timezone.utc))
    # Entry resolver state is batch-local; it must not mutate a caller's
    # monitor-scoped provider.
    assert get_active_provider() is stale
    register_active_provider(None)

    register_active_provider(stale)
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: (_ for _ in ()).throw(ValueError("bad city snapshot")),
    )
    unavailable = _entry_resolver(
        object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))}
    )
    with pytest.raises(PayoffQCorrectionUnavailable, match="PROVIDER_UNAVAILABLE"):
        unavailable(SimpleNamespace(family_key="one"), .8, .4, datetime.now(timezone.utc))
    assert get_active_provider() is stale
    register_active_provider(None)
    assert "MARKET_ANCHORED_CITY_SNAPSHOT_UNAVAILABLE:ValueError" in caplog.text


def test_active_provider_scope_is_context_local_and_resets_after_exception():
    provider = object()
    child_values: list[object] = []

    with active_provider_scope(provider):
        assert get_active_provider() is provider
        child = threading.Thread(target=lambda: child_values.append(get_active_provider()))
        child.start()
        child.join()
    assert child_values == [None]
    assert get_active_provider() is None

    try:
        with active_provider_scope(provider):
            raise RuntimeError("probe")
    except RuntimeError:
        pass
    assert get_active_provider() is None


def test_cycle_runner_monitor_wrapper_uses_current_connection_and_cleans_scope(monkeypatch):
    observed: list[object] = []

    def fake_execute(*args, **kwargs):
        observed.append(get_active_provider())
        return False, False

    monkeypatch.setattr(cycle_runner._runtime, "execute_monitoring_phase", fake_execute)
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"city-0": SimpleNamespace(timezone="UTC")},
    )
    conn = sqlite3.connect(":memory:")
    try:
        assert cycle_runner._execute_monitoring_phase(
            conn,
            None,
            None,
            None,
            None,
            {},
            held_position_monitor_budget_seconds=10.0,
        ) == (False, False)
    finally:
        conn.close()
    assert len(observed) == 1
    assert isinstance(observed[0], MarketAnchoredFitProvider)
    assert observed[0]._schema_alias == "world"
    assert get_active_provider() is None


def test_legacy_monitor_warm_close_then_refits_on_attached_trade(
    monkeypatch, tmp_path
):
    """The legacy monitor refits its own expired artifact from fresh WORLD."""

    entry_at = datetime.now(timezone.utc) - timedelta(hours=7)
    target_date = entry_at.date() + timedelta(days=1)
    world_path = tmp_path / "world.db"
    world = sqlite3.connect(world_path)
    world.row_factory = sqlite3.Row
    world.execute(
        """CREATE TABLE settlement_attribution (
            q_in_bin REAL, market_in_bin_prob REAL, settled_in_bin INTEGER,
            direction TEXT, decision_posterior_computed_at TEXT,
            target_date TEXT, settled_at TEXT, graded_at TEXT,
            city TEXT, temperature_metric TEXT, traded_bin_label TEXT
        )"""
    )
    world.executemany(
        "INSERT INTO settlement_attribution VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                0.9,
                0.35,
                index % 2,
                "buy_yes",
                (entry_at - timedelta(days=index % 3)).isoformat(),
                target_date.isoformat(),
                (entry_at - timedelta(days=1)).isoformat(),
                (entry_at - timedelta(days=1)).isoformat(),
                "Chicago",
                "high",
                f"bin-{index}",
            )
            for index in range(40)
        ],
    )
    world.commit()
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"Chicago": SimpleNamespace(timezone="UTC")},
    )

    prior_monitor = MarketAnchoredFitProvider(
        lambda: world, city_timezones={"Chicago": "UTC"},
    )
    assert prior_monitor.artifact(now=entry_at) is not None
    world.close()

    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    observed: list[tuple[object, bool, str]] = []

    def fake_execute(*args, **kwargs):
        context = ExitContext(
            fresh_prob=0.9,
            fresh_prob_is_fresh=True,
            current_market_price=0.35,
            current_market_price_is_fresh=True,
            best_bid=0.3,
            best_ask=0.4,
            market_vig=1.0,
            hours_to_settlement=12.0,
            position_state="holding",
            current_ci=(0.05, 0.95),
            belief_available=True,
        )
        observed.append(
            Position(
                trade_id="current",
                market_id="market",
                city="Chicago",
                cluster="family",
                target_date=target_date.isoformat(),
                bin_label="bin-0",
                direction="buy_yes",
            )._exit_q_mean_and_source(context)
        )
        return False, False

    monkeypatch.setattr(cycle_runner._runtime, "execute_monitoring_phase", fake_execute)
    try:
        assert cycle_runner._execute_monitoring_phase(
            trade,
            None,
            None,
            None,
            None,
            {},
            held_position_monitor_budget_seconds=10.0,
        ) == (False, False)
    finally:
        trade.close()

    assert len(observed) == 1
    assert observed[0][2] == "market_anchored"
    assert observed[0][0] != 0.9
    assert get_active_provider() is None


def test_cycle_runner_monitor_budget_includes_provider_setup_time(monkeypatch):
    observed: dict[str, float] = {}

    class StubProvider:
        _schema_alias = "world"

        def __init__(self, *args, **kwargs):
            time.sleep(0.03)

        def warm(self, *, now, deadline_monotonic):
            observed["warm_remaining"] = deadline_monotonic - time.monotonic()

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.MarketAnchoredFitProvider",
        StubProvider,
    )
    monkeypatch.setattr(
        cycle_runner._runtime,
        "_held_position_monitor_budget_seconds",
        lambda override: 0.05,
    )

    def fake_execute(*args, **kwargs):
        observed["runtime_budget"] = kwargs["held_position_monitor_budget_seconds"]
        return False, False

    monkeypatch.setattr(cycle_runner._runtime, "execute_monitoring_phase", fake_execute)
    conn = sqlite3.connect(":memory:")
    try:
        assert cycle_runner._execute_monitoring_phase(
            conn,
            None,
            None,
            None,
            None,
            {},
            held_position_monitor_budget_seconds=0.05,
        ) == (False, False)
    finally:
        conn.close()

    assert observed["warm_remaining"] < 0.04
    assert observed["runtime_budget"] < 0.04
    assert get_active_provider() is None


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("mode,contract", [
    ("TAKER_LIMIT", "FOK_FULL_OR_ZERO"), ("MAKER_REST", "MAKER_REST"),
])
def test_current_entry_fit_scope_uses_exact_execution_contract(metric, mode, contract):
    scope = adapter._global_entry_calibration_fit_scope(
        SimpleNamespace(action="BUY", execution_mode=mode),
        metric=metric, raw_probability_revision="current-raw-v3",
    )
    assert scope == CalibrationFitScope(metric, mode, contract, "current-raw-v3")


@pytest.mark.parametrize("action,metric,revision,mode", [
    ("SELL", "high", "v3", "TAKER_LIMIT"),
    ("BUY", None, "v3", "TAKER_LIMIT"),
    ("BUY", "high", None, "TAKER_LIMIT"),
    ("BUY", "low", "v3", "UNKNOWN"),
])
def test_unbound_or_sell_candidate_has_no_entry_fit_scope(action, metric, revision, mode):
    assert adapter._global_entry_calibration_fit_scope(
        SimpleNamespace(action=action, execution_mode=mode),
        metric=metric, raw_probability_revision=revision,
    ) is None


def test_entry_resolver_borrows_all_handles_and_passes_deadline_and_scope(monkeypatch):
    seen = {}
    world, trade, forecast = object(), object(), object()
    scope = CalibrationFitScope("low", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "current-raw-v3")

    class Provider:
        def __init__(self, connects, **kwargs):
            seen["connections"] = connects()

        def artifact(self, *, scope, now, deadline_monotonic):
            seen.update(scope=scope, now=now, deadline=deadline_monotonic)
            return None

    monkeypatch.setattr("src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider", Provider)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")})
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    resolver = _entry_resolver(
        world, trade_conn=trade, forecast_conn=forecast,
        target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))},
        calibration_scope_resolver=lambda candidate, prepared: scope, deadline_monotonic=123.0,
    )
    with pytest.raises(PayoffQCorrectionUnavailable, match="SCOPED_FIT_UNAVAILABLE"):
        resolver(SimpleNamespace(family_key="one", side="YES", execution_mode="TAKER_LIMIT"), .8, .4, now)
    assert seen == {"connections": (world, trade, forecast), "scope": scope, "now": now, "deadline": 123.0}


def test_missing_canonical_scope_does_not_construct_legacy_fit(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("selected attribution cannot replace canonical ENTRY evidence")
    monkeypatch.setattr("src.calibration.market_anchored_live_fit.MarketAnchoredFitProvider", forbidden)
    resolver = _entry_resolver(
        object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))},
        calibration_scope_resolver=None,
    )
    assert callable(resolver)
    assert resolver(object(), .8, .4, datetime(2026, 1, 1, tzinfo=timezone.utc)) is None


@pytest.mark.parametrize("with_family_endowment", [False, True])
def test_unavailable_canonical_fit_cannot_size_raw_buy(monkeypatch, with_family_endowment):
    from tests.solve.test_solver_properties import _global_candidate, _global_select, _family_endowment

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        def artifact(self, **kwargs):
            return None

    monkeypatch.setattr("src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider", Provider)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")})
    candidate = _global_candidate(
        candidate_id="unavailable-fit", family="one", side="YES", q=.8,
        levels=((".35", "100"),),
    )
    resolver = _entry_resolver(
        object(), target_context_by_family={"one": ("Tokyo", date(2026, 7, 11))},
    )
    kwargs = {"cap": "60"}
    if with_family_endowment:
        kwargs["family_portfolio_endowment_resolver"] = lambda _: _family_endowment(candidate)
    raw = _global_select((candidate,), **kwargs)
    decision = _global_select((candidate,), payoff_q_correction_resolver=resolver, **kwargs)
    assert raw.candidate is candidate and raw.shares > 0, raw.rejection_reasons
    assert decision.candidate is None and decision.shares == 0
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "CALIBRATED_PAYOFF_Q_UNAVAILABLE:SCOPED_FIT_UNAVAILABLE"
    )


def test_missing_fit_rejects_only_its_cell_and_preserves_calibrated_competitor(monkeypatch):
    from tests.solve.test_solver_properties import _global_candidate, _global_select
    from src.calibration.market_anchored_live_fit import CanonicalMarketAnchoredFitProvider

    policy = CanonicalMarketAnchoredFitProvider(
        lambda: (), city_timezones={"Tokyo": "Asia/Tokyo"},
    ).calibration_policy
    artifact = _artifact(snapshot=(("Tokyo", "Asia/Tokyo"),))

    class Provider:
        calibration_policy = policy

        def __init__(self, *args, **kwargs):
            pass

        def artifact(self, *, scope, **kwargs):
            return None if scope.metric == "high" else artifact

    monkeypatch.setattr("src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider", Provider)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")})
    missing = _global_candidate(candidate_id="no-fit", family="high", side="YES", q=.95, levels=((".35", "100"),))
    qualified = _global_candidate(candidate_id="has-fit", family="low", side="YES", q=.8, levels=((".35", "100"),))
    resolver = _entry_resolver(
        object(), target_context_by_family={key: ("Tokyo", date(2026, 7, 11)) for key in ("high", "low")},
        calibration_scope_resolver=lambda candidate, prepared: adapter._global_entry_calibration_fit_scope(
            candidate, metric=candidate.family_key, raw_probability_revision="raw-v3",
        ),
    )
    decision = _global_select((missing, qualified), cap="60", payoff_q_correction_resolver=resolver)
    assert decision.candidate is qualified and decision.shares > 0, decision.rejection_reasons
    assert decision.payoff_q_correction.fit_scope.metric == "low"
    assert decision.expected_terminal_wealth.win_probability_mean < .8
    assert decision.rejection_reasons[missing.candidate_id].startswith("CALIBRATED_PAYOFF_Q_UNAVAILABLE:")


@pytest.mark.parametrize("warm_raises", [False, True])
def test_entry_warm_does_not_bind_scope_or_grant_fit_authority(monkeypatch, warm_raises):
    calls = []
    prepared_by_family = {}
    at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    scope = CalibrationFitScope("high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "current-v3")

    class Provider:
        def __init__(self, connects, **kwargs):
            calls.append("provider")
        def warm_corpus(self, *, now, deadline_monotonic):
            assert now == at and deadline_monotonic == 123.0
            assert not prepared_by_family
            calls.append("warm")
            if warm_raises:
                raise RuntimeError("temporary input unavailability")
            return True
        def artifact(self, *, scope, now, deadline_monotonic):
            calls.append("artifact")
            return None

    def scope_for(candidate, prepared):
        assert prepared is prepared_by_family["one"]
        calls.append("scope")
        return scope

    monkeypatch.setattr("src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider", Provider)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")})
    resolver = _entry_resolver(object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))},
                               prepared_by_family=prepared_by_family, calibration_scope_resolver=scope_for,
                               warm_corpus_at=at, deadline_monotonic=123.0)
    assert calls == ["provider", "warm"]
    prepared_by_family["one"] = object()
    with pytest.raises(PayoffQCorrectionUnavailable, match="SCOPED_FIT_UNAVAILABLE"):
        resolver(SimpleNamespace(family_key="one", side="YES", execution_mode="TAKER_LIMIT", action="BUY"), .8, .4, at)
    assert calls == ["provider", "warm", "scope", "artifact"]
