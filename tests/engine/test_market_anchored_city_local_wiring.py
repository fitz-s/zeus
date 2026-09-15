"""City-local calendar identity tests for market-anchored correction."""

# Created: 2026-09-08
# Last reused or audited: 2026-09-15
# Authority basis: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
from __future__ import annotations

from dataclasses import asdict, replace
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
    def apply_held(**kwargs):
        corrected = corrected_probability(
            artifact, p0=kwargs["p0"], q_raw=kwargs["raw_q"],
            city=kwargs["city"], target_date=kwargs["target_date"],
            decision_at=kwargs["decision_at"], side=kwargs["side"],
        )
        return SimpleNamespace(corrected_q=corrected[0])

    provider.load = lambda **kwargs: SimpleNamespace(
        family_key="family", bin_id="b", corrected_probability=apply_held,
        fit_scope=CalibrationFitScope("high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "fixture-revision-v1"),
    )
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


@pytest.mark.parametrize("dedicated_reader", (True, False))
def test_cycle_runner_monitor_wrapper_uses_current_connection_and_cleans_scope(monkeypatch, dedicated_reader):
    observed: list[object] = []
    routed = []

    def fake_execute(*args, **kwargs):
        observed.append(get_active_provider())
        routed.append((args[0], kwargs["read_conn"]))
        return False, False

    monkeypatch.setattr(cycle_runner._runtime, "execute_monitoring_phase", fake_execute)
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"city-0": SimpleNamespace(timezone="UTC")},
    )
    conn = sqlite3.connect(":memory:")
    read_conn = sqlite3.connect(":memory:")
    read_conn.execute("PRAGMA query_only=ON")
    try:
        assert cycle_runner._execute_monitoring_phase(
            conn,
            None,
            None,
            None,
            None,
            {},
            held_position_monitor_budget_seconds=10.0,
            **({"read_conn": read_conn} if dedicated_reader else {}),
        ) == (False, False)
    finally:
        conn.close()
        read_conn.close()
    assert len(observed) == 1
    from src.calibration.market_anchored_live_fit import HeldEntryCalibrationProvider

    assert isinstance(observed[0], HeldEntryCalibrationProvider)
    assert observed[0]._trade_conn is conn
    assert observed[0]._world_schema_alias == "world"
    assert routed == [(conn, read_conn if dedicated_reader else None)]
    assert get_active_provider() is None


def test_unscoped_legacy_fit_cannot_authorize_current_held_exit(
    monkeypatch, tmp_path
):
    """A usable attribution fit cannot substitute for the held ENTRY policy."""

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
    assert observed[0][2] == "entry_calibration_unavailable"
    assert observed[0][1] is False
    assert get_active_provider() is None


def test_cycle_runner_monitor_budget_includes_provider_setup_time(monkeypatch):
    observed: dict[str, float] = {}

    class StubProvider:
        _schema_alias = "world"

        def __init__(self, *args, **kwargs):
            time.sleep(0.03)

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.HeldEntryCalibrationProvider",
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


def test_entry_resolver_records_each_consulted_fit_artifact(monkeypatch):
    artifact = _artifact(snapshot=(("Tokyo", "Asia/Tokyo"),))
    policy = CalibrationPolicySpec(
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

    class Provider:
        calibration_policy = policy

        def __init__(self, *args, **kwargs):
            pass

        def artifact(self, **kwargs):
            return artifact

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        Provider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")},
    )
    audit: dict[str, object] = {}
    scope = CalibrationFitScope(
        "high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "current-raw-v3"
    )
    resolver = _entry_resolver(
        object(),
        target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))},
        calibration_scope_resolver=lambda candidate, prepared: scope,
        market_anchored_fit_artifact_audit=audit,
    )

    correction = resolver(
        SimpleNamespace(
            family_key="one",
            side="YES",
            bin_id="bin",
            token_id="token",
            execution_mode="TAKER_LIMIT",
            action="BUY",
        ),
        0.9,
        0.35,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    scope_identity = scope.as_payload()["scope_hash"]
    assert correction is not None
    assert audit["revision"] == "canonical_entry_fit_artifact_audit_v1"
    assert audit["unavailable_scopes"] == {}
    entry = audit["consulted_scopes"][f"{scope_identity}:{artifact.param_hash}"]
    assert entry["status"] == "AVAILABLE"
    assert entry["scope"] == scope.as_payload()
    assert entry["param_hash"] == artifact.param_hash
    assert entry["artifact"] == asdict(artifact)
    assert entry["policy"] == policy.as_payload()


@pytest.mark.parametrize("same_parameters", [False, True])
def test_entry_resolver_keeps_same_scope_refit_artifacts_without_changing_correction(
    monkeypatch, same_parameters,
):
    from src.contracts.payoff_q_correction import CanonicalTrainingManifest

    scope = CalibrationFitScope(
        "high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "current-raw-v3"
    )
    base_artifact = _artifact(snapshot=(("Tokyo", "Asia/Tokyo"),))
    manifests = tuple(
        CanonicalTrainingManifest.build(
            scope_hash=scope.as_payload()["scope_hash"], corpus_revision="fixture-v1",
            training_cutoff=base_artifact.training_cutoff,
            row_count=base_artifact.n_train, event_count=base_artifact.n_train,
            weight_sum=float(base_artifact.n_train),
            max_fill_available_at="2025-12-30T00:00:00Z",
            max_label_available_at="2025-12-31T00:00:00Z", input_hash=char * 64,
        ) for char in ("a", "b")
    )
    artifacts = tuple(
        replace(base_artifact, param_hash=param_hash, training_manifest=manifest)
        for param_hash, manifest in zip(
            ("param-old", "param-old" if same_parameters else "param-new"), manifests,
        )
    )
    calls = 0
    asdict_calls = 0
    original_asdict = runtime.asdict

    def counting_asdict(value):
        nonlocal asdict_calls
        asdict_calls += 1
        return original_asdict(value)

    monkeypatch.setattr(runtime, "asdict", counting_asdict)

    class Provider:
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

        def __init__(self, *args, **kwargs):
            pass

        def artifact(self, **kwargs):
            nonlocal calls
            result = artifacts[min(calls, len(artifacts) - 1)]
            calls += 1
            return result

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        Provider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")},
    )
    audit: dict[str, object] = {}
    resolver = _entry_resolver(
        object(),
        target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))},
        calibration_scope_resolver=lambda candidate, prepared: scope,
        market_anchored_fit_artifact_audit=audit,
    )
    candidate = SimpleNamespace(
        family_key="one",
        side="YES",
        bin_id="bin",
        token_id="token",
        execution_mode="TAKER_LIMIT",
        action="BUY",
    )
    first = resolver(
        candidate,
        0.9,
        0.35,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    second = resolver(
        candidate,
        0.9,
        0.35,
        datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
    )
    third = resolver(
        candidate,
        0.9,
        0.35,
        datetime(2026, 1, 2, 2, tzinfo=timezone.utc),
    )

    scope_identity = scope.as_payload()["scope_hash"]
    consulted = audit["consulted_scopes"]
    assert first is not None and second is not None and third is not None
    assert first.corrected_q == second.corrected_q
    assert first.lead_bucket == second.lead_bucket
    assert first.alpha_lead == second.alpha_lead
    assert len(consulted) == 2
    assert f"{scope_identity}:param-old" in consulted
    if same_parameters:
        assert all(key.startswith(f"{scope_identity}:param-old") for key in consulted)
    else:
        assert f"{scope_identity}:param-new" in consulted
    assert {row["artifact"]["training_manifest"]["input_hash"] for row in consulted.values()} == {"a" * 64, "b" * 64}
    assert all(row["status"] == "AVAILABLE" for row in consulted.values())
    assert asdict_calls == 2
    assert audit["unavailable_scopes"] == {}


def test_entry_resolver_records_unavailable_scope_without_fabricated_artifact(monkeypatch):
    scope = CalibrationFitScope(
        "high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "current-raw-v3"
    )

    class Provider:
        calibration_policy = object()

        def __init__(self, *args, **kwargs):
            pass

        def artifact(self, **kwargs):
            return None

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        Provider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")},
    )
    audit: dict[str, object] = {}
    resolver = _entry_resolver(
        object(),
        target_context_by_family={"missing": ("Tokyo", date(2026, 1, 2))},
        calibration_scope_resolver=lambda candidate, prepared: scope,
        market_anchored_fit_artifact_audit=audit,
    )

    with pytest.raises(PayoffQCorrectionUnavailable, match="SCOPED_FIT_UNAVAILABLE"):
        resolver(
            SimpleNamespace(
                family_key="missing",
                side="YES",
                bin_id="bin",
                token_id="token",
                execution_mode="TAKER_LIMIT",
                action="BUY",
            ),
            0.9,
            0.35,
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

    assert audit["consulted_scopes"] == {}
    unavailable = audit["unavailable_scopes"]
    assert len(unavailable) == 1
    row = next(iter(unavailable.values()))
    assert row["status"] == "UNAVAILABLE"
    assert row["family_key"] == "missing"
    assert row["scope_identity"] == scope.as_payload()["scope_hash"]
    assert row["scope"] == scope.as_payload()
    assert "artifact" not in row


def test_fit_audit_delta_accepts_old_receipt_then_preserves_changed_snapshot():
    import base64
    import hashlib
    import json
    import zlib

    base = {"probability_manifest": [["family", "witness"]]}
    for param in ("first-fit", "refitted"):
        current = {
            "probability_manifest": base["probability_manifest"],
            "market_anchored_fit_artifact_audit": {
                "revision": "canonical_entry_fit_artifact_audit_v1",
                "consulted_scopes": {"scope": {"param_hash": param}},
                "unavailable_scopes": {},
            },
        }
        delta = runtime._json_object_delta_receipt(
            prefix="audit_context", base=base, current=current,
            expected_sha256=hashlib.sha256(runtime._canonical_json_bytes(current)).hexdigest(),
        )
        payload = json.loads(zlib.decompress(base64.b64decode(delta["audit_context_delta_zlib_b64"])))
        assert "market_anchored_fit_artifact_audit" in payload["replacements"]
        base = runtime._apply_json_object_delta(base, payload)
        assert base == current


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

@pytest.mark.parametrize('side', ['YES', 'NO'])
@pytest.mark.parametrize('sell_mode', ['TAKER_LIMIT', 'MAKER_REST'])
@pytest.mark.parametrize('entry_mode', ['TAKER_LIMIT', 'MAKER_REST'])
def test_held_sell_uses_current_fit_under_entry_policy(monkeypatch, side, sell_mode, entry_mode):
    from src.calibration import market_anchored_live_fit as live_fit
    from src.contracts.payoff_q_correction import PayoffQCorrection

    entry_scope = CalibrationFitScope('high', entry_mode, 'MAKER_REST' if entry_mode == 'MAKER_REST' else 'FOK_FULL_OR_ZERO', 'entry-revision')
    policy = live_fit.CanonicalMarketAnchoredFitProvider(
        lambda: (None, None, None), city_timezones={'Tokyo': 'Asia/Tokyo'},
    ).calibration_policy
    artifact = replace(_artifact(snapshot=(('Tokyo', 'Asia/Tokyo'),)), param_hash='current-held-fit')
    calls = []

    def apply(**kwargs):
        calls.append(kwargs)
        q, lead, alpha = corrected_probability(
            artifact, p0=kwargs['p0'], q_raw=kwargs['raw_q'],
            target_date=kwargs['target_date'], side=kwargs['side'],
            city=kwargs['city'], decision_at=kwargs['decision_at'],
        )
        return PayoffQCorrection(
            family_key=kwargs['family_key'], bin_id=kwargs['bin_id'],
            token_id=kwargs['token_id'], side=kwargs['side'],
            raw_q=kwargs['raw_q'], corrected_q=q, p0=kwargs['p0'],
            lead_bucket=lead, alpha_lead=alpha, beta=artifact.beta,
            lambda_=artifact.lambda_, training_cutoff=artifact.training_cutoff,
            n_train=artifact.n_train, param_hash=artifact.param_hash,
            calibration_policy=policy, fit_scope=entry_scope,
        )

    binding = SimpleNamespace(
        fit_scope=entry_scope, calibration_policy=policy, artifact=artifact,
        corrected_probability=apply, adaptive_authority=True, decision_certificate_hash="entry-cert",
    )
    trade, world = object(), object()
    loaded = []

    def load(conn, **kwargs):
        loaded.append((conn, kwargs))
        return binding

    monkeypatch.setattr(live_fit, 'load_held_entry_calibration', load, raising=False)
    fit_provider = SimpleNamespace(calibration_policy=policy)
    refreshed = []
    def at_decision(provider, **kwargs):
        assert provider is fit_provider
        refreshed.append(kwargs)
        return binding
    binding.at_decision = at_decision
    monkeypatch.setattr(live_fit, 'CanonicalMarketAnchoredFitProvider', lambda *_, **__: fit_provider)
    monkeypatch.setattr("src.engine.event_reactor_adapter._prepared_global_probability_semantics_revision", lambda *_: "entry-revision")
    audit = {}
    resolver = _entry_resolver(
        world, trade_conn=trade,
        target_context_by_family={'family': ('Tokyo', date(2026, 1, 2))},
        market_anchored_fit_artifact_audit=audit,
    )
    from decimal import Decimal
    from src.contracts.executable_cost_curve import BookLevel
    from tests.solve.test_solver_properties import _global_sell_candidate, _current_maker_witness
    from src.solve import solver as S
    candidate = _global_sell_candidate(
        candidate_id="entry-anchor-held", family="family", side=side,
        held_q=.83, bids=((".42", "10"),), min_tick=".01",
        required_mode="TAKER_LIMIT", probability_functional="POSTERIOR_PREDICTIVE_MEAN",
    )
    candidate = replace(candidate, native_ask_levels=(BookLevel(Decimal(".55"), Decimal("10")),))
    if sell_mode == "MAKER_REST":
        proposal = S.passive_sell_proposal_curve(candidate.executable_sell_curve, capacity=candidate.held_shares)
        candidate = replace(candidate, execution_mode="MAKER_REST", proposal_sell_curve=proposal,
            fill_probability=1.0, fill_probability_source="current-fill", rest_deadline_minutes=20.0,
            asset_epoch_identity="entry-anchor-epoch")
        fill = _current_maker_witness(candidate, proposal=proposal, asset_epoch="entry-anchor-epoch",
            outcomes=(S.MakerFillOutcome(Decimal("1"), Decimal("1"), Decimal(".43")),))
        candidate = replace(candidate, maker_fill_witness=fill, fill_probability_source=fill.witness_identity)
    expected_anchor = .43 if entry_mode == "MAKER_REST" else .55
    now = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    correction = resolver(candidate, 0.83, 0.42, now)
    assert correction.raw_q == 0.83
    assert correction.p0 == expected_anchor
    assert correction.fit_scope is entry_scope
    assert correction.calibration_policy is policy
    assert correction.corrected_q == pytest.approx(corrected_probability(
        artifact, p0=expected_anchor, q_raw=0.83, city='Tokyo', target_date=date(2026, 1, 2),
        decision_at=now, side=side,
    )[0])
    assert calls[0]['decision_at'] == now
    assert refreshed == [dict(decision_at=now, current_raw_revision="entry-revision", deadline_monotonic=None)]
    assert correction.param_hash == 'current-held-fit'
    assert loaded == [(trade, {
        'position_id': candidate.position_id, 'token_id': candidate.token_id,
        'side': side, 'world_conn': world,
    })]
    recorded = next(iter(audit['consulted_scopes'].values()))
    assert recorded['scope'] == entry_scope.as_payload()
    assert recorded['policy'] == policy.as_payload()
    assert recorded['param_hash'] == 'current-held-fit'


def test_held_sell_missing_entry_policy_cannot_fall_back_to_raw(monkeypatch):
    from src.calibration import market_anchored_live_fit as live_fit

    def missing(*args, **kwargs):
        raise PayoffQCorrectionUnavailable('ENTRY_BINDING_MISSING')

    monkeypatch.setattr(live_fit, 'load_held_entry_calibration', missing, raising=False)
    resolver = _entry_resolver(object(), target_context_by_family={
        'family': ('Tokyo', date(2026, 1, 2)),
    })
    candidate = SimpleNamespace(
        action='SELL', family_key='family', bin_id='bin', token_id='held-token',
        position_id='held-position', side='NO', execution_mode='TAKER_LIMIT',
    )
    with pytest.raises(PayoffQCorrectionUnavailable, match='ENTRY_BINDING_MISSING'):
        resolver(candidate, 0.1, 0.9, datetime(2026, 1, 1, tzinfo=timezone.utc))
