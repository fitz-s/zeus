# Created: 2026-08-27
# Last reused or audited: 2026-09-13
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") — live wiring, fit provider.
"""Tests for src/calibration/market_anchored_live_fit.py.

The provider's whole job is to hand the live path a fit or nothing at all, so
these tests pin the boundary between the two: too little evidence, unreachable
evidence, and evidence that had not settled yet all produce None, while a
sufficient settled sample produces one artifact that is reused until the TTL
expires.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import json
import math
import time
import zlib
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.calibration.market_anchored_live_fit as live_fit
from src.calibration.market_anchored_live_fit import (
    CALIBRATION_ALGORITHM_REVISION,
    CALIBRATION_INPUT_REVISION,
    CALIBRATION_METRIC_POOLING,
    CANONICAL_CALIBRATION_INPUT_REVISION,
    CANONICAL_CALIBRATION_METRIC_POOLING,
    CANONICAL_CORPUS_REVISION,
    CanonicalMarketAnchoredFitProvider,
    MarketAnchoredArtifactCache,
    MarketAnchoredFitProvider,
    _sqlite_fit_deadline,
    corrected_probability,
    load_fit_rows,
)
from src.contracts.payoff_q_correction import (
    CalibrationFitScope,
    CalibrationPolicySpec,
    CanonicalTrainingManifest,
    PayoffQCorrection,
)
from src.calibration.market_anchored_residual import (
    CLIP_D,
    LEGACY_LEAD_BUCKETS,
    LEGACY_LEAD_CALENDAR_REVISION,
    LEAD_BUCKETS,
    LEAD_CALENDAR_REVISION,
    P_CLIP_HI,
    P_CLIP_LO,
    ResidualCalibratorArtifact,
    _param_hash,
)
from src.engine.lifecycle_events import ACTIVE, build_entry_canonical_write
from src.state.portfolio import Position

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
_TEST_CITY_TIMEZONES = {
    **{f"city-{i}": "UTC" for i in range(100)},
    "Warsaw": "Europe/Warsaw",
    "Austin": "America/Chicago",
}


def _memory_db(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            attribution_id TEXT,
            q_in_bin REAL,
            market_in_bin_prob REAL,
            settled_in_bin INTEGER,
            direction TEXT,
            decision_posterior_computed_at TEXT,
            target_date TEXT,
            settled_at TEXT,
            graded_at TEXT,
            city TEXT,
            temperature_metric TEXT,
            traded_bin_label TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO settlement_attribution (
            attribution_id, q_in_bin, market_in_bin_prob, settled_in_bin,
            direction, decision_posterior_computed_at, target_date,
            settled_at, graded_at, city, temperature_metric, traded_bin_label
        ) VALUES (
            :attribution_id, :q_in_bin, :market_in_bin_prob, :settled_in_bin,
            :direction, :decision_posterior_computed_at, :target_date,
            :settled_at, :graded_at, :city, :temperature_metric, :traded_bin_label
        )
        """,
        rows,
    )
    conn.commit()
    return conn


def _row(
    index: int,
    *,
    settled_at: datetime,
    lead_days: int = 1,
    city: str | None = None,
    claim_suffix: object = None,
    claim_index: int | None = None,
) -> dict:
    """One settlement_attribution row. Each row is its own distinct claim by
    default (``city`` keyed on ``index``), matching every pre-existing test's
    assumption that N rows here means N independently-weighted claims. Tests
    of claim-count weighting pass an explicit ``city``/``claim_suffix`` (so
    rows collide on the claim key) AND ``claim_index`` (so re-certifications
    of the same claim also share target_date, since a claim is fixed to one
    (city, target_date, temperature_metric, traded_bin_label, direction) —
    ``index`` alone still varies to keep attribution_id/outcome distinct.
    """
    decision_basis = index if claim_index is None else claim_index
    decision_day = date(2026, 8, 1) + timedelta(days=decision_basis % 5)
    return {
        "attribution_id": f"row-{index}",
        "q_in_bin": 0.9,
        "market_in_bin_prob": 0.35,
        "settled_in_bin": index % 2,
        "direction": "buy_yes",
        "decision_posterior_computed_at": datetime.combine(
            decision_day, datetime.min.time(), tzinfo=timezone.utc
        ).isoformat(),
        "target_date": (decision_day + timedelta(days=lead_days)).isoformat(),
        "settled_at": settled_at.isoformat(),
        "graded_at": settled_at.isoformat(),
        "city": city if city is not None else f"city-{index}",
        "temperature_metric": "high",
        "traded_bin_label": f"bin-{claim_suffix if claim_suffix is not None else index}",
    }


def _settled_rows(count: int, *, settled_at: datetime | None = None) -> list[dict]:
    when = settled_at or (NOW - timedelta(days=3))
    return [_row(i, settled_at=when) for i in range(count)]


def _known_policy(**changes) -> CalibrationPolicySpec:
    values = dict(
        algorithm_revision=CALIBRATION_ALGORITHM_REVISION,
        input_revision=CALIBRATION_INPUT_REVISION,
        metric_pooling=CALIBRATION_METRIC_POOLING,
        lead_calendar_revision="city_local_target_date_v1",
        lambda_=10.0,
        min_train_weight=20,
        beta_bounds=(0.0, 0.12),
        logit_clip=3.0,
        probability_clip=(0.005, 0.995),
        refit_seconds=21600.0,
    )
    values.update(changes)
    return CalibrationPolicySpec(**values)


def test_calibration_policy_is_frozen_and_round_trips_without_fitted_values():
    policy = _known_policy()
    payload = policy.as_payload()
    restored = CalibrationPolicySpec.from_payload(payload)

    assert restored == policy
    assert payload["policy_hash"] == restored.as_payload()["policy_hash"]
    payload["beta_bounds"][0] = 0.01
    assert policy.beta_bounds == (0.0, 0.12)
    with pytest.raises((AttributeError, TypeError)):
        policy.lambda_ = 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(policy_hash="tampered"),
        lambda p: p.update(extra="unknown"),
        lambda p: p.update(min_train_weight=True),
        lambda p: p.update(probability_clip=[0.0, 0.9]),
    ],
)
def test_calibration_policy_rejects_tampered_and_invalid_payloads(mutation):
    payload = _known_policy().as_payload()
    mutation(payload)
    with pytest.raises((TypeError, ValueError, KeyError)):
        CalibrationPolicySpec.from_payload(payload)


def test_calibration_policy_hash_ignores_fitted_values_but_tracks_policy_inputs():
    first = _known_policy()
    second = _known_policy(lambda_=1.0, refit_seconds=3600.0, min_train_weight=21)
    assert first.as_payload()["policy_hash"] != second.as_payload()["policy_hash"]


@pytest.mark.parametrize(
    "changes",
    [
        {"input_revision": "other-input-v1"},
        {"beta_bounds": (0.0, 0.11)},
        {"probability_clip": (0.01, 0.99)},
    ],
)
def test_calibration_policy_hash_tracks_semantic_revision_and_clips(changes):
    assert _known_policy().as_payload()["policy_hash"] != (
        _known_policy(**changes).as_payload()["policy_hash"]
    )


def test_calibration_policy_rejects_noncanonical_integer_hash_and_huge_numeric_value():
    from src.decision_kernel.canonicalization import stable_hash

    payload = _known_policy().as_payload()
    payload["lambda_"] = 10
    payload["policy_hash"] = stable_hash(
        {key: value for key, value in payload.items() if key != "policy_hash"}
    )
    with pytest.raises(ValueError):
        CalibrationPolicySpec.from_payload(payload)

    huge = _known_policy().as_payload()
    huge["lambda_"] = 10**1000
    huge["policy_hash"] = stable_hash(
        {key: value for key, value in huge.items() if key != "policy_hash"}
    )
    with pytest.raises(ValueError):
        CalibrationPolicySpec.from_payload(huge)


def test_fit_returns_none_below_min_train_rows():
    conn = _memory_db(_settled_rows(5))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES)

    assert provider.artifact(now=NOW) is None


def test_fit_produces_artifact_at_min_train_rows():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES)

    artifact = provider.artifact(now=NOW)

    assert artifact is not None
    assert artifact.n_train == 40
    assert set(artifact.alpha) == {"day0", "day1", "day2plus"}


def test_unreachable_database_fails_open_to_none():
    def explode():
        raise sqlite3.OperationalError("unable to open database file")

    provider = MarketAnchoredFitProvider(explode, min_train_rows=1, city_timezones=_TEST_CITY_TIMEZONES)

    assert provider.artifact(now=NOW) is None


def test_rows_settling_after_the_cutoff_never_train():
    """The walk-forward law: an outcome that had not resolved cannot inform."""

    conn = _memory_db(_settled_rows(40, settled_at=NOW + timedelta(days=1)))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=1, city_timezones=_TEST_CITY_TIMEZONES)

    assert provider.artifact(now=NOW) is None

    rows = load_fit_rows(conn, training_cutoff=NOW + timedelta(days=2), city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))
    assert len(rows) == 40


def test_training_cutoff_is_the_fit_instant():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES)

    artifact = provider.artifact(now=NOW)

    assert artifact is not None
    assert artifact.training_cutoff == "2026-08-27T12:00:00Z"


def test_artifact_is_reused_within_ttl_then_refitted():
    conn = _memory_db(_settled_rows(40))
    fits: list[int] = []

    def connect():
        fits.append(1)
        return conn

    provider = MarketAnchoredFitProvider(
        connect, min_train_rows=20, ttl=timedelta(hours=6), city_timezones=_TEST_CITY_TIMEZONES
    )

    first = provider.artifact(now=NOW)
    cached = provider.artifact(now=NOW + timedelta(hours=5, minutes=59))
    assert len(fits) == 1
    assert cached is first

    refit = provider.artifact(now=NOW + timedelta(hours=6, minutes=1))
    assert len(fits) == 2
    assert refit is not None
    assert refit.training_cutoff != first.training_cutoff


def test_policy_identity_is_stable_across_refits_and_tracks_configuration():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(
        lambda: conn,
        min_train_rows=20,
        ttl=timedelta(hours=6),
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    policy_hash = provider.calibration_policy.as_payload()["policy_hash"]
    first = provider.artifact(now=NOW)
    refit = provider.artifact(now=NOW + timedelta(hours=6, minutes=1))
    assert first is not None and refit is not None
    assert first.param_hash != refit.param_hash
    assert provider.calibration_policy.as_payload()["policy_hash"] == policy_hash

    changed = MarketAnchoredFitProvider(
        lambda: conn,
        min_train_rows=21,
        ttl=timedelta(hours=1),
        lambda_=1.0,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    assert changed.calibration_policy.as_payload()["policy_hash"] != (
        provider.calibration_policy.as_payload()["policy_hash"]
    )


def test_backward_time_does_not_reuse_a_future_cached_artifact_then_recovers():
    """A clock moving backward must not serve a fit made in the future."""

    conn = _memory_db(_settled_rows(40, settled_at=NOW - timedelta(minutes=30)))
    fits: list[int] = []

    def connect():
        fits.append(1)
        return conn

    provider = MarketAnchoredFitProvider(
        connect, min_train_rows=20, ttl=timedelta(hours=6), city_timezones=_TEST_CITY_TIMEZONES
    )

    current = provider.artifact(now=NOW)
    assert current is not None
    assert provider.artifact(now=NOW - timedelta(hours=1)) is None
    recovered = provider.artifact(now=NOW)

    assert recovered is not None
    assert len(fits) == 2
    assert recovered is current


def test_earlier_causal_cutoff_refits_when_it_still_has_enough_evidence():
    conn = _memory_db(_settled_rows(40, settled_at=NOW - timedelta(days=3)))
    fits: list[int] = []

    def connect():
        fits.append(1)
        return conn

    provider = MarketAnchoredFitProvider(
        connect, min_train_rows=20, ttl=timedelta(hours=6), city_timezones=_TEST_CITY_TIMEZONES
    )

    current = provider.artifact(now=NOW)
    earlier = provider.artifact(now=NOW - timedelta(days=1))
    recovered = provider.artifact(now=NOW)

    assert current is not None
    assert earlier is not None
    assert earlier.training_cutoff == "2026-08-26T12:00:00Z"
    assert earlier.training_cutoff != current.training_cutoff
    assert recovered is current
    assert len(fits) == 2


def test_failed_fit_is_cached_so_a_dead_db_is_not_redialled_per_candidate():
    attempts: list[int] = []

    def explode():
        attempts.append(1)
        raise sqlite3.OperationalError("database is locked")

    provider = MarketAnchoredFitProvider(explode, min_train_rows=1, city_timezones=_TEST_CITY_TIMEZONES)

    assert provider.artifact(now=NOW) is None
    assert provider.artifact(now=NOW + timedelta(minutes=1)) is None
    assert len(attempts) == 1


def test_shared_cache_reuses_artifact_across_borrowed_connections(monkeypatch):
    conn_a = _memory_db(_settled_rows(40))
    conn_b = _memory_db(_settled_rows(40))
    cache = MarketAnchoredArtifactCache()
    fit_calls: list[int] = []
    original_fit = live_fit.fit

    def counted_fit(*args, **kwargs):
        fit_calls.append(1)
        return original_fit(*args, **kwargs)

    monkeypatch.setattr(live_fit, "fit", counted_fit)
    provider_a = MarketAnchoredFitProvider(
        lambda: conn_a,
        cache=cache,
        db_identity=("world", 1, 1),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    provider_b = MarketAnchoredFitProvider(
        lambda: conn_b,
        cache=cache,
        db_identity=("world", 1, 1),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )

    first = provider_a.artifact(now=NOW)
    second = provider_b.artifact(now=NOW + timedelta(hours=1))

    assert first is not None
    assert second is first
    assert len(fit_calls) == 1


def test_failed_borrowed_connection_does_not_poison_next_provider_cache():
    dead = sqlite3.connect(":memory:")
    dead.close()
    live = _memory_db(_settled_rows(40))
    cache = MarketAnchoredArtifactCache()
    dead_provider = MarketAnchoredFitProvider(
        lambda: dead,
        cache=cache,
        db_identity=("world", 2, 2),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    live_provider = MarketAnchoredFitProvider(
        lambda: live,
        cache=cache,
        db_identity=("world", 2, 2),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )

    assert dead_provider.artifact(now=NOW) is None
    assert live_provider.artifact(now=NOW + timedelta(minutes=1)) is not None


def test_expired_deadline_rejects_even_a_valid_shared_cache_hit():
    conn = _memory_db(_settled_rows(40))
    cache = MarketAnchoredArtifactCache()
    provider = MarketAnchoredFitProvider(
        lambda: conn,
        cache=cache,
        db_identity=("world", 3, 3),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    assert provider.artifact(now=NOW) is not None
    assert (
        provider.artifact(
            now=NOW + timedelta(minutes=1),
            deadline_monotonic=0.0,
        )
        is None
    )


def test_shared_cache_uses_physical_world_identity_across_main_and_world_aliases(
    tmp_path, monkeypatch
):
    world_path = tmp_path / "world.db"
    source = _memory_db(_settled_rows(40))
    world = sqlite3.connect(world_path)
    source.backup(world)
    source.close()
    world.close()

    main = sqlite3.connect(world_path)
    main.row_factory = sqlite3.Row
    attached = sqlite3.connect(":memory:")
    attached.row_factory = sqlite3.Row
    attached.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    cache = MarketAnchoredArtifactCache()
    fit_calls: list[int] = []
    original_fit = live_fit.fit

    def counted_fit(*args, **kwargs):
        fit_calls.append(1)
        return original_fit(*args, **kwargs)

    monkeypatch.setattr(live_fit, "fit", counted_fit)
    try:
        main_provider = MarketAnchoredFitProvider(
            lambda: main,
            cache=cache,
            schema_alias="main",
            min_train_rows=20,
            city_timezones=_TEST_CITY_TIMEZONES,
        )
        world_provider = MarketAnchoredFitProvider(
            lambda: attached,
            cache=cache,
            schema_alias="world",
            min_train_rows=20,
            city_timezones=_TEST_CITY_TIMEZONES,
        )
        first = main_provider.artifact(now=NOW)
        second = world_provider.artifact(now=NOW + timedelta(hours=1))
        assert first is not None
        assert second is first
        assert fit_calls == [1]
    finally:
        attached.close()
        main.close()


def test_shared_cache_isolated_by_fit_configuration():
    conn = _memory_db(_settled_rows(40))
    cache = MarketAnchoredArtifactCache()
    provider_a = MarketAnchoredFitProvider(
        lambda: conn,
        cache=cache,
        db_identity=("world", 4, 4),
        min_train_rows=20,
        lambda_=1.0,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    provider_b = MarketAnchoredFitProvider(
        lambda: conn,
        cache=cache,
        db_identity=("world", 4, 4),
        min_train_rows=20,
        lambda_=2.0,
        city_timezones=_TEST_CITY_TIMEZONES,
    )

    first = provider_a.artifact(now=NOW)
    second = provider_b.artifact(now=NOW)

    assert first is not None
    assert second is not None
    assert second is not first


def test_backward_provider_does_not_hide_newer_shared_artifact():
    conn = _memory_db(_settled_rows(40))
    cache = MarketAnchoredArtifactCache()
    future_provider = MarketAnchoredFitProvider(
        lambda: conn,
        cache=cache,
        db_identity=("world", 5, 5),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    earlier_provider = MarketAnchoredFitProvider(
        lambda: conn,
        cache=cache,
        db_identity=("world", 5, 5),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )

    future = future_provider.artifact(now=NOW)
    earlier = earlier_provider.artifact(now=NOW - timedelta(hours=1))
    recovered = earlier_provider.artifact(now=NOW)

    assert future is not None
    assert earlier is not None
    assert earlier is not future
    assert recovered is future


def test_shared_cache_deadline_bounds_lock_and_late_fit_without_publish():
    cache = MarketAnchoredArtifactCache()
    key = ("bounded",)
    cache._lock.acquire()
    try:
        started = time.monotonic()
        result, _ = cache.get_or_fit(
            key,
            now=NOW,
            ttl=timedelta(hours=1),
            fit_current=lambda: pytest.fail("fit must not run after lock deadline"),
            deadline_monotonic=time.monotonic() + 0.01,
        )
        assert result is None
        assert time.monotonic() - started < 0.2
    finally:
        cache._lock.release()

    def late_fit():
        time.sleep(0.02)
        return object()

    result, _ = cache.get_or_fit(
        key,
        now=NOW,
        ttl=timedelta(hours=1),
        fit_current=late_fit,
        deadline_monotonic=time.monotonic() + 0.005,
    )
    assert result is None
    assert key not in cache._entries


def test_cache_only_provider_serves_warmed_artifact_after_sql_deadline():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(
        lambda: conn,
        cache=MarketAnchoredArtifactCache(),
        db_identity=("world", 6, 6),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
        cache_only=True,
    )
    warmed = provider.warm(
        now=NOW,
        deadline_monotonic=time.monotonic() + 0.2,
    )
    assert warmed is not None
    time.sleep(0.21)

    served_after_sql_cap = provider.artifact(
        now=NOW + timedelta(minutes=1)
    )

    assert served_after_sql_cap is warmed


@pytest.mark.parametrize("slow_stage", ("sql", "fit"))
def test_inmemory_warm_deadline_rejects_late_stage_and_restores_timeout(
    monkeypatch, slow_stage
):
    conn = _memory_db(_settled_rows(40))
    conn.execute("PRAGMA busy_timeout = 1234")
    provider = MarketAnchoredFitProvider(
        lambda: conn,
        cache=MarketAnchoredArtifactCache(),
        min_train_rows=20,
        city_timezones=_TEST_CITY_TIMEZONES,
    )
    if slow_stage == "sql":
        original_load = live_fit.load_fit_rows

        def slow_load(*args, **kwargs):
            time.sleep(0.02)
            return original_load(*args, **kwargs)

        monkeypatch.setattr(live_fit, "load_fit_rows", slow_load)
    else:
        original_fit = live_fit.fit

        def slow_fit(*args, **kwargs):
            time.sleep(0.02)
            return original_fit(*args, **kwargs)

        monkeypatch.setattr(live_fit, "fit", slow_fit)

    result = provider.warm(
        now=NOW,
        deadline_monotonic=time.monotonic() + 0.005,
    )

    assert result is None
    assert provider._artifact is None
    assert provider._fitted_at is None
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_bounded_sqlite_fit_restores_timeout_and_preserves_outer_progress_handler():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA busy_timeout = 1234")
    progress_calls: list[int] = []

    def outer_progress_handler():
        progress_calls.append(1)
        return 0

    conn.set_progress_handler(outer_progress_handler, 1)
    try:
        with _sqlite_fit_deadline(conn, time.monotonic() + 0.2):
            conn.execute("SELECT 1").fetchone()
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
        conn.execute(
            "WITH RECURSIVE scan(value) AS (SELECT 1 UNION ALL "
            "SELECT value + 1 FROM scan WHERE value < 1000) "
            "SELECT SUM(value) FROM scan"
        ).fetchone()
        assert progress_calls
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()


def test_world_alias_reads_canonical_table_when_main_has_same_name(tmp_path):
    world_path = tmp_path / "world.db"
    world = sqlite3.connect(world_path)
    world.row_factory = sqlite3.Row
    world.execute(
        """CREATE TABLE settlement_attribution (
        q_in_bin REAL, market_in_bin_prob REAL, settled_in_bin INTEGER,
        decision_posterior_computed_at TEXT, target_date TEXT,
        settled_at TEXT, graded_at TEXT, city TEXT, temperature_metric TEXT,
        traded_bin_label TEXT, direction TEXT)"""
    )
    world.execute(
        "INSERT INTO settlement_attribution VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (0.8, 0.3, 1, "2026-08-01T00:00:00Z", "2026-08-02", "2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z", "city-0", "high", "bin", "buy_yes"),
    )
    world.commit()
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute("CREATE TABLE settlement_attribution AS SELECT 0.1 AS q_in_bin, 0.1 AS market_in_bin_prob, 0 AS settled_in_bin, NULL AS decision_posterior_computed_at, NULL AS target_date, NULL AS settled_at, NULL AS graded_at, 'wrong' AS city, 'high' AS temperature_metric, 'bin' AS traded_bin_label, 'buy_yes' AS direction WHERE 0")
    trade.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    try:
        rows = load_fit_rows(
            trade,
            training_cutoff=datetime(2026, 8, 4, tzinfo=timezone.utc),
            city_timezone_snapshot=(("city-0", "UTC"),),
            schema_alias="world",
        )
        assert len(rows) == 1
        assert rows[0].q_raw == 0.8
    finally:
        trade.close()
        world.close()


def test_rows_missing_decision_time_or_target_date_are_skipped():
    rows = _settled_rows(4)
    rows[0]["decision_posterior_computed_at"] = None
    rows[1]["target_date"] = None
    conn = _memory_db(rows)

    assert len(load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))) == 2


def test_late_grade_excludes_an_already_settled_current_attribution():
    row = _row(0, settled_at=NOW - timedelta(days=2))
    row["graded_at"] = (NOW + timedelta(minutes=1)).isoformat()
    conn = _memory_db([row])

    assert load_fit_rows(
        conn,
        training_cutoff=NOW,
        city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()),
    ) == []


def test_regrade_version_is_not_reconstructed_from_an_older_grade():
    row = _row(0, settled_at=NOW - timedelta(days=2))
    # The current row represents a regrade written after this fit cutoff. The
    # supersession history is intentionally outside this loader's authority.
    row["graded_at"] = (NOW + timedelta(days=1)).isoformat()
    conn = _memory_db([row])

    assert load_fit_rows(
        conn,
        training_cutoff=NOW,
        city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()),
    ) == []


def test_grade_at_cutoff_is_usable_but_settlement_at_cutoff_remains_strict():
    graded_at_cutoff = _row(0, settled_at=NOW - timedelta(days=1))
    graded_at_cutoff["graded_at"] = NOW.isoformat()
    settled_at_cutoff = _row(1, settled_at=NOW)
    settled_at_cutoff["graded_at"] = (NOW - timedelta(minutes=1)).isoformat()
    conn = _memory_db([graded_at_cutoff, settled_at_cutoff])

    rows = load_fit_rows(
        conn,
        training_cutoff=NOW,
        city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()),
    )

    assert len(rows) == 1


def test_missing_or_invalid_grade_cannot_be_admitted_by_old_settlement_time():
    missing = _row(0, settled_at=NOW - timedelta(days=2))
    missing["graded_at"] = None
    naive = _row(1, settled_at=NOW - timedelta(days=2))
    naive["graded_at"] = "2026-08-20T00:00:00"
    conn = _memory_db([missing, naive])

    assert load_fit_rows(
        conn,
        training_cutoff=NOW,
        city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()),
    ) == []


def test_unmodeled_lead_is_excluded_from_training():
    conn = _memory_db(
        [_row(i, settled_at=NOW - timedelta(days=3), lead_days=-1) for i in range(6)]
    )

    assert load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items())) == []


def test_graded_at_substitutes_for_a_missing_settled_at():
    rows = _settled_rows(2)
    for row in rows:
        row["graded_at"] = row["settled_at"]
        row["settled_at"] = None
    conn = _memory_db(rows)

    assert len(load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))) == 2


def test_malformed_or_naive_settled_at_does_not_fallback_to_graded_at():
    rows = _settled_rows(2)
    rows[0]["graded_at"] = rows[0]["settled_at"]
    rows[0]["settled_at"] = "2026-08-20T00:00:00"
    rows[1]["graded_at"] = rows[1]["settled_at"]
    rows[1]["settled_at"] = "not-a-timestamp"
    conn = _memory_db(rows)

    assert load_fit_rows(
        conn,
        training_cutoff=NOW,
        city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()),
    ) == []


def test_corrected_probability_shrinks_an_overconfident_q_toward_the_market():
    conn = _memory_db(_settled_rows(60))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES)
    artifact = provider.artifact(now=NOW)

    applied = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        city="city-0", decision_at=NOW,
        target_date=date(2026, 8, 28),
        side="YES",
    )

    assert applied is not None
    corrected, lead_bucket, _alpha = applied
    assert lead_bucket == "day1"
    assert 0.0 <= corrected <= 1.0
    # The fitted beta is far below 1, so the corrected value must sit strictly
    # between the market anchor and the raw claim rather than tracking q_raw.
    assert corrected < 0.9


def test_corrected_probability_fails_closed_without_an_artifact():
    assert (
        corrected_probability(
            None,
            p0=0.35,
            q_raw=0.9,
            city="city-0", decision_at=NOW,
            target_date=date(2026, 8, 28),
            side="YES",
        )
        is None
    )


def test_corrected_probability_fails_closed_on_unmodeled_lead():
    conn = _memory_db(_settled_rows(40))
    artifact = MarketAnchoredFitProvider(
        lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES
    ).artifact(now=NOW)
    kwargs = dict(
        artifact=artifact, p0=0.35, q_raw=0.9, city="city-0",
        decision_at=NOW, target_date=date(2026, 8, 28), side="YES",
    )
    assert corrected_probability(**kwargs) is not None
    assert corrected_probability(**{**kwargs, "target_date": date(2026, 8, 26)}) is None


@pytest.mark.parametrize("lead_days", [2, 3, 7])
def test_corrected_probability_v2_serves_all_day2plus_leads(lead_days):
    artifact = _synthetic_artifact(alpha_day1=0.0, alpha_day2plus=0.21, beta=0.08)
    result = corrected_probability(
        artifact, p0=0.3, q_raw=0.6, city="city-0", decision_at=NOW,
        target_date=(NOW.date() + timedelta(days=lead_days)), side="YES",
    )
    assert result is not None
    assert result[1] == "day2plus"


@pytest.mark.parametrize("lead_days", [2, 3, 7])
def test_corrected_probability_v2_day2plus_preserves_no_complement(lead_days):
    artifact = _synthetic_artifact(alpha_day1=0.0, alpha_day2plus=0.21, beta=0.08)
    common = dict(
        city="city-0", decision_at=NOW,
        target_date=NOW.date() + timedelta(days=lead_days),
    )
    yes = corrected_probability(artifact, p0=0.3, q_raw=0.6, side="YES", **common)
    no = corrected_probability(artifact, p0=0.7, q_raw=0.4, side="NO", **common)
    assert yes is not None and no is not None
    assert yes[1] == no[1] == "day2plus"
    assert no[0] == pytest.approx(1.0 - yes[0], abs=1e-12)


def test_corrected_probability_uses_city_local_midnight_and_dst_for_v2_tail():
    artifact = _synthetic_artifact(
        alpha_day1=0.0, alpha_day2plus=0.21, beta=0.08,
        training_cutoff="2026-03-01T00:00:00Z",
        city_timezone_snapshot=(("Warsaw", "Europe/Warsaw"),),
    )
    for decision_at in (
        datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc),
    ):
        result = corrected_probability(
            artifact, p0=0.3, q_raw=0.6, city="Warsaw", decision_at=decision_at,
            target_date=date(2026, 3, 31), side="YES",
        )
        assert result is not None and result[1] == "day2plus"


def test_corrected_probability_v1_artifact_keeps_exact_day2_and_rejects_tail():
    artifact = _synthetic_artifact(
        alpha_day1=0.0, alpha_day2plus=0.17, beta=0.08,
        lead_calendar_revision=LEGACY_LEAD_CALENDAR_REVISION,
    )
    kwargs = dict(
        p0=0.3, q_raw=0.6, city="city-0", decision_at=NOW,
        side="YES",
    )
    exact_two = corrected_probability(
        **kwargs, artifact=artifact, target_date=NOW.date() + timedelta(days=2),
    )
    assert exact_two is not None and exact_two[1] == "day2"
    for lead_days in (3, 7):
        assert corrected_probability(
            **kwargs, artifact=artifact,
            target_date=NOW.date() + timedelta(days=lead_days),
        ) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_corrected_probability_fails_closed_on_non_finite_inputs(bad):
    conn = _memory_db(_settled_rows(40))
    artifact = MarketAnchoredFitProvider(
        lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES
    ).artifact(now=NOW)
    kwargs = dict(
        artifact=artifact, p0=0.35, q_raw=0.9, city="city-0",
        decision_at=NOW, target_date=date(2026, 8, 28), side="YES",
    )
    assert corrected_probability(**kwargs) is not None
    assert corrected_probability(**{**kwargs, "p0": bad}) is None


def _synthetic_artifact(
    *, alpha_day1: float, beta: float, alpha_day2plus: float = 0.0,
    lead_calendar_revision: str = LEAD_CALENDAR_REVISION,
    training_cutoff: str = "2026-08-25T00:00:00Z",
    city_timezone_snapshot: tuple[tuple[str, str], ...] = (("city-0", "UTC"),),
) -> ResidualCalibratorArtifact:
    buckets = (
        LEGACY_LEAD_BUCKETS
        if lead_calendar_revision == LEGACY_LEAD_CALENDAR_REVISION
        else LEAD_BUCKETS
    )
    alpha = {
        bucket: (
            alpha_day1 if bucket == "day1"
            else alpha_day2plus if bucket == "day2plus"
            else 0.0
        )
        for bucket in buckets
    }
    return ResidualCalibratorArtifact(
        alpha=alpha,
        beta=beta,
        lambda_=10.0,
        clip_d=CLIP_D,
        p_clip=(P_CLIP_LO, P_CLIP_HI),
        lead_buckets=buckets,
        training_cutoff=training_cutoff,
        n_train=100,
        n_excluded=0,
        excluded_reasons={},
        param_hash="synthetic",
        lead_calendar_revision=lead_calendar_revision,
        city_timezone_snapshot=city_timezone_snapshot,
    )


def test_corrected_probability_buy_no_is_the_exact_complement_of_buy_yes():
    """buy_no must be algebra-exact against buy_yes in the complemented space.

    q_NO = 1 - q_in and p_NO = 1 - p_in, so applying the (unchanged) in-bin
    artifact to the complemented inputs and complementing back must equal the
    exact complement of the buy_yes result — to floating-point precision, not
    just approximately.
    """
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.08)
    decision_date = date(2026, 8, 26)
    target_date = date(2026, 8, 27)
    p0 = 0.3
    q_raw = 0.6

    yes_applied = corrected_probability(
        artifact,
        p0=p0,
        q_raw=q_raw,
        city="city-0", decision_at=datetime.combine(decision_date, datetime.min.time(), tzinfo=timezone.utc),
        target_date=target_date,
        side="YES",
    )
    no_applied = corrected_probability(
        artifact,
        p0=1.0 - p0,
        q_raw=1.0 - q_raw,
        city="city-0", decision_at=datetime.combine(decision_date, datetime.min.time(), tzinfo=timezone.utc),
        target_date=target_date,
        side="NO",
    )

    assert yes_applied is not None
    assert no_applied is not None
    yes_corrected, yes_lead, yes_alpha = yes_applied
    no_corrected, no_lead, no_alpha = no_applied
    assert no_corrected == pytest.approx(1.0 - yes_corrected, abs=1e-12)
    assert no_lead == yes_lead == "day1"
    assert no_alpha == pytest.approx(-yes_alpha, abs=1e-12)


def test_corrected_probability_alpha_sign_flips_for_buy_no():
    """With beta=0, a positive alpha must pull buy_yes up and buy_no down.

    beta=0 isolates the alpha term: apply_artifact degrades to
    sigmoid(logit(p0) + alpha), so a positive day1 alpha strictly increases
    the corrected probability for buy_yes and strictly decreases it for
    buy_no at the same market price.
    """
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.0)
    decision_date = date(2026, 8, 26)
    target_date = date(2026, 8, 27)
    p0 = 0.3

    yes_corrected, _, _ = corrected_probability(
        artifact,
        p0=p0,
        q_raw=p0,
        city="city-0", decision_at=datetime.combine(decision_date, datetime.min.time(), tzinfo=timezone.utc),
        target_date=target_date,
        side="buy_yes",
    )
    no_corrected, _, _ = corrected_probability(
        artifact,
        p0=p0,
        q_raw=p0,
        city="city-0", decision_at=datetime.combine(decision_date, datetime.min.time(), tzinfo=timezone.utc),
        target_date=target_date,
        side="buy_no",
    )

    assert yes_corrected > p0
    assert no_corrected < p0


def test_corrected_probability_rejects_an_unrecognized_side():
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.08)

    with pytest.raises(ValueError, match="unrecognized side"):
        corrected_probability(
            artifact,
            p0=0.3,
            q_raw=0.6,
            city="city-0", decision_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
            target_date=date(2026, 8, 27),
            side="sell_no",
        )


@pytest.mark.parametrize(
    ("training_cutoff", "side"),
    [
        ("2026-08-28T00:00:00Z", "YES"),
        ("not-a-timestamp", "NO"),
        ("2026-08-26T00:00:00", "YES"),
        ("2026-08-28T00:00:00Z", "NO"),
        ("not-a-timestamp", "YES"),
        ("2026-08-26T00:00:00", "NO"),
    ],
)
def test_corrected_probability_rejects_future_malformed_or_naive_training_cutoff(
    training_cutoff, side
):
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.08)
    assert corrected_probability(
        artifact, p0=0.3, q_raw=0.6, city="city-0",
        decision_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        target_date=date(2026, 8, 28), side=side,
    ) is not None
    artifact = ResidualCalibratorArtifact(
        **{**artifact.__dict__, "training_cutoff": training_cutoff}
    )

    assert corrected_probability(
        artifact,
        p0=0.3,
        q_raw=0.6,
        city="city-0",
        decision_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        target_date=date(2026, 8, 28),
        side=side,
    ) is None


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_corrected_probability_accepts_equal_cutoff_and_equivalent_timezones(side):
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.08)
    utc = corrected_probability(
        artifact,
        p0=0.3,
        q_raw=0.6,
        city="city-0",
        decision_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        target_date=date(2026, 8, 26),
        side=side,
    )
    chicago = corrected_probability(
        artifact,
        p0=0.3,
        q_raw=0.6,
        city="city-0",
        decision_at=datetime(2026, 8, 24, 19, tzinfo=timezone(timedelta(hours=-5))),
        target_date=date(2026, 8, 26),
        side=side,
    )

    assert utc is not None
    assert chicago == utc


def test_corrected_probability_rejects_an_old_artifact_without_cutoff_metadata():
    artifact = _synthetic_artifact(alpha_day1=0.41, beta=0.08)
    legacy = SimpleNamespace(**artifact.__dict__)
    del legacy.training_cutoff

    assert corrected_probability(
        legacy,
        p0=0.3,
        q_raw=0.6,
        city="city-0",
        decision_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        target_date=date(2026, 8, 28),
        side="YES",
    ) is None


# ---------------------------------------------------------------------------
# claim-count weighting: a claim (city, target_date, temperature_metric,
# traded_bin_label, direction) that re-certifies many rows must not
# contribute more than one row's worth of evidence to the fit.
# ---------------------------------------------------------------------------


def test_load_fit_rows_weights_by_reciprocal_claim_count():
    when = NOW - timedelta(days=3)
    # Claim A re-certified 4x (same city/target_date/temp/bin/direction);
    # claim B is a singleton.
    claim_a = [
        _row(i, settled_at=when, city="Warsaw", claim_suffix="A", claim_index=0)
        for i in range(4)
    ]
    claim_b = [
        _row(4, settled_at=when, city="Austin", claim_suffix="B", claim_index=1)
    ]
    conn = _memory_db(claim_a + claim_b)

    rows = load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))

    assert len(rows) == 5
    a_weights = [r.w for r in rows[:4]]
    b_weight = rows[4].w
    assert all(w == pytest.approx(0.25) for w in a_weights)
    assert b_weight == pytest.approx(1.0)
    assert sum(r.w for r in rows) == pytest.approx(2.0)


def test_duplicated_claim_window_refused_though_row_count_meets_floor():
    """20 rows that are really 5 distinct claims re-certified 4x each must be
    refused at min_train_rows=20 (sum(w) == 5), even though len(rows) == 20
    would have passed under the old unweighted floor."""
    when = NOW - timedelta(days=3)
    rows_in = []
    for claim_idx in range(5):
        for cert in range(4):
            rows_in.append(
                _row(
                    claim_idx * 4 + cert,
                    settled_at=when,
                    city=f"city-{claim_idx}",
                    claim_suffix=claim_idx,
                    claim_index=claim_idx,
                )
            )
    conn = _memory_db(rows_in)

    # Sanity: the row-count floor alone would have accepted this sample.
    raw_rows = load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))
    assert len(raw_rows) == 20
    assert sum(r.w for r in raw_rows) == pytest.approx(5.0)

    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20, city_timezones=_TEST_CITY_TIMEZONES)
    assert provider.artifact(now=NOW) is None


def _canonical_corpus_fixture(*, side="YES", corrected=True, metric="high", size=10.0,
                              legacy=False, probability_revision="fixture-revision-v1",
                              return_forecast=False, legacy_maker=False,
                              return_details=False, native_quote_available=True,
                              forecast_lineage=False, child_posterior_id=None,
                              child_global_posterior_id=None, child_posterior_identity=None,
                              include_calibration_policy=True, calibration_policy_payload=None,
                              correction_lead_bucket="day1", correction_alpha_lead=None,
                              unused_large_parent=False, extra_legacy_anchor_edges=False,
                              correction_extra_fields=None, raw_calibration_input_extra_fields=None,
                              uncorrected_raw_q=None):
    """Real certificate hashing and canonical economic revisions in private DBs."""
    import json
    from src.decision_kernel.certificate import build_certificate, certificate_payload_json, ParentEdge
    from src.decision_kernel.ledger import DecisionCertificateLedger

    world = sqlite3.connect(":memory:")
    trade = sqlite3.connect(":memory:")
    decision = NOW - timedelta(days=3)
    parent_decision = decision - timedelta(hours=3) if legacy or forecast_lineage else decision
    parent = build_certificate(
        certificate_type="ProbabilityEvidenceCertificate", semantic_key="fixture-parent",
        claim_type="fixture", mode="LIVE", decision_time=parent_decision,
        source_available_at=parent_decision, agent_received_at=parent_decision, persisted_at=parent_decision,
        payload={}, authority_id="fixture", authority_version="1", algorithm_id="fixture", algorithm_version="1",
    )
    token = "11" if side == "YES" else "12"
    bin_label = "Will Austin be 80°F on 2026-08-28?"
    correction_alpha_lead = (
        correction_alpha_lead
        if correction_alpha_lead is not None
        else (
            math.log(.52 / .48) - math.log(.35 / .65)
            if side == "YES"
            else -(math.log(.52 / .48) - math.log(.65 / .35))
        )
    )
    corrected_payload = {
        "applied": True, "q_raw": .70, "q_corrected": .52, "p0": .35,
        "alpha_lead": correction_alpha_lead,
        "beta": 0.0, "lambda": 10.0,
        "lead_bucket": correction_lead_bucket, "training_cutoff": decision.isoformat(),
        "n_train": 20, "param_hash": "fixture-param",
    }
    if include_calibration_policy:
        corrected_payload["calibration_policy"] = (
            calibration_policy_payload
            if calibration_policy_payload is not None
            else _known_policy().as_payload()
        )
    if correction_extra_fields:
        corrected_payload.update(correction_extra_fields)
    economics = {
        "payoff_q_point": .52 if corrected else .70,
        "market_anchored_correction": ({} if legacy else
                                       (corrected_payload
                                        if corrected else {"applied": False})),
        "global_execution_mode": ("MAKER_REST" if legacy_maker else "TAKER_LIMIT"), "decision_p0": .35,
        "decision_p0_source": "snapshot", "global_book_hash": "book-hash",
        "global_candidate_id": "candidate", "global_token_id": token,
        "global_jit_execution_curve_identity": "curve-hash",
    }
    parents = [parent]
    forecast_conn = None
    include_forecast = legacy or forecast_lineage
    bin_label = "Will Austin be 80°F on 2026-08-28?"
    if legacy:
        economics.update({"q_source": "replacement_0_1", "payoff_q_point": .70})
    elif forecast_lineage:
        raw_token_q = .70 if side == "YES" else .30
        acting_token_q = .52 if side == "YES" else .48
        economics["payoff_q_point"] = acting_token_q if corrected else raw_token_q
        if corrected:
            economics["market_anchored_correction"] = {
                "applied": True, "q_raw": raw_token_q,
                "q_corrected": acting_token_q, "p0": .35,
                "alpha_lead": correction_alpha_lead,
                "beta": 0.0, "lambda": 10.0,
                "lead_bucket": correction_lead_bucket, "training_cutoff": decision.isoformat(),
                "n_train": 20, "param_hash": "fixture-param",
                **(
                    {"calibration_policy": (
                        calibration_policy_payload
                        if calibration_policy_payload is not None
                        else _known_policy().as_payload()
                    )}
                    if include_calibration_policy
                    else {}
                ),
            }
    if include_forecast:
        forecast_payload = {
            "city": "Austin", "target_date": (decision.date()+timedelta(days=1)).isoformat(),
            "metric": metric, "temperature_metric": metric,
            "replacement_posterior_id": 1, "posterior_identity_hash": "posterior-hash",
            "replacement_q": {bin_label: .70},
        }
        forecast_certificate = build_certificate(
            certificate_type="ForecastAuthorityCertificate", semantic_key="fixture-forecast",
            claim_type="fixture", mode="LIVE", decision_time=parent_decision,
            source_available_at=parent_decision, agent_received_at=parent_decision, persisted_at=parent_decision,
            payload=forecast_payload, authority_id="fixture", authority_version="1",
            algorithm_id="fixture", algorithm_version="1",
        )
        parents.append(forecast_certificate)
        for role, ctype, fields in ((
            ("quote_feasibility", "QuoteFeasibilityCertificate", {
                "best_ask": .35, "condition_id": "condition", "token_id": token,
                "selected_token_id": token, "quote_book_condition_id": "condition",
                "quote_book_token_id": token, "quote_depth_hash": "book-hash",
                "cost_source": "native_orderbook_ask", "quote_source_kind": "executable_market_snapshot_native_book",
                "native_quote_available": native_quote_available,
            }),
            ("executable_snapshot", "ExecutableSnapshotCertificate", {
                "condition_id": "condition", "token_id": token,
                "selected_snapshot_id": "snapshot", "orderbook_hash": "book-hash",
                "captured_at": decision.isoformat(),
            }),
            ("candidate", "CandidateEvidenceCertificate", {
                "condition_id": "condition", "selected_token_id": token,
            }),
            ("cost_model", "CostModelCertificate", {
                "condition_id": "condition", "token_id": token,
                "cost_source": "native_orderbook_ask", "quote_source_kind": "executable_market_snapshot_native_book",
            }),
        ) if (legacy or extra_legacy_anchor_edges) else ()):
            parents.append(build_certificate(
                certificate_type=ctype, semantic_key=f"fixture-{role}", claim_type="fixture",
                mode="LIVE", decision_time=parent_decision, source_available_at=parent_decision,
                agent_received_at=parent_decision, persisted_at=parent_decision, payload=fields,
                authority_id="fixture", authority_version="1", algorithm_id="fixture",
                algorithm_version="1",
            ))
    if unused_large_parent:
        parents.append(build_certificate(
            certificate_type="UnusedFixtureCertificate", semantic_key="fixture-unused-large",
            claim_type="fixture", mode="LIVE", decision_time=parent_decision,
            source_available_at=parent_decision, agent_received_at=parent_decision,
            persisted_at=parent_decision, payload={"unused": "x" * 1_000_000},
            authority_id="fixture", authority_version="1", algorithm_id="fixture",
            algorithm_version="1",
        ))
    q_live = (.70 if legacy else (
        ((.52 if side == "YES" else .48) if corrected else (.70 if side == "YES" else .30))
        if forecast_lineage else (.52 if corrected else .70)
    ))
    if uncorrected_raw_q is not None and not corrected:
        q_live = float(uncorrected_raw_q) if side == "YES" else 1.0 - float(uncorrected_raw_q)
        economics["payoff_q_point"] = q_live
    if raw_calibration_input_extra_fields is not None:
        economics.update({
            "global_family_key": "family",
            "global_bin_id": "bin",
            "global_probability_witness_identity": "witness",
            "sample_hash": "sample",
        })
        capture = {
            "schema_version": 1,
            "capture_basis": "GLOBAL_CERTIFICATE_INPUT",
            "p0_basis": "GROSS_NATIVE_TOKEN_PRICE",
            "condition_id": "condition",
            "token_id": token,
            "side": side,
            "candidate_id": "candidate",
            "family_key": "family",
            "bin_id": "bin",
            "probability_witness_identity": "witness",
            "sample_hash": "sample",
            "economic_curve_identity": "curve-hash",
            "execution_mode": economics["global_execution_mode"],
            "correction_applied": economics["market_anchored_correction"].get("applied"),
            "book_snapshot_id": "snapshot",
            "book_hash": "book-hash",
            "raw_q_held": corrected_payload["q_raw"] if corrected else economics["payoff_q_point"],
            "p0_held": .35,
        }
        capture.update(raw_calibration_input_extra_fields)
        economics["raw_calibration_input"] = capture
    payload = {"candidate_id": "candidate", "condition_id": "condition", "token_id": token,
               "q_live": q_live, "direction": "buy_yes" if side == "YES" else "buy_no", "city": "Austin", "target_date": (decision.date()+timedelta(days=1)).isoformat(),
               "temperature_metric": metric, "probability_semantics_revision": probability_revision,
               "qkernel_execution_economics": economics,
               "bin_label": bin_label if include_forecast else None}
    if child_posterior_id is not None:
        payload["posterior_id"] = child_posterior_id
    if child_posterior_identity is not None:
        payload["posterior_identity_hash"] = child_posterior_identity
    if child_global_posterior_id is not None:
        economics["global_posterior_id"] = child_global_posterior_id
    if legacy:
        payload.update({"q_source": "replacement_0_1", "_edli_q_source": "replacement_0_1",
                        "bin_label": bin_label,
                        "executable_snapshot_id": "snapshot",
                        "proof_execution_mode_intent": "MAKER" if legacy_maker else "TAKER",
                        "proof_maker_limit_price": .30})
    parent_edges = ()
    if include_forecast:
        parent_edges = tuple(
            ParentEdge(role, item.certificate_hash, item.certificate_type)
            for role, item in (
                [("forecast_authority", parents[1])]
                + ([("quote_feasibility", parents[2]), ("executable_snapshot", parents[3]),
                    ("candidate", parents[4]), ("cost_model", parents[5])]
                   if (legacy or extra_legacy_anchor_edges) else [])
                + ([("unused_large_parent", parents[-1])] if unused_large_parent else [])
            )
        )
    certificate = build_certificate(
        certificate_type="ActionableTradeCertificate", semantic_key="fixture-entry", claim_type="fixture",
        mode="LIVE", decision_time=decision, source_available_at=decision, agent_received_at=decision,
        persisted_at=decision, payload=payload, parent_edges=parent_edges,
        parent_certificates=tuple(parents),
        authority_id="fixture", authority_version="1", algorithm_id="fixture", algorithm_version="1",
    )
    ledger = DecisionCertificateLedger(world)
    # Fixture isolates corpus validation; execution verifier has its own suite.
    for item in parents:
        ledger.insert_idempotent(item, preverified=True)
    ledger.insert_idempotent(certificate, preverified=True)
    if include_forecast:
        # The fixture's wall clock is later than its historical training
        # cutoff; the production contract requires each parent row's actual
        # created_at to remain before that cutoff.
        world.execute(
            "UPDATE decision_certificates SET created_at=? WHERE certificate_hash IN (%s)"
            % ",".join("?" for _ in parents),
            ((decision - timedelta(hours=2)).isoformat(),
             *[item.certificate_hash for item in parents]),
        )
    trade.executescript("""
      CREATE TABLE venue_commands(command_id TEXT, token_id TEXT, created_at TEXT,
        venue_order_id TEXT, snapshot_id TEXT, intent_kind TEXT, side TEXT,
        envelope_id TEXT);
      CREATE TABLE executable_market_snapshots(snapshot_id TEXT, condition_id TEXT, yes_token_id TEXT,
        no_token_id TEXT, selected_outcome_token_id TEXT, orderbook_top_ask REAL,
        raw_orderbook_hash TEXT, captured_at TEXT, token_map_json TEXT);
      CREATE TABLE position_decision_attribution(command_id TEXT, decision_certificate_hash TEXT,
        intent_kind TEXT, created_at TEXT);
      CREATE TABLE venue_trade_facts(trade_fact_id INTEGER PRIMARY KEY, command_id TEXT, trade_id TEXT,
        venue_order_id TEXT, state TEXT, filled_size REAL, tx_hash TEXT, observed_at TEXT,
        ingested_at TEXT, venue_timestamp TEXT, local_sequence INTEGER, raw_payload_json TEXT);
      CREATE TABLE payout_observations(id INTEGER PRIMARY KEY, condition_id TEXT, outcome_index INTEGER,
        payout_numerator INTEGER, payout_denominator INTEGER, state TEXT, source TEXT,
        block_number INTEGER, block_hash TEXT, observed_at TEXT, superseded_by INTEGER);
      CREATE TABLE venue_submission_envelopes (
        envelope_id TEXT PRIMARY KEY, order_type TEXT, post_only INTEGER);
    """)
    default_order_type = "GTC" if legacy_maker else ("FAK" if legacy else "FOK")
    default_post_only = 1 if legacy_maker else 0
    trade.execute("INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)", ("command", token, decision.isoformat(), "order", "snapshot", "ENTRY", "BUY", "envelope"))
    trade.execute("INSERT INTO venue_submission_envelopes VALUES (?,?,?)", ("envelope", default_order_type, default_post_only))
    trade.execute("INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?,?)", (
        "snapshot", "condition", "11", "12", token, .35, "book-hash", decision.isoformat(), json.dumps({"YES":"11","NO":"12"})))
    trade.execute("INSERT INTO position_decision_attribution VALUES (?,?,?,?)", ("command",certificate.certificate_hash,"ENTRY",decision.isoformat()))
    if include_forecast:
        forecast_conn = sqlite3.connect(":memory:")
        forecast_conn.executescript("""CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY, source_id TEXT, product_id TEXT,
            data_version TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, computed_at TEXT,
            q_json TEXT, provenance_json TEXT, posterior_identity_hash TEXT,
            runtime_layer TEXT, training_allowed INTEGER, recorded_at TEXT)""")
        forecast_conn.execute("INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            1, "source", "replacement_0_1", "v1", "Austin",
            (decision.date()+timedelta(days=1)).isoformat(), metric,
            decision.isoformat(), decision.isoformat(), decision.isoformat(),
            json.dumps({bin_label:.70}),
            json.dumps({
                "probability_semantics_revision": probability_revision,
                "bayes_precision_fusion": {"current_evidence_shape": {
                    "snapshot_id": 101, "shape_hash": "shape-hash",
                    "semantics_revision": probability_revision,
                }},
            } if probability_revision else {
                "bayes_precision_fusion": {"current_evidence_shape": {
                    "snapshot_id": 101, "shape_hash": "shape-hash",
                }},
            }),
            "posterior-hash", "live", 0,
            decision.isoformat()))
    filled = decision+timedelta(seconds=2)
    for fact_id, state, sequence in [(1,"MATCHED",1),(2,"MINED",2),(3,"CONFIRMED",3)]:
        trade.execute("INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            fact_id,"command","fill","order",state,size,"tx",filled.isoformat(),filled.strftime("%Y-%m-%d %H:%M:%S"),filled.isoformat(),sequence,"{}"))
    for index in (0,1):
        trade.execute("INSERT INTO payout_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            index+1,"condition",index,1-index,1,"RESOLVED_NONZERO" if index==0 else "RESOLVED_ZERO",
            "chain_rpc_finalized_v1",100,"0x"+"aa"*32,(NOW-timedelta(days=1)).isoformat(),None))
    world.commit()
    trade.commit()
    result = (world, trade, certificate_payload_json(certificate))
    if return_details:
        return (*result, forecast_conn, certificate, tuple(parents))
    return (*result, forecast_conn) if return_forecast else result


def _read_canonical(world, trade, cutoff=NOW, forecast=None, *, include_cash_proofs=True):
    return live_fit.load_canonical_fit_corpus(world, trade, training_cutoff=cutoff,
        city_timezone_snapshot=tuple(sorted(_TEST_CITY_TIMEZONES.items())), forecast_conn=forecast,
        include_cash_proofs=include_cash_proofs)


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("corrected", [True, False])
def test_canonical_fit_preserves_raw_and_yes_no_event_geometry(side, corrected):
    world, trade, _ = _canonical_corpus_fixture(side=side, corrected=corrected)
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        assert row["q_raw"] == .70
        assert row["acting_q"] == (.52 if corrected else .70)
        assert row["confirmed_shares"] == 10  # Not 30 across lifecycle revisions.
        assert row["fill_proof_tier"] == "CLOB_CONFIRMED"
        fit_row, = corpus.fit_rows(metric="high", execution_mode="TAKER_LIMIT", execution_contract="FOK_FULL_OR_ZERO", probability_revision="fixture-revision-v1")
        assert fit_row.q_raw == pytest.approx(.70 if side=="YES" else .30)
        assert fit_row.p0 == pytest.approx(.35 if side=="YES" else .65)
        assert fit_row.y == 1 and fit_row.w == 1
        assert corpus.fit_rows(metric="low", execution_mode="TAKER_LIMIT", execution_contract="FOK_FULL_OR_ZERO", probability_revision="fixture-revision-v1") == []
        assert corpus.fit_rows(metric="high", execution_mode="MAKER_REST", execution_contract="MAKER_REST", probability_revision="fixture-revision-v1") == []
    finally:
        world.close()
        trade.close()


def _typed_exact_capture_fields(*, payoff=1, content_hash="a" * 64,
                                witness_hash="b" * 64):
    return {
        "probability_input_kind": "TYPED_EXACT_PAYOFF",
        "exact_payoff_content_identity": content_hash,
        "exact_payoff_witness_identity": witness_hash,
        "exact_payoff": payoff,
    }


def test_canonical_fit_excludes_typed_exact_payoff_and_keeps_denominator():
    world, trade, _ = _canonical_corpus_fixture(
        corrected=False,
        raw_calibration_input_extra_fields=_typed_exact_capture_fields(),
        uncorrected_raw_q=1.0,
    )
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.fit_rows(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="FOK_FULL_OR_ZERO",
            probability_revision="fixture-revision-v1",
        ) == []
        assert corpus.command_count == len(corpus.command_accounting) == 1
        assert corpus.unknown == {"TYPED_EXACT_PAYOFF_NOT_STATISTICAL_INPUT": 1}
        assert corpus.command_accounting[0]["calibration_evidence_reason"] == (
            "TYPED_EXACT_PAYOFF_NOT_STATISTICAL_INPUT"
        )
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize(
    "marker",
    [
        {"probability_input_kind": "TYPED_EXACT_PAYOFF",
         "exact_payoff_witness_identity": "b" * 64, "exact_payoff": 1},
        _typed_exact_capture_fields(payoff=0),
    ],
)
def test_canonical_fit_rejects_invalid_typed_exact_capture(marker):
    world, trade, _ = _canonical_corpus_fixture(
        corrected=False, raw_calibration_input_extra_fields=marker,
    )
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.command_count == len(corpus.command_accounting) == 1
        assert corpus.unknown == {"INVALID_TYPED_EXACT_PAYOFF_CAPTURE": 1}
    finally:
        world.close()
        trade.close()


def test_canonical_fit_rejects_typed_exact_capture_when_correction_applied():
    world, trade, _ = _canonical_corpus_fixture(
        corrected=True,
        raw_calibration_input_extra_fields=_typed_exact_capture_fields(),
    )
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.unknown == {"INVALID_TYPED_EXACT_PAYOFF_CAPTURE": 1}
    finally:
        world.close()
        trade.close()


def test_canonical_fit_does_not_exclude_unmarked_probability_one():
    world, trade, _ = _canonical_corpus_fixture(
        corrected=False, uncorrected_raw_q=1.0,
    )
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.unknown == {}
        assert len(corpus.records) == 1
        assert corpus.records[0]["q_raw"] == 1.0
    finally:
        world.close()
        trade.close()


def test_canonical_fit_seals_and_validates_calibration_policy_without_provider_backfill():
    world, trade, _ = _canonical_corpus_fixture(side="YES", corrected=True)
    try:
        corpus = _read_canonical(world, trade)
        row, = corpus.records
        assert row["calibration_policy"] == _known_policy().as_payload()
        assert row["calibration_policy_reason"] is None
    finally:
        world.close()
        trade.close()

    world, trade, _ = _canonical_corpus_fixture(
        side="YES", corrected=True, include_calibration_policy=False
    )
    try:
        corpus = _read_canonical(world, trade)
        assert len(corpus.records) == 1
        assert corpus.records[0]["calibration_policy"] is None
        assert corpus.records[0]["calibration_policy_reason"] == (
            "CALIBRATION_POLICY_MISSING"
        )
    finally:
        world.close()
        trade.close()


def test_canonical_fit_keeps_malformed_calibration_policy_as_attribution_evidence():
    malformed = _known_policy().as_payload()
    malformed["policy_hash"] = "tampered"
    world, trade, _ = _canonical_corpus_fixture(
        side="YES", corrected=True, calibration_policy_payload=malformed
    )
    try:
        corpus = _read_canonical(world, trade)
        assert len(corpus.records) == 1
        assert corpus.records[0]["calibration_policy"] is None
        assert corpus.records[0]["calibration_policy_reason"] == (
            "CALIBRATION_POLICY_INVALID"
        )
        assert corpus.unknown == {}
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize("correction_lead_bucket", [[], "day0"])
def test_canonical_fit_rejects_malformed_or_wrong_city_local_lead_without_dropping_row(
    correction_lead_bucket,
):
    world, trade, _ = _canonical_corpus_fixture(
        side="YES", corrected=True, correction_lead_bucket=correction_lead_bucket
    )
    try:
        corpus = _read_canonical(world, trade)
        assert len(corpus.records) == 1
        assert corpus.records[0]["calibration_policy"] is None
        assert corpus.records[0]["calibration_policy_reason"] == (
            "CALIBRATION_POLICY_INVALID"
        )
        assert corpus.unknown == {}
    finally:
        world.close()
        trade.close()


def test_canonical_fit_rejects_huge_sealed_alpha_without_crashing_or_dropping_row():
    world, trade, _ = _canonical_corpus_fixture(
        side="YES", corrected=True, correction_alpha_lead=10**1000
    )
    try:
        corpus = _read_canonical(world, trade)
        assert len(corpus.records) == 1
        assert corpus.records[0]["calibration_policy_reason"] == (
            "CALIBRATION_POLICY_INVALID"
        )
        assert corpus.unknown == {}
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("corrected", [True, False])
def test_canonical_fit_seals_raw_forecast_lineage_for_each_side_and_revision(side, corrected):
    world, trade, _, forecast, certificate, parents = _canonical_corpus_fixture(
        side=side, corrected=corrected, forecast_lineage=True,
        return_details=True,
    )
    try:
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        lineage = row["raw_forecast_lineage"]
        assert row["raw_forecast_lineage_reason"] is None
        assert lineage["forecast_certificate_hash"] == parents[1].certificate_hash
        assert lineage["posterior_id"] == 1
        assert lineage["posterior_identity_hash"] == "posterior-hash"
        assert lineage["ensemble_snapshot_id"] == 101
        assert lineage["current_evidence_shape_hash"] == "shape-hash"
        assert lineage["probability_revision"] == "fixture-revision-v1"
        assert row["q_raw"] == pytest.approx(.70 if side == "YES" else .30)
        assert row["acting_q"] == pytest.approx(
            (.52 if side == "YES" else .48) if corrected
            else (.70 if side == "YES" else .30)
        )
        # The certificate hash is the immutable parent identity, not a copied
        # posterior string; changing the parent payload must remove lineage.
        assert certificate.certificate_hash == row["certificate_hash"]
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("mutation, expected_reason", [
    (
        "DELETE FROM decision_certificates WHERE certificate_type='ForecastAuthorityCertificate'",
        "FORECAST_LINEAGE_PARENT_UNBOUND",
    ),
    (
        "UPDATE forecast_posteriors SET q_json=?",
        "FORECAST_LINEAGE_Q_UNBOUND",
    ),
    (
        "UPDATE forecast_posteriors SET source_available_at=?",
        "FORECAST_LINEAGE_CLOCK_UNBOUND",
    ),
    (
        "UPDATE forecast_posteriors SET provenance_json=?",
        "FORECAST_LINEAGE_SHAPE_UNBOUND",
    ),
])
def test_canonical_fit_lineage_gaps_keep_accepted_record_and_reason(mutation, expected_reason):
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        if mutation.startswith("DELETE"):
            world.execute(mutation)
        elif "q_json" in mutation:
            forecast.execute(mutation, (json.dumps({"Will Austin be 80°F on 2026-08-28?": .69}),))
        elif "source_available_at" in mutation:
            forecast.execute(mutation, ((NOW + timedelta(hours=1)).isoformat(),))
        else:
            forecast.execute(mutation, (json.dumps({
                "probability_semantics_revision": "fixture-revision-v1",
                "bayes_precision_fusion": {},
            }),))
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == expected_reason
        assert row["q_raw"] == pytest.approx(.70)
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_lineage_rejects_tampered_forecast_parent_hash_without_dropping_record():
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        world.execute(
            "UPDATE decision_certificates SET payload_json='{}' "
            "WHERE certificate_type='ForecastAuthorityCertificate'"
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == "FORECAST_LINEAGE_PARENT_UNBOUND"
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_lineage_normalizes_sqlite_recorded_at_clock():
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET recorded_at=?",
            ("2026-08-24 12:00:00",),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        row, = corpus.records
        assert row["raw_forecast_lineage_reason"] is None
        assert row["raw_forecast_lineage"]["recorded_at"] == "2026-08-24T12:00:00+00:00"
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("kwargs", [
    {"child_posterior_id": 2},
    {"child_global_posterior_id": 2},
    {"child_posterior_id": 1, "child_global_posterior_id": 2},
    {"child_posterior_identity": "wrong-identity"},
])
def test_canonical_fit_lineage_rejects_child_forecast_identity_mismatch(kwargs):
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True, **kwargs,
    )
    try:
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.unknown == {}
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == "FORECAST_LINEAGE_IDENTITY_UNBOUND"
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("training_allowed", [None, 0.5, 1.5, 1])
def test_canonical_fit_lineage_invalid_training_metadata_stays_accepted(training_allowed):
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET training_allowed=?", (training_allowed,)
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == "FORECAST_LINEAGE_METADATA_UNBOUND"
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_lineage_rejects_shape_revision_mismatch():
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET provenance_json=?",
            (json.dumps({
                "probability_semantics_revision": "fixture-revision-v1",
                "bayes_precision_fusion": {"current_evidence_shape": {
                    "snapshot_id": 101, "shape_hash": "shape-hash",
                    "semantics_revision": "other-revision",
                }},
            }),),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == "FORECAST_LINEAGE_REVISION_UNBOUND"
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("snapshot_id", [True, 0, -1, 0.5, "", float("nan")])
def test_canonical_fit_lineage_requires_positive_integer_snapshot_id(snapshot_id):
    world, trade, _, forecast = _canonical_corpus_fixture(
        corrected=True, forecast_lineage=True, return_forecast=True,
    )
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET provenance_json=?",
            (json.dumps({
                "probability_semantics_revision": "fixture-revision-v1",
                "bayes_precision_fusion": {"current_evidence_shape": {
                    "snapshot_id": snapshot_id, "shape_hash": "shape-hash",
                    "semantics_revision": "fixture-revision-v1",
                }},
            }),),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        row, = corpus.records
        assert row["raw_forecast_lineage"] is None
        assert row["raw_forecast_lineage_reason"] == "FORECAST_LINEAGE_SHAPE_UNBOUND"
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize(
    "legacy,legacy_maker,order_type,post_only,expected",
    [
        (False, False, "FOK", 0, "FOK_FULL_OR_ZERO"),
        (False, False, "FAK", 0, "FAK_PARTIAL"),
        (True, True, "GTC", 1, "MAKER_REST"),
    ],
)
def test_canonical_fit_seals_execution_contract_from_same_command_envelope(
    legacy, legacy_maker, order_type, post_only, expected,
):
    world, trade, _, forecast = _canonical_corpus_fixture(
        legacy=legacy, legacy_maker=legacy_maker, return_forecast=True,
    )
    try:
        trade.execute(
            "UPDATE venue_submission_envelopes SET order_type=?, post_only=?",
            (order_type, post_only),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        assert corpus.records[0]["execution_contract"] == expected
        rows = corpus.fit_rows(
            metric="high", execution_mode=corpus.records[0]["execution_mode"],
            execution_contract=expected, probability_revision="fixture-revision-v1",
        )
        assert len(rows) == 1
    finally:
        world.close()
        trade.close()
        if forecast is not None:
            forecast.close()


@pytest.mark.parametrize("mutation,reason", [
    ("DELETE FROM venue_submission_envelopes", "EXECUTION_CONTRACT_ENVELOPE_MISSING"),
    (
        "UPDATE venue_submission_envelopes SET order_type='GTC', post_only=1",
        "EXECUTION_CONTRACT_ENVELOPE_MISMATCH",
    ),
])
def test_canonical_fit_missing_or_mismatched_envelope_stays_in_unknown_denominator(
    mutation, reason,
):
    world, trade, _ = _canonical_corpus_fixture()
    try:
        trade.execute(mutation)
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.command_count == 1
        assert corpus.unknown == {reason: 1}
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize(
    "post_only,reason",
    [
        (0.5, "EXECUTION_CONTRACT_ENVELOPE_INVALID"),
        (1.5, "EXECUTION_CONTRACT_ENVELOPE_INVALID"),
        (float("inf"), "EXECUTION_CONTRACT_ENVELOPE_INVALID"),
        (float("nan"), "EXECUTION_CONTRACT_ENVELOPE_MISSING"),
        ("NaN", "EXECUTION_CONTRACT_ENVELOPE_INVALID"),
        ("other", "EXECUTION_CONTRACT_ENVELOPE_INVALID"),
    ],
)
def test_canonical_fit_invalid_post_only_stays_unknown_without_lossy_coercion(post_only, reason):
    world, trade, _ = _canonical_corpus_fixture()
    try:
        trade.execute(
            "UPDATE venue_submission_envelopes SET post_only=?",
            (post_only,),
        )
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.command_count == 1
        assert corpus.unknown == {reason: 1}
    finally:
        world.close()
        trade.close()


def test_canonical_fit_contract_selection_does_not_mix_fok_and_fak():
    records = tuple(dict(
        metric="high", execution_mode="TAKER_LIMIT", execution_contract=contract,
        event_key=("Austin", f"2026-08-0{index + 1}", "high"),
        lead_bucket="day1", side="YES", p0=.4, q_raw=q, payout=1,
        confirmed_shares=10, raw_probability_revision="fixture-revision-v1",
    ) for index, (contract, q) in enumerate((("FOK_FULL_OR_ZERO", .6), ("FAK_PARTIAL", .7))))
    corpus = live_fit.CanonicalFitCorpus(records, {}, 2, NOW.isoformat())
    assert [row.q_raw for row in corpus.fit_rows(
        metric="high", execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO", probability_revision="fixture-revision-v1",
    )] == [.6]
    assert [row.q_raw for row in corpus.fit_rows(
        metric="high", execution_mode="TAKER_LIMIT",
        execution_contract="FAK_PARTIAL", probability_revision="fixture-revision-v1",
    )] == [.7]
    with pytest.raises(ValueError, match="execution_contract"):
        corpus.fit_rows(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="MAKER_REST", probability_revision="fixture-revision-v1",
        )


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("legacy_maker", [False, True])
@pytest.mark.parametrize("native_quote_available", [True, False])
def test_canonical_fit_recovers_legacy_replacement_raw_and_anchor(
    side, legacy_maker, native_quote_available,
):
    world, trade, _, forecast = _canonical_corpus_fixture(
        side=side, legacy=True, legacy_maker=legacy_maker,
        native_quote_available=native_quote_available, return_forecast=True,
    )
    try:
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.command_count == 1 and corpus.unknown == {}
        row, = corpus.records
        assert row["q_raw"] == pytest.approx(.70 if side == "YES" else .30)
        assert row["raw_probability_revision"] == "fixture-revision-v1"
        assert row["execution_mode"] == ("MAKER_REST" if legacy_maker else "TAKER_LIMIT")
        assert row["p0"] == pytest.approx(.30 if legacy_maker else (.35 if side == "YES" else .35))
        rows = corpus.fit_rows(
            metric="high", execution_mode=row["execution_mode"],
            execution_contract=("MAKER_REST" if legacy_maker else "FAK_PARTIAL"),
            probability_revision="fixture-revision-v1",
        )
        assert len(rows) == 1
        # The borrowed forecast handle remains usable and was never attached or
        # closed by the corpus reader.
        assert forecast.execute("SELECT 1").fetchone()[0] == 1
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("mutation,reason", [
    ("UPDATE forecast_posteriors SET computed_at=?", "LEGACY_FORECAST_POSTERIOR_UNBOUND"),
])
def test_canonical_fit_legacy_replacement_requires_forecast_cutoff(mutation, reason):
    world, trade, _, forecast = _canonical_corpus_fixture(legacy=True, return_forecast=True)
    try:
        forecast.execute(mutation, ((NOW + timedelta(days=1)).isoformat(),))
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.records == () and corpus.unknown == {reason: 1}
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_legacy_revision_unknown_cannot_fit_or_mix():
    world, trade, _, forecast = _canonical_corpus_fixture(
        legacy=True, probability_revision=None, return_forecast=True,
    )
    try:
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.records[0]["raw_probability_revision"] is None
        assert corpus.fit_rows(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="FAK_PARTIAL",
            probability_revision="fixture-revision-v1",
        ) == []
        with pytest.raises(ValueError, match="probability_revision is required"):
            corpus.fit_rows(metric="high", execution_mode="TAKER_LIMIT", execution_contract="FAK_PARTIAL", probability_revision="")
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_keeps_independent_raw_when_child_revision_is_missing():
    world, trade, _, forecast = _canonical_corpus_fixture(
        legacy=True, probability_revision=None, return_forecast=True,
    )
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET provenance_json=?",
            (json.dumps({"probability_semantics_revision": "fixture-source-v1"}),),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.records[0]["q_raw"] == pytest.approx(.70)
        assert corpus.records[0]["raw_probability_revision"] is None
        assert corpus.fit_rows(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="FAK_PARTIAL",
            probability_revision="fixture-source-v1",
        ) == []
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("reverse_command_order", [False, True])
@pytest.mark.parametrize("late_field", ["source_and_computed", "recorded_only"])
def test_canonical_fit_forecast_cache_is_point_in_time_safe(reverse_command_order, late_field):
    """A shared posterior cannot be accepted/rejected based on traversal order."""
    from src.decision_kernel.certificate import build_certificate
    from src.decision_kernel.ledger import DecisionCertificateLedger

    world, trade, _, forecast, first_certificate, parents = _canonical_corpus_fixture(
        legacy=True, return_details=True,
    )
    decision = NOW - timedelta(days=3)
    earlier = decision - timedelta(hours=2)
    source_time = decision - timedelta(hours=(3 if late_field == "recorded_only" else 1))
    recorded_time = decision - timedelta(hours=(1 if late_field == "recorded_only" else 3))
    try:
        forecast.execute(
            "UPDATE forecast_posteriors SET source_available_at=?, computed_at=?, recorded_at=?",
            (source_time.isoformat(), source_time.isoformat(), recorded_time.isoformat()),
        )
        second_payload = dict(first_certificate.payload)
        second_certificate = build_certificate(
            certificate_type="ActionableTradeCertificate", semantic_key="fixture-entry-earlier",
            claim_type="fixture", mode="LIVE", decision_time=earlier,
            source_available_at=earlier, agent_received_at=earlier, persisted_at=earlier,
            payload=second_payload, parent_edges=first_certificate.header.parent_edges,
            parent_certificates=parents, authority_id="fixture", authority_version="1",
            algorithm_id="fixture", algorithm_version="1",
        )
        DecisionCertificateLedger(world).insert_idempotent(second_certificate, preverified=True)
        original_id = "command"
        later_id = "a-later" if reverse_command_order else original_id
        earlier_id = "z-earlier" if reverse_command_order else "a-earlier"
        if reverse_command_order:
            trade.execute("UPDATE venue_commands SET command_id=? WHERE command_id=?", (later_id, original_id))
            trade.execute("UPDATE position_decision_attribution SET command_id=? WHERE command_id=?", (later_id, original_id))
            trade.execute("UPDATE venue_trade_facts SET command_id=? WHERE command_id=?", (later_id, original_id))
        trade.execute(
            "INSERT INTO venue_commands(command_id,token_id,created_at,venue_order_id,snapshot_id,intent_kind,side,envelope_id) VALUES (?,?,?,?,?,?,?,?)",
            (earlier_id, "11", earlier.isoformat(), "order-earlier", "snapshot", "ENTRY", "BUY", "envelope-earlier"),
        )
        trade.execute("INSERT INTO venue_submission_envelopes VALUES ('envelope-earlier','FAK',0)")
        trade.execute(
            "INSERT INTO position_decision_attribution VALUES (?,?,?,?)",
            (earlier_id, second_certificate.certificate_hash, "ENTRY", earlier.isoformat()),
        )
        filled = earlier + timedelta(seconds=2)
        for fact_id, state, sequence in [(10, "MATCHED", 1), (11, "MINED", 2), (12, "CONFIRMED", 3)]:
            trade.execute("INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                fact_id, earlier_id, "fill-earlier", "order-earlier", state, 10.0,
                "tx-earlier", filled.isoformat(), filled.strftime("%Y-%m-%d %H:%M:%S"),
                filled.isoformat(), sequence, "{}",
            ))
        trade.commit()
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert len(corpus.records) == 1
        assert corpus.records[0]["command_id"] == later_id
        assert corpus.unknown.get("LEGACY_FORECAST_POSTERIOR_UNBOUND") == 1
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("mutation,reason", [
    ("UPDATE decision_certificates SET payload_json='{}' WHERE certificate_type='ForecastAuthorityCertificate'",
     "LEGACY_FORECAST_AUTHORITY_UNBOUND"),
    ("UPDATE forecast_posteriors SET posterior_identity_hash='wrong'",
     "LEGACY_FORECAST_POSTERIOR_UNBOUND"),
])
def test_canonical_fit_legacy_rejects_tampered_forecast_bindings(mutation, reason):
    world, trade, _, forecast = _canonical_corpus_fixture(legacy=True, return_forecast=True)
    try:
        (world if "decision_certificates" in mutation else forecast).execute(mutation)
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.records == () and corpus.unknown == {reason: 1}
    finally:
        world.close()
        trade.close()
        forecast.close()


@pytest.mark.parametrize("role,field,value", [
    ("quote_feasibility", "native_quote_available", None),
    ("cost_model", "cost_source", "synthetic_quote"),
    ("executable_snapshot", "captured_at", (NOW + timedelta(days=1)).isoformat()),
])
def test_canonical_fit_legacy_anchor_projection_gaps_remain_unknown(role, field, value):
    world, trade, _, forecast = _canonical_corpus_fixture(legacy=True, return_forecast=True)
    try:
        row = world.execute(
            """SELECT certificate_hash, payload_json FROM decision_certificates
               WHERE certificate_type=?""",
            ({"quote_feasibility": "QuoteFeasibilityCertificate",
             "cost_model": "CostModelCertificate",
             "executable_snapshot": "ExecutableSnapshotCertificate"}[role],),
        ).fetchone()
        payload = json.loads(row[1])
        if value is None:
            payload.pop(field, None)
        else:
            payload[field] = value
        # Deliberately leave the sealed hash untouched: this is a tamper
        # antibody, while a separately resigned future payload is checked by
        # the captured_at <= child-decision gate in the reader.
        world.execute(
            "UPDATE decision_certificates SET payload_json=? WHERE certificate_hash=?",
            (json.dumps(payload), row[0]),
        )
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert corpus.records == ()
        assert corpus.command_count == 1
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_future_revisions_cannot_erase_asof_truth():
    world, trade, _ = _canonical_corpus_fixture()
    try:
        future = (NOW+timedelta(days=1)).isoformat()
        trade.execute("UPDATE payout_observations SET superseded_by=99")
        trade.execute("INSERT INTO payout_observations VALUES (99,'condition',0,NULL,NULL,'UNKNOWN','chain_rpc_finalized_v1',101,'hash',?,NULL)", (future,))
        trade.execute("INSERT INTO venue_trade_facts SELECT 99,command_id,trade_id,venue_order_id,'CONFIRMED',999,tx_hash,?,?,venue_timestamp,99,'{}' FROM venue_trade_facts WHERE trade_fact_id=3", (future,future))
        before = _read_canonical(world, trade)
        assert before.records[0]["confirmed_shares"] == 10
        assert before.records[0]["payout"] == 1
        after = _read_canonical(world, trade, NOW+timedelta(days=2))
        assert after.records == ()
        assert after.unknown == {"FINALIZED_PAYOUT_UNBOUND":1}
    finally:
        world.close()
        trade.close()


def test_canonical_fit_future_alias_source_cannot_remove_existing_fill():
    world, trade, _ = _canonical_corpus_fixture()
    try:
        import json
        trade.execute("UPDATE venue_trade_facts SET raw_payload_json=?", (json.dumps({"raw_fill_payload":{"source_trade_fact_id":99}}),))
        future = (NOW+timedelta(days=1)).isoformat()
        trade.execute("INSERT INTO venue_trade_facts SELECT 99,command_id,'source-fill',venue_order_id,'CONFIRMED',10,tx_hash,?,?,venue_timestamp,1,'{}' FROM venue_trade_facts WHERE trade_fact_id=3", (future,future))
        assert _read_canonical(world, trade).records[0]["confirmed_shares"] == 10
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize("mutation,reason", [
    ("UPDATE venue_commands SET side='SELL'", "ENTRY_NOT_BUY"),
    ("UPDATE payout_observations SET payout_denominator=100,payout_numerator=40+outcome_index*20,state='RESOLVED_NONZERO'", "FRACTIONAL_PAYOUT_UNSUPPORTED"),
    ("UPDATE venue_trade_facts SET state='MINED'", "CONFIRMED_FILL_MISSING"),
    ("UPDATE venue_trade_facts SET venue_order_id='other-order'", "CONFIRMED_FILL_CLOCK_OR_IDENTITY_UNBOUND"),
    ("UPDATE executable_market_snapshots SET selected_outcome_token_id='12'", "CERTIFICATE_IDENTITY_UNBOUND"),
    ("UPDATE decision_certificates SET payload_json='{}' WHERE certificate_type='ActionableTradeCertificate'", "CERTIFICATE_IDENTITY_UNBOUND"),
    ("UPDATE decision_certificates SET algorithm_version='tampered' WHERE certificate_type='ActionableTradeCertificate'", "CERTIFICATE_HEADER_HASH_UNBOUND"),
])
def test_canonical_fit_rejects_unbound_evidence_and_keeps_denominator(mutation, reason):
    world, trade, _ = _canonical_corpus_fixture()
    try:
        (world if "decision_certificates" in mutation else trade).execute(mutation)
        corpus = _read_canonical(world, trade)
        assert corpus.records == () and corpus.command_count == 1
        assert corpus.unknown == {reason:1}
    finally:
        world.close()
        trade.close()


def test_canonical_event_weight_preserves_shares_without_inventing_sample_size():
    records = tuple(dict(metric="high",execution_mode="MAKER_REST",execution_contract="MAKER_REST",event_key=("Austin","2026-08-01","high"),
        lead_bucket="day1",side="YES",p0=.4,q_raw=.6,payout=1,confirmed_shares=shares,
        raw_probability_revision="fixture-revision-v1") for shares in (10,30))
    corpus = live_fit.CanonicalFitCorpus(records, {}, 2, NOW.isoformat())
    rows = corpus.fit_rows(metric="high",execution_mode="MAKER_REST", execution_contract="MAKER_REST", probability_revision="fixture-revision-v1")
    assert [row.w for row in rows] == [.25,.75]
    assert sum(row.w for row in rows) == 1


def _accounting_cash_fixture(monkeypatch, *, available_at=None):
    # Isolate cohort selection from the independently tested receipt decoder.
    import src.state.fill_cash_reader as cash_reader
    available_at = available_at or (NOW - timedelta(days=2)).isoformat()

    def cash(_conn, *, command, fills, cutoff, schema):
        assert schema == 'main'
        if not fills or datetime.fromisoformat(available_at) >= cutoff:
            return {'status': 'UNKNOWN', 'reason': 'CHAIN_CASH_PROOF_UNAVAILABLE'}
        return {'status': 'PROVEN', 'reason': 'FINALIZED_FILL_CASH_PROVEN',
                'shares_atoms': 10_000_000, 'principal_atoms': 3_500_000,
                'fee_atoms': 100_000, 'collateral_delta_atoms': -3_600_000,
                'available_at': available_at, 'proof_hashes': ['receipt-proof']}
    monkeypatch.setattr(cash_reader, 'read_command_fill_cash', cash)


@pytest.mark.parametrize('side,net', [('YES', 6_400_000), ('NO', -3_600_000)])
def test_command_accounting_retains_wins_and_losses_without_selected_certificate(monkeypatch, side, net):
    _accounting_cash_fixture(monkeypatch)
    world, trade, _ = _canonical_corpus_fixture(side=side, include_calibration_policy=False)
    try:
        trade.execute('DELETE FROM position_decision_attribution')
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()
        assert corpus.command_count == len(corpus.command_accounting) == 1
        row, = corpus.command_accounting
        assert row['physical_endpoint_status'] == 'PROVEN'
        assert row['terminal_net_payoff_numerator_atoms'] == net
        assert row['terminal_net_payoff_denominator'] == 1
        assert row['net_markout_per_share_numerator'] / row['net_markout_per_share_denominator'] == pytest.approx(net / 10_000_000)
        assert row['confirmed_fill_count'] == 1  # MATCHED/MINED/CONFIRMED are one fill.
        assert row['calibration_evidence_reason'] == 'CERTIFICATE_LINK_MISSING_OR_AMBIGUOUS'
        assert row['calibration_policy'] is None
    finally:
        world.close()
        trade.close()


def test_command_accounting_preserves_fractional_settlement_and_legacy_policy_unknown(monkeypatch):
    _accounting_cash_fixture(monkeypatch)
    world, trade, _ = _canonical_corpus_fixture(include_calibration_policy=False)
    try:
        before = _read_canonical(world, trade).command_accounting[0]
        assert before['physical_endpoint_status'] == 'PROVEN'
        assert before['calibration_policy_reason'] == 'CALIBRATION_POLICY_MISSING'
        trade.execute("UPDATE payout_observations SET payout_denominator=100,payout_numerator=40+outcome_index*20,state='RESOLVED_NONZERO'")
        corpus = _read_canonical(world, trade)
        assert corpus.records == ()  # Fit only supports binary labels.
        row, = corpus.command_accounting
        assert row['physical_endpoint_status'] == 'PROVEN'
        assert row['terminal_net_payoff_numerator_atoms'] == 40_000_000
        assert row['terminal_net_payoff_denominator'] == 100
        assert row['net_markout_per_share_numerator'] / row['net_markout_per_share_denominator'] == pytest.approx(.04)
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize('mutation,reason', [
    ("UPDATE venue_trade_facts SET state='MINED'", 'NO_CONFIRMED_FILL_AT_CUTOFF'),
    ("DELETE FROM venue_trade_facts", 'NO_CONFIRMED_FILL_AT_CUTOFF'),
    ("UPDATE venue_commands SET side='SELL'", 'ENTRY_SIDE_UNSUPPORTED_FOR_BUY_MARKOUT'),
    ("UPDATE venue_trade_facts SET venue_timestamp='2099-01-01T00:00:00Z'", 'CONFIRMED_FILL_CLOCK_OR_IDENTITY_UNBOUND'),
    ("UPDATE payout_observations SET block_hash='different' WHERE outcome_index=1", 'PAYOUT_PENDING_OR_UNKNOWN'),
    ("UPDATE payout_observations SET source='local_pnl'", 'PAYOUT_PENDING_OR_UNKNOWN'),
    ("UPDATE executable_market_snapshots SET token_map_json='{}'", 'PAYOUT_IDENTITY_UNKNOWN'),
])
def test_command_accounting_unknown_is_never_zero_or_deleted(monkeypatch, mutation, reason):
    _accounting_cash_fixture(monkeypatch)
    world, trade, _ = _canonical_corpus_fixture()
    try:
        trade.execute(mutation)
        corpus = _read_canonical(world, trade)
        assert len(corpus.command_accounting) == corpus.command_count == 1
        row, = corpus.command_accounting
        assert row['physical_endpoint_status'] == 'UNKNOWN'
        assert row['physical_endpoint_reason'] == reason
        assert row['terminal_net_payoff_numerator_atoms'] is None
        assert row['net_markout_per_share_numerator'] is None
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize('late_surface', ['cash', 'payout'])
def test_command_accounting_evidence_arrival_does_not_rewrite_earlier_cutoff(monkeypatch, late_surface):
    future = (NOW + timedelta(hours=1)).isoformat()
    _accounting_cash_fixture(monkeypatch, available_at=future if late_surface == 'cash' else None)
    world, trade, _ = _canonical_corpus_fixture()
    try:
        if late_surface == 'payout':
            trade.execute('UPDATE payout_observations SET observed_at=?', (future,))
        early = _read_canonical(world, trade)
        late = _read_canonical(world, trade, NOW + timedelta(hours=2))
        assert early.command_count == late.command_count == 1
        assert early.command_accounting[0]['physical_endpoint_status'] == 'UNKNOWN'
        assert late.command_accounting[0]['physical_endpoint_status'] == 'PROVEN'
        assert early.command_accounting[0]['terminal_net_payoff_numerator_atoms'] is None
        assert _read_canonical(world, trade).command_accounting == early.command_accounting
    finally:
        world.close()
        trade.close()


def test_command_accounting_cash_presence_prefilter_never_grants_proof(monkeypatch):
    import src.state.fill_cash_reader as cash_reader
    world, trade, _ = _canonical_corpus_fixture()
    calls = []

    def reject_cash(_conn, **kwargs):
        calls.append(kwargs['command']['command_id'])
        return {'status': 'UNKNOWN', 'reason': 'SIGNED_ENVELOPE_IDENTITY_MISMATCH'}
    monkeypatch.setattr(cash_reader, 'read_command_fill_cash', reject_cash)
    try:
        trade.execute('DELETE FROM position_decision_attribution')
        trade.execute('CREATE TABLE venue_fill_cash_facts(chain_id INTEGER, tx_hash TEXT, status TEXT, observed_at TEXT)')
        trade.execute('INSERT INTO venue_fill_cash_facts VALUES(137,?,?,?)', ('tx', 'UNKNOWN', (NOW-timedelta(days=1)).isoformat()))
        before = _read_canonical(world, trade).command_accounting[0]
        assert calls == []
        assert before['chain_cash']['reason'] == 'CHAIN_CASH_PROOF_UNAVAILABLE'
        trade.execute("UPDATE venue_fill_cash_facts SET status='PROVEN',chain_id=1")
        _read_canonical(world, trade)
        assert calls == []
        trade.execute('UPDATE venue_fill_cash_facts SET chain_id=137,observed_at=?', ((NOW+timedelta(days=1)).isoformat(),))
        _read_canonical(world, trade)
        assert calls == []
        trade.execute('UPDATE venue_fill_cash_facts SET observed_at=?', ((NOW-timedelta(days=1)).isoformat(),))
        after = _read_canonical(world, trade).command_accounting[0]
        assert calls == ['command']
        assert after['physical_endpoint_status'] == 'UNKNOWN'
        assert after['chain_cash']['reason'] == 'SIGNED_ENVELOPE_IDENTITY_MISMATCH'
        assert after['terminal_net_payoff_numerator_atoms'] is None
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_probability_only_corpus_skips_cash_decode_without_changing_fit_rows(monkeypatch, side):
    import src.state.fill_cash_reader as cash_reader

    world, trade, _, forecast = _canonical_corpus_fixture(
        side=side, return_forecast=True, forecast_lineage=True,
    )
    try:
        full = _read_canonical(world, trade, forecast=forecast)
        monkeypatch.setattr(
            cash_reader, "read_command_fill_cash",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cash decode used")),
        )
        probability_only = _read_canonical(
            world, trade, forecast=forecast, include_cash_proofs=False,
        )
        kwargs = dict(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="FOK_FULL_OR_ZERO",
            probability_revision="fixture-revision-v1",
        )
        assert probability_only.command_count == full.command_count
        assert probability_only.unknown == full.unknown
        assert len(probability_only.records) == len(full.records)
        assert probability_only.fit_rows(**kwargs) == full.fit_rows(**kwargs)
        accounting, = probability_only.command_accounting
        assert accounting["chain_cash"] == {
            "status": "UNKNOWN", "reason": "CHAIN_CASH_NOT_REQUESTED",
        }
        assert accounting["physical_endpoint_status"] == "UNKNOWN"
        assert accounting["terminal_net_payoff_numerator_atoms"] is None
        assert accounting["net_markout_per_share_numerator"] is None
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_skips_unused_parent_payload_but_keeps_child_header_edges():
    world, trade, _, forecast, certificate, parents = _canonical_corpus_fixture(
        return_details=True, forecast_lineage=True, unused_large_parent=True,
    )
    unused = parents[-1]
    trace: list[str] = []
    try:
        world.set_trace_callback(trace.append)
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert len(corpus.records) == 1
        parent_payload_queries = [
            sql for sql in trace
            if "FROM main.decision_certificates" in sql
            and "WHERE certificate_hash IN" in sql
        ]
        assert parent_payload_queries
        assert all(unused.certificate_hash not in sql for sql in parent_payload_queries)

        child_id = world.execute(
            "SELECT certificate_id FROM decision_certificates WHERE certificate_hash=?",
            (certificate.certificate_hash,),
        ).fetchone()[0]
        world.execute(
            """UPDATE decision_certificate_edges
               SET parent_certificate_type='TamperedUnusedCertificate'
               WHERE child_certificate_id=? AND parent_role='unused_large_parent'""",
            (child_id,),
        )
        world.commit()
        rejected = _read_canonical(world, trade, forecast=forecast)
        assert rejected.records == ()
        assert rejected.unknown == {"CERTIFICATE_HEADER_HASH_UNBOUND": 1}
    finally:
        world.set_trace_callback(None)
        world.close()
        trade.close()
        forecast.close()


def test_corrected_child_does_not_load_its_legacy_anchor_parent_payloads():
    world, trade, _, forecast, _certificate, parents = _canonical_corpus_fixture(
        return_details=True, forecast_lineage=True, extra_legacy_anchor_edges=True,
        unused_large_parent=True,
    )
    trace: list[str] = []
    try:
        world.set_trace_callback(trace.append)
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert len(corpus.records) == 1
        parent_payload_queries = [
            sql for sql in trace
            if "FROM main.decision_certificates" in sql
            and "WHERE certificate_hash IN" in sql
        ]
        assert any(parents[1].certificate_hash in sql for sql in parent_payload_queries)
        assert all(
            parent.certificate_hash not in sql
            for parent in (*parents[2:6], parents[-1])
            for sql in parent_payload_queries
        )
    finally:
        world.set_trace_callback(None)
        world.close()
        trade.close()
        forecast.close()


def test_mixed_legacy_and_corrected_children_share_forecast_but_skip_unused_parent():
    from src.decision_kernel.certificate import ParentEdge, build_certificate
    from src.decision_kernel.ledger import DecisionCertificateLedger

    world, trade, _, forecast, legacy_certificate, parents = _canonical_corpus_fixture(
        legacy=True, return_details=True, unused_large_parent=True,
    )
    decision = NOW - timedelta(days=3)
    trace: list[str] = []
    try:
        corrected_payload = dict(legacy_certificate.payload)
        corrected_payload.pop("q_source", None)
        corrected_payload.pop("_edli_q_source", None)
        corrected_payload["q_live"] = .52
        economics = dict(corrected_payload["qkernel_execution_economics"])
        economics["payoff_q_point"] = .52
        economics["market_anchored_correction"] = {
            "applied": True, "q_raw": .70, "q_corrected": .52, "p0": .35,
            "alpha_lead": math.log(.52 / .48) - math.log(.35 / .65),
            "beta": 0.0, "lambda": 10.0, "lead_bucket": "day1",
            "training_cutoff": decision.isoformat(), "n_train": 20,
            "param_hash": "mixed-corrected", "calibration_policy": _known_policy().as_payload(),
        }
        corrected_payload["qkernel_execution_economics"] = economics
        corrected = build_certificate(
            certificate_type="ActionableTradeCertificate", semantic_key="fixture-mixed-corrected",
            claim_type="fixture", mode="LIVE", decision_time=decision,
            source_available_at=decision, agent_received_at=decision, persisted_at=decision,
            payload=corrected_payload,
            parent_edges=(
                ParentEdge("forecast_authority", parents[1].certificate_hash, parents[1].certificate_type),
                ParentEdge("unused_large_parent", parents[-1].certificate_hash, parents[-1].certificate_type),
            ),
            parent_certificates=parents, authority_id="fixture", authority_version="1",
            algorithm_id="fixture", algorithm_version="1",
        )
        DecisionCertificateLedger(world).insert_idempotent(corrected, preverified=True)
        trade.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
            ("corrected-command", "11", decision.isoformat(), "corrected-order", "snapshot", "ENTRY", "BUY", "corrected-envelope"),
        )
        trade.execute("INSERT INTO venue_submission_envelopes VALUES ('corrected-envelope','FOK',0)")
        trade.execute(
            "INSERT INTO position_decision_attribution VALUES (?,?,?,?)",
            ("corrected-command", corrected.certificate_hash, "ENTRY", decision.isoformat()),
        )
        filled = decision + timedelta(seconds=2)
        for fact_id, state, sequence in ((10, "MATCHED", 1), (11, "MINED", 2), (12, "CONFIRMED", 3)):
            trade.execute(
                "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fact_id, "corrected-command", "corrected-fill", "corrected-order", state, 10,
                 "corrected-tx", filled.isoformat(), filled.strftime("%Y-%m-%d %H:%M:%S"),
                 filled.isoformat(), sequence, "{}"),
            )
        world.commit()
        trade.commit()
        world.set_trace_callback(trace.append)
        corpus = _read_canonical(world, trade, forecast=forecast)
        assert len(corpus.records) == 2
        parent_payload_queries = [
            sql for sql in trace
            if "FROM main.decision_certificates" in sql
            and "WHERE certificate_hash IN" in sql
        ]
        assert any(parents[1].certificate_hash in sql for sql in parent_payload_queries)
        assert all(
            parent.certificate_hash in "\n".join(parent_payload_queries)
            for parent in parents[2:6]
        )
        assert all(parents[-1].certificate_hash not in sql for sql in parent_payload_queries)
    finally:
        world.set_trace_callback(None)
        world.close()
        trade.close()
        forecast.close()


def test_canonical_fit_ranks_payouts_only_for_entry_conditions():
    world, trade, _ = _canonical_corpus_fixture()
    trace: list[str] = []
    try:
        for index in range(20):
            trade.execute(
                "INSERT INTO payout_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    100 + index, f"other-condition-{index}", index % 2, 1, 1,
                    "RESOLVED_NONZERO", "chain_rpc_finalized_v1", 100,
                    "0x" + "bb" * 32, (NOW - timedelta(days=1)).isoformat(), None,
                ),
            )
        trade.commit()
        trade.set_trace_callback(trace.append)
        corpus = _read_canonical(trade=trade, world=world)
        assert len(corpus.records) == 1
        payout_queries = [sql for sql in trace if "payout_observations" in sql]
        assert payout_queries
        assert all("other-condition-" not in sql for sql in payout_queries)
        assert any("condition_id IN ('condition')" in sql for sql in payout_queries)
    finally:
        trade.set_trace_callback(None)
        world.close()
        trade.close()


def _canonical_provider(world, trade, forecast, *, cache=None):
    return CanonicalMarketAnchoredFitProvider(
        lambda: (world, trade, forecast),
        city_timezones=_TEST_CITY_TIMEZONES,
        min_train_rows=1,
        cache=cache or MarketAnchoredArtifactCache(),
    )


def _canonical_scope(*, metric="high", execution_contract="FOK_FULL_OR_ZERO",
                     revision="fixture-revision-v1"):
    return CalibrationFitScope(
        metric=metric,
        execution_mode=("MAKER_REST" if execution_contract == "MAKER_REST" else "TAKER_LIMIT"),
        execution_contract=execution_contract,
        raw_probability_revision=revision,
    )


def test_canonical_provider_fits_actual_corpus_without_selected_attribution(monkeypatch):
    import src.state.fill_cash_reader as cash_reader

    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        # If serving accidentally regresses to selected settlement_attribution,
        # it would call this loader instead of the canonical corpus reader.
        monkeypatch.setattr(live_fit, "load_fit_rows", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("selected corpus used")))
        monkeypatch.setattr(cash_reader, "read_command_fill_cash", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cash decode used")))
        provider = _canonical_provider(world, trade, forecast)
        artifact = provider.artifact(scope=_canonical_scope(), now=NOW)
        assert artifact is not None
        assert provider.calibration_policy.input_revision == live_fit.CANONICAL_CALIBRATION_INPUT_REVISION
        assert provider.calibration_policy.metric_pooling == live_fit.CANONICAL_CALIBRATION_METRIC_POOLING
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_manifest_commits_exact_rows_without_changing_fit():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        scope = _canonical_scope()
        artifact = provider.artifact(scope=scope, now=NOW)
        assert artifact is not None and artifact.training_manifest is not None
        manifest = artifact.training_manifest
        payload = manifest.as_payload()
        assert len(json.dumps(payload, sort_keys=True)) < 2_000
        assert set(payload) == {
            "type", "version", "scope_hash", "corpus_revision", "training_cutoff",
            "row_count", "event_count", "weight_sum", "max_fill_available_at",
            "max_label_available_at", "availability_upper_bound", "input_hash",
            "manifest_hash",
        }
        assert payload["scope_hash"] == scope.as_payload()["scope_hash"]
        assert payload["corpus_revision"] == live_fit.CANONICAL_CORPUS_REVISION
        assert payload["row_count"] == artifact.n_train == 1
        assert payload["event_count"] == 1
        assert payload["weight_sum"] == pytest.approx(1.0)
        assert payload["max_fill_available_at"] < payload["training_cutoff"]
        assert payload["max_label_available_at"] < payload["training_cutoff"]
        assert CanonicalTrainingManifest.from_payload(payload) == manifest

        baseline = live_fit.fit(
            _read_canonical(world, trade, forecast=forecast).fit_rows(
                metric="high", execution_mode="TAKER_LIMIT",
                execution_contract="FOK_FULL_OR_ZERO",
                probability_revision="fixture-revision-v1",
            ),
            lambda_=provider.calibration_policy.lambda_,
            training_cutoff=artifact.training_cutoff,
            lead_calendar_revision=artifact.lead_calendar_revision,
            city_timezone_snapshot=artifact.city_timezone_snapshot,
        )
        assert (artifact.alpha, artifact.beta, artifact.param_hash) == (
            baseline.alpha, baseline.beta, baseline.param_hash,
        )

        correction = PayoffQCorrection(
            family_key="family", bin_id="bin", side="YES", token_id="11",
            raw_q=.70, corrected_q=.52, p0=.35, lead_bucket="day1",
            alpha_lead=artifact.alpha["day1"], beta=artifact.beta,
            lambda_=artifact.lambda_, training_cutoff=artifact.training_cutoff,
            n_train=artifact.n_train, param_hash=artifact.param_hash,
            fit_scope=scope, training_manifest=manifest,
        )
        assert correction.as_cert_fields()["training_manifest"] == payload
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_training_manifest_hash_scope_and_cutoff_validation():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        manifest = provider.artifact(scope=_canonical_scope(), now=NOW).training_manifest
        payload = manifest.as_payload()
        payload["input_hash"] = "tampered"
        with pytest.raises(ValueError, match="hash"):
            CanonicalTrainingManifest.from_payload(payload)

        base = manifest.as_payload()
        with pytest.raises(ValueError, match="availability"):
            CanonicalTrainingManifest.build(
                scope_hash=base["scope_hash"],
                corpus_revision=base["corpus_revision"],
                training_cutoff=base["training_cutoff"],
                row_count=base["row_count"], event_count=base["event_count"],
                weight_sum=base["weight_sum"],
                max_fill_available_at=base["training_cutoff"],
                max_label_available_at=base["max_label_available_at"],
                input_hash=base["input_hash"],
            )
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_training_manifest_binds_consumed_order_and_event_weights():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        base = dict(_read_canonical(world, trade, forecast=forecast).records[0])
        first = dict(base)
        second = dict(base)
        first.update(command_id="command-a", certificate_hash="cert-a", confirmed_shares=2.0)
        second.update(command_id="command-b", certificate_hash="cert-b", confirmed_shares=3.0)
        corpus = live_fit.CanonicalFitCorpus(
            (second, first), {}, 2, NOW.isoformat(),
        )
        scope = _canonical_scope()
        manifest = corpus.training_manifest(scope=scope)
        fit_rows = corpus.fit_rows(
            metric="high", execution_mode="TAKER_LIMIT",
            execution_contract="FOK_FULL_OR_ZERO",
            probability_revision="fixture-revision-v1",
        )
        fitted = live_fit.fit(
            fit_rows, lambda_=10.0, training_cutoff=NOW.isoformat(),
            lead_calendar_revision=LEAD_CALENDAR_REVISION,
            city_timezone_snapshot=tuple(sorted(_TEST_CITY_TIMEZONES.items())),
            training_manifest=manifest,
        )
        assert fitted.training_manifest == manifest
        assert manifest.row_count == 2
        assert manifest.event_count == 1
        assert manifest.weight_sum == pytest.approx(1.0)
        assert manifest == corpus.training_manifest(scope=scope)
        assert manifest.input_hash != live_fit.CanonicalFitCorpus(
            (first, second), {}, 2, NOW.isoformat(),
        ).training_manifest(scope=scope).input_hash
        assert len(json.dumps(manifest.as_payload(), sort_keys=True)) < 2_000
        malformed = dict(first, certificate_hash="")
        with pytest.raises(ValueError, match="aligned"):
            live_fit.CanonicalFitCorpus(
                (malformed,), {}, 1, NOW.isoformat(),
            ).training_manifest(scope=scope)
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_manifest_scope_binding_and_legacy_none_compatibility():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        scope = _canonical_scope()
        artifact = provider.artifact(scope=scope, now=NOW)
        manifest = artifact.training_manifest
        with pytest.raises(ValueError, match="scope"):
            PayoffQCorrection(
                family_key="family", bin_id="bin", side="YES", token_id="11",
                raw_q=.70, corrected_q=.52, p0=.35, lead_bucket="day1",
                alpha_lead=artifact.alpha["day1"], beta=artifact.beta,
                lambda_=artifact.lambda_, training_cutoff=artifact.training_cutoff,
                n_train=artifact.n_train, param_hash=artifact.param_hash,
                fit_scope=_canonical_scope(metric="low"),
                training_manifest=manifest,
            )
        legacy = PayoffQCorrection(
            family_key="family", bin_id="bin", side="YES", token_id="11",
            raw_q=.70, corrected_q=.52, p0=.35, lead_bucket="day1",
            alpha_lead=0.0, beta=0.0, lambda_=1.0,
            training_cutoff=NOW.isoformat(), n_train=0, param_hash="legacy",
        )
        assert "training_manifest" not in legacy.as_cert_fields()
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_keeps_no_geometry_and_backward_cutoff_causal():
    world, trade, _, forecast = _canonical_corpus_fixture(
        side="NO", return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        scope = _canonical_scope()
        assert provider.artifact(scope=scope, now=NOW) is not None
        # The only command is later than this cutoff; it cannot reuse the
        # future artifact through the shared cache.
        assert provider.artifact(scope=scope, now=NOW - timedelta(days=4)) is None
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_scope_and_cache_keys_do_not_mix():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        high = _canonical_scope()
        high_fak = _canonical_scope(execution_contract="FAK_PARTIAL")
        low = _canonical_scope(metric="low")
        revision = _canonical_scope(revision="another-revision")
        assert provider.artifact(scope=high, now=NOW) is not None
        assert provider.artifact(scope=high_fak, now=NOW) is None
        assert provider.artifact(scope=low, now=NOW) is None
        assert provider.artifact(scope=revision, now=NOW) is None
        identities = tuple(live_fit._borrowed_db_identity(conn, schema_alias="main") for conn in (world, trade, forecast))
        keys = {provider._cache_key(identities, scope) for scope in (high, high_fak, low, revision)}
        assert len(keys) == 4
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_deadline_and_closed_handles_never_serve_stale_fit():
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    provider = _canonical_provider(world, trade, forecast)
    scope = _canonical_scope()
    try:
        assert provider.artifact(scope=scope, now=NOW) is not None
        assert provider.artifact(
            scope=scope, now=NOW,
            deadline_monotonic=time.monotonic() - 0.001,
        ) is None
    finally:
        world.close()
        trade.close()
        forecast.close()
    assert provider.artifact(scope=scope, now=NOW) is None


def test_canonical_provider_warm_corpus_does_not_fit(monkeypatch):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        monkeypatch.setattr(
            live_fit, "fit",
            lambda *args, **kwargs: pytest.fail("warm_corpus must not fit"),
        )
        assert provider.warm_corpus(now=NOW)
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_warm_corpus_is_reused_by_new_provider_with_pit_cutoff(
    tmp_path, monkeypatch,
):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    handles = []
    try:
        forecast.commit()
        for source, name in zip(
            (world, trade, forecast), ("world", "trade", "forecast"), strict=True,
        ):
            destination = sqlite3.connect(tmp_path / (name + ".db"))
            destination.row_factory = sqlite3.Row
            source.backup(destination)
            handles.append(destination)
        corpus_cache = live_fit.CanonicalCorpusCache()
        artifact_cache = MarketAnchoredArtifactCache()
        original_load = live_fit.load_canonical_fit_corpus
        load_calls = []

        def counted_load(*args, **kwargs):
            load_calls.append(kwargs["training_cutoff"])
            return original_load(*args, **kwargs)

        monkeypatch.setattr(live_fit, "load_canonical_fit_corpus", counted_load)
        provider = CanonicalMarketAnchoredFitProvider(
            lambda: tuple(handles), city_timezones=_TEST_CITY_TIMEZONES,
            min_train_rows=1, cache=artifact_cache, corpus_cache=corpus_cache,
        )
        assert provider.warm_corpus(now=NOW)
        later_provider = CanonicalMarketAnchoredFitProvider(
            lambda: tuple(handles), city_timezones=_TEST_CITY_TIMEZONES,
            min_train_rows=1, cache=artifact_cache, corpus_cache=corpus_cache,
        )
        artifact = later_provider.artifact(
            scope=_canonical_scope(), now=NOW + timedelta(hours=1),
        )
        assert artifact is not None
        assert load_calls == [NOW]
        assert artifact.training_cutoff == NOW.isoformat()
    finally:
        for conn in (*handles, world, trade, forecast):
            conn.close()


def test_canonical_provider_warm_failure_deadline_and_closed_handles_are_retryable(
    monkeypatch,
):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    active = [(world, trade, forecast)]
    try:
        corpus_cache = live_fit.CanonicalCorpusCache()
        provider = CanonicalMarketAnchoredFitProvider(
            lambda: active[0], city_timezones=_TEST_CITY_TIMEZONES,
            min_train_rows=1, corpus_cache=corpus_cache,
        )
        original_load = live_fit.load_canonical_fit_corpus
        attempts = []

        def fail_once(*args, **kwargs):
            attempts.append(kwargs["training_cutoff"])
            if len(attempts) == 1:
                return None
            return original_load(*args, **kwargs)

        monkeypatch.setattr(live_fit, "load_canonical_fit_corpus", fail_once)
        assert not provider.warm_corpus(now=NOW)
        assert not corpus_cache._entries
        assert not provider.warm_corpus(
            now=NOW, deadline_monotonic=time.monotonic() - 1,
        )
        assert not corpus_cache._entries
        for conn in active[0]:
            conn.close()
        assert not provider.warm_corpus(now=NOW)
        assert not corpus_cache._entries
        replacement_world, replacement_trade, _, replacement_forecast = (
            _canonical_corpus_fixture(return_forecast=True, forecast_lineage=True)
        )
        active[0] = (replacement_world, replacement_trade, replacement_forecast)
        assert provider.artifact(scope=_canonical_scope(), now=NOW) is not None
        assert len(attempts) == 3
    finally:
        for conn in (*active[0],):
            conn.close()


def test_canonical_provider_warm_corpus_keeps_inmemory_no_reuse(monkeypatch):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    try:
        provider = _canonical_provider(world, trade, forecast)
        original_load = live_fit.load_canonical_fit_corpus
        load_calls = []

        def counted_load(*args, **kwargs):
            load_calls.append(kwargs["training_cutoff"])
            return original_load(*args, **kwargs)

        monkeypatch.setattr(live_fit, "load_canonical_fit_corpus", counted_load)
        assert provider.warm_corpus(now=NOW)
        assert provider.artifact(scope=_canonical_scope(), now=NOW) is not None
        assert load_calls == [NOW, NOW]
    finally:
        world.close()
        trade.close()
        forecast.close()


def test_canonical_provider_reloads_corpus_when_connector_changes_physical_db(tmp_path):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    copies = []
    try:
        forecast.commit()

        def copy(source, name):
            destination = sqlite3.connect(tmp_path / name)
            destination.row_factory = sqlite3.Row
            source.backup(destination)
            copies.append(destination)
            return destination

        first = tuple(copy(conn, name) for conn, name in zip(
            (world, trade, forecast), ("world-a.db", "trade-a.db", "forecast-a.db"), strict=True,
        ))
        second = tuple(copy(conn, name) for conn, name in zip(
            (world, trade, forecast), ("world-b.db", "trade-b.db", "forecast-b.db"), strict=True,
        ))
        second[1].execute("DELETE FROM venue_commands")
        second[1].commit()
        active = [first]
        provider = CanonicalMarketAnchoredFitProvider(
            lambda: active[0], city_timezones=_TEST_CITY_TIMEZONES,
            min_train_rows=1, cache=MarketAnchoredArtifactCache(),
        )
        scope = _canonical_scope()
        assert provider.artifact(scope=scope, now=NOW) is not None
        active[0] = second
        assert provider.artifact(scope=scope, now=NOW) is None
    finally:
        world.close()
        trade.close()
        forecast.close()
        for conn in copies:
            conn.close()


def test_calibration_fit_scope_round_trips_and_rejects_mismatched_contract():
    scope = _canonical_scope()
    assert CalibrationFitScope.from_payload(scope.as_payload()) == scope
    with pytest.raises(ValueError, match="execution_contract"):
        CalibrationFitScope(
            metric="high", execution_mode="TAKER_LIMIT", execution_contract="MAKER_REST",
            raw_probability_revision="fixture-revision-v1",
        )
    payload = scope.as_payload()
    payload["version"] = True
    with pytest.raises(ValueError, match="type or version"):
        CalibrationFitScope.from_payload(payload)


def test_canonical_policy_scope_must_bind_the_actual_corpus_payload():
    provider = CanonicalMarketAnchoredFitProvider(
        lambda: (_ for _ in ()).throw(AssertionError("not called")),
        city_timezones=_TEST_CITY_TIMEZONES, min_train_rows=1,
    )
    scope = _canonical_scope()
    correction = {
        "applied": True, "q_raw": .70, "p0": .35, "alpha_lead": .01,
        "beta": .12, "lambda": provider.calibration_policy.lambda_,
        "lead_bucket": "day1", "calibration_policy": provider.calibration_policy.as_payload(),
        "fit_scope": scope.as_payload(),
    }
    correction["q_corrected"] = live_fit._reproduced_policy_probability(
        provider.calibration_policy, raw_q=.70, p0=.35, alpha_lead=.01,
        beta=.12, lead_bucket="day1", side="YES",
    )
    payload = {"temperature_metric": "high"}
    assert live_fit._sealed_calibration_policy(
        correction, raw_q=.70, p0=.35, payload=payload, side="YES",
        expected_lead_bucket="day1", execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO",
        raw_probability_revision="fixture-revision-v1",
    )[1] is None
    correction["fit_scope"] = _canonical_scope(metric="low").as_payload()
    assert live_fit._sealed_calibration_policy(
        correction, raw_q=.70, p0=.35, payload=payload, side="YES",
        expected_lead_bucket="day1", execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO",
        raw_probability_revision="fixture-revision-v1",
    )[1] == "CALIBRATION_FIT_SCOPE_INVALID"


def test_v1_sealed_corrected_evidence_remains_readable_at_exact_day2():
    policy = _known_policy()
    correction = {
        "applied": True, "q_raw": .70, "p0": .35, "alpha_lead": .01,
        "beta": .12, "lambda": policy.lambda_, "lead_bucket": "day2",
        "calibration_policy": policy.as_payload(),
    }
    correction["q_corrected"] = live_fit._reproduced_policy_probability(
        policy, raw_q=.70, p0=.35, alpha_lead=.01, beta=.12,
        lead_bucket="day2", side="YES",
    )
    accepted, reason = live_fit._sealed_calibration_policy(
        correction, raw_q=.70, p0=.35, payload={"temperature_metric": "high"},
        side="YES", expected_lead_bucket="day2plus",
        legacy_expected_lead_bucket="day2", execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO", raw_probability_revision=None,
    )
    assert accepted == policy.as_payload()
    assert reason is None

    rejected, reason = live_fit._sealed_calibration_policy(
        correction, raw_q=.70, p0=.35, payload={"temperature_metric": "high"},
        side="YES", expected_lead_bucket="day2plus",
        legacy_expected_lead_bucket=None, execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO", raw_probability_revision=None,
    )
    assert rejected is None and reason == "CALIBRATION_POLICY_INVALID"


def test_v2_policy_hash_and_artifact_cache_key_are_distinct_from_v1():
    provider = CanonicalMarketAnchoredFitProvider(
        lambda: (_ for _ in ()).throw(AssertionError("not called")),
        city_timezones=_TEST_CITY_TIMEZONES, min_train_rows=1,
    )
    v1 = _known_policy()
    v2 = provider.calibration_policy
    assert v2.lead_calendar_revision == LEAD_CALENDAR_REVISION
    assert v2.as_payload()["policy_hash"] != v1.as_payload()["policy_hash"]
    key = provider._cache_key(
        (("world", 1, 1), ("trade", 1, 2), ("forecast", 1, 3)),
        _canonical_scope(),
    )
    assert key[-1] == v2.as_payload()["policy_hash"]


def test_canonical_shared_corpus_preserves_cutoff_across_batches_and_scopes(tmp_path, monkeypatch):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    handles = []
    try:
        forecast.commit()
        for source, name in zip((world, trade, forecast), ("world", "trade", "forecast"), strict=True):
            destination = sqlite3.connect(tmp_path / (name + ".db"))
            destination.row_factory = sqlite3.Row
            source.backup(destination)
            handles.append(destination)
        corpus_cache = live_fit.CanonicalCorpusCache()
        artifact_cache = MarketAnchoredArtifactCache()
        cutoffs = []
        load = live_fit.load_canonical_fit_corpus

        def counted_load(*args, **kwargs):
            cutoffs.append(kwargs["training_cutoff"])
            return load(*args, **kwargs)

        monkeypatch.setattr(live_fit, "load_canonical_fit_corpus", counted_load)

        def provider():
            return CanonicalMarketAnchoredFitProvider(
                lambda: tuple(handles), city_timezones=_TEST_CITY_TIMEZONES,
                min_train_rows=1, cache=artifact_cache, corpus_cache=corpus_cache,
            )

        first = provider().artifact(scope=_canonical_scope(), now=NOW)
        assert first is not None
        # A later batch and an unfittable scope use the same completed corpus.
        assert provider().artifact(scope=_canonical_scope(metric="low"), now=NOW + timedelta(hours=1)) is None
        later = provider().artifact(scope=_canonical_scope(), now=NOW + timedelta(hours=5))
        assert later == first
        assert datetime.fromisoformat(later.training_cutoff) == NOW
        assert cutoffs == [NOW]
        # A new scope first fitted at hour five must retain the hour-zero cutoff.
        late_scope = _canonical_scope(execution_contract="FAK_PARTIAL")
        scoped_corpus = next(iter(corpus_cache._entries.values()))[0]
        original_selection = live_fit.CanonicalFitCorpus._fit_selection

        def equivalent_selection(self, **kwargs):
            kwargs["execution_contract"] = "FOK_FULL_OR_ZERO"
            return original_selection(self, **kwargs)

        monkeypatch.setattr(live_fit.CanonicalFitCorpus, "_fit_selection", equivalent_selection)
        delayed = provider().artifact(scope=late_scope, now=NOW + timedelta(hours=5))
        assert delayed is not None and delayed.training_cutoff == first.training_cutoff
        # Exactly TTL refreshes both input and artifact; no second TTL extension.
        refreshed = provider().artifact(scope=late_scope, now=NOW + timedelta(hours=6))
        assert refreshed is not None
        assert datetime.fromisoformat(refreshed.training_cutoff) == NOW + timedelta(hours=6)
        assert cutoffs == [NOW, NOW + timedelta(hours=6)]
        assert next(iter(corpus_cache._entries.values()))[0] is not scoped_corpus
        # Backward requests do not use future outcomes or displace forward cache.
        assert provider().artifact(scope=_canonical_scope(), now=NOW - timedelta(days=4)) is None
        assert provider().artifact(scope=late_scope, now=NOW + timedelta(hours=7)) == refreshed
        assert cutoffs == [NOW, NOW + timedelta(hours=6), NOW - timedelta(days=4)]
    finally:
        for conn in (*handles, world, trade, forecast):
            conn.close()


def test_canonical_shared_corpus_failed_or_late_load_is_retryable():
    cache = live_fit.CanonicalCorpusCache(max_entries=2)
    corpus = live_fit.CanonicalFitCorpus((), {}, 0, NOW.isoformat())
    assert cache.get_or_load(("db",), requested_cutoff=NOW, ttl=timedelta(hours=6), load_current=lambda: None) == (None, None)
    assert not cache._entries
    future = live_fit.CanonicalFitCorpus((), {}, 0, (NOW + timedelta(seconds=1)).isoformat())
    assert cache.get_or_load(("db",), requested_cutoff=NOW, ttl=timedelta(hours=6), load_current=lambda: future) == (None, None)
    assert not cache._entries

    def late():
        time.sleep(.02)
        return corpus

    assert cache.get_or_load(("db",), requested_cutoff=NOW, ttl=timedelta(hours=6), load_current=late,
                             deadline_monotonic=time.monotonic() + .005) == (None, None)
    assert not cache._entries
    assert cache.get_or_load(("db",), requested_cutoff=NOW, ttl=timedelta(hours=6), load_current=lambda: corpus) == (corpus, NOW)
    cache._lock.acquire()
    try:
        assert cache.get_or_load(("db",), requested_cutoff=NOW, ttl=timedelta(hours=6),
                                 load_current=lambda: pytest.fail("locked cache must honor deadline"),
                                 deadline_monotonic=time.monotonic() + .005) == (None, None)
    finally:
        cache._lock.release()
    for key in ("second", "third"):
        cache.get_or_load((key,), requested_cutoff=NOW, ttl=timedelta(hours=6), load_current=lambda: corpus)
    assert len(cache._entries) == 2 and ("db",) not in cache._entries


def test_canonical_shared_corpus_is_deeply_detached_and_immutable():
    cache = live_fit.CanonicalCorpusCache()
    record = {"payout": 1, "policy": {"bounds": [0, 1]}}
    accounting = {"reason": {"codes": ["unknown"]}}
    unknown = {"missing": 1}
    corpus = live_fit.CanonicalFitCorpus((record,), unknown, 1, NOW.isoformat(),
                                         command_accounting=(accounting,))
    first, cutoff = cache.get_or_load(("db",), requested_cutoff=NOW,
                                     ttl=timedelta(hours=6), load_current=lambda: corpus)
    assert first is not corpus and cutoff == NOW
    record["payout"] = 0
    record["policy"]["bounds"].append(2)
    accounting["reason"]["codes"].append("tampered")
    unknown["missing"] = 99
    with pytest.raises(TypeError):
        first.records[0]["payout"] = 0
    with pytest.raises(TypeError):
        first.records[0]["policy"]["bounds"][0] = 2
    with pytest.raises(TypeError):
        first.command_accounting[0]["reason"]["codes"][0] = "tampered"
    with pytest.raises(TypeError):
        first.unknown["missing"] = 2
    second, _ = cache.get_or_load(("db",), requested_cutoff=NOW + timedelta(hours=1),
                                  ttl=timedelta(hours=6), load_current=lambda: pytest.fail("cache miss"))
    assert second.records[0]["payout"] == 1
    assert second.records[0]["policy"]["bounds"] == (0, 1)
    assert second.command_accounting[0]["reason"]["codes"] == ("unknown",)
    assert second.unknown == {"missing": 1}


def _reader_training_commitment(cutoff, scope=None):
    scope = scope or _canonical_scope()
    manifest = CanonicalTrainingManifest.build(
        scope_hash=scope.as_payload()['scope_hash'],
        corpus_revision=live_fit.CANONICAL_CORPUS_REVISION,
        training_cutoff=cutoff.isoformat(), row_count=20, event_count=20,
        weight_sum=20.0,
        max_fill_available_at=(cutoff - timedelta(hours=2)).isoformat(),
        max_label_available_at=(cutoff - timedelta(hours=1)).isoformat(),
        input_hash='a' * 64,
    )
    policy = _known_policy(
        input_revision=live_fit.CANONICAL_CALIBRATION_INPUT_REVISION,
        metric_pooling=live_fit.CANONICAL_CALIBRATION_METRIC_POOLING,
        lead_calendar_revision=LEAD_CALENDAR_REVISION,
    )
    correction = dict(
        applied=True, training_manifest=manifest.as_payload(),
        fit_scope=scope.as_payload(), training_cutoff=cutoff.isoformat(), n_train=20,
    )
    return correction, policy


@pytest.mark.parametrize('side', ['YES', 'NO'])
def test_canonical_reader_preserves_sealed_training_commitment_without_qualifying_profit(side):
    correction, policy = _reader_training_commitment(NOW - timedelta(days=3))
    world, trade, _ = _canonical_corpus_fixture(
        side=side, calibration_policy_payload=policy.as_payload(),
        correction_extra_fields=correction,
        correction_alpha_lead=math.log(.52 / .48) - math.log(.35 / .65),
    )
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.command_count == 1 and corpus.unknown == {}
        record, = corpus.records
        accounting, = corpus.command_accounting
        for row in (record, accounting):
            assert row['calibration_training_manifest'] == correction['training_manifest']
            assert row['calibration_training_manifest_reason'] is None
            assert not any('oos_pass' in key or 'qualified' in key for key in row)
        assert record['acting_q'] == .52
        assert len(corpus.fit_rows(
            metric='high', execution_mode='TAKER_LIMIT',
            execution_contract='FOK_FULL_OR_ZERO', probability_revision='fixture-revision-v1',
        )) == 1
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize('applied', [True, False])
def test_missing_training_commitment_stays_explicit_without_deleting_physical_denominator(applied):
    world, trade, _ = _canonical_corpus_fixture(corrected=applied)
    try:
        corpus = _read_canonical(world, trade)
        assert corpus.command_count == len(corpus.records) == len(corpus.command_accounting) == 1
        expected = 'CALIBRATION_TRAINING_MANIFEST_MISSING' if applied else 'CALIBRATION_NOT_APPLIED'
        for row in (*corpus.records, *corpus.command_accounting):
            assert row['calibration_training_manifest'] is None
            assert row['calibration_training_manifest_reason'] == expected
    finally:
        world.close()
        trade.close()


@pytest.mark.parametrize('mutation,reason', [
    ('hash', 'INVALID'), ('scope', 'SCOPE_MISMATCH'),
    ('cutoff', 'CUTOFF_MISMATCH'), ('naive_cutoff', 'CUTOFF_MISMATCH'),
    ('future', 'DECISION_CLOCK_UNBOUND'), ('naive_decision', 'DECISION_CLOCK_UNBOUND'),
    ('count', 'COUNT_MISMATCH'), ('boolean_count', 'COUNT_MISMATCH'),
    ('revision', 'REVISION_MISMATCH'),
])
def test_training_commitment_reader_rejects_unbound_provenance(mutation, reason):
    correction, policy = _reader_training_commitment(NOW)
    decision = NOW
    scope = _canonical_scope()
    if mutation == 'hash':
        correction['training_manifest']['input_hash'] = 'b' * 64
    elif mutation == 'scope':
        scope = _canonical_scope(metric='low')
    elif mutation == 'cutoff':
        correction['training_cutoff'] = (NOW - timedelta(seconds=1)).isoformat()
    elif mutation == 'naive_cutoff':
        correction['training_cutoff'] = NOW.replace(tzinfo=None).isoformat()
    elif mutation == 'future':
        decision = NOW - timedelta(microseconds=1)
    elif mutation == 'naive_decision':
        decision = NOW.replace(tzinfo=None)
    elif mutation == 'count':
        correction['n_train'] = 19
    elif mutation == 'boolean_count':
        correction['n_train'] = True
    elif mutation == 'revision':
        policy = _known_policy()
    manifest, error = live_fit._sealed_training_manifest(
        correction, calibration_policy=policy.as_payload(), decision_at=decision, scope=scope,
    )
    assert manifest is None
    assert error == 'CALIBRATION_TRAINING_MANIFEST_' + reason


def test_training_commitment_reader_accepts_causal_equal_cutoff_in_equivalent_timezone():
    correction, policy = _reader_training_commitment(NOW)
    manifest, reason = live_fit._sealed_training_manifest(
        correction, calibration_policy=policy.as_payload(),
        decision_at=NOW.astimezone(timezone(timedelta(hours=-5))), scope=_canonical_scope(),
    )
    assert manifest == correction['training_manifest'] and reason is None
    assert live_fit._sealed_training_manifest(
        correction, calibration_policy=None, decision_at=NOW, scope=_canonical_scope(),
    ) == (None, 'CALIBRATION_POLICY_UNAVAILABLE')


def _held_entry_reader_fixture(monkeypatch, *, side="NO"):
    """Exact ENTRY event/certificate/audit evidence; no corpus tables exist."""

    trade = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    trade.execute("CREATE TABLE position_events (position_id TEXT, event_type TEXT, sequence_no INTEGER, decision_id TEXT, payload_json TEXT)")
    trade.execute("CREATE TABLE position_decision_attribution (position_id TEXT, intent_kind TEXT, resolution TEXT, decision_certificate_hash TEXT)")
    world.execute("CREATE TABLE decision_certificates (certificate_hash TEXT, certificate_type TEXT, mode TEXT, verifier_status TEXT, payload_json TEXT, payload_hash TEXT)")
    scope = CalibrationFitScope("high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "raw-revision-v1")
    policy = CalibrationPolicySpec(
        algorithm_revision=CALIBRATION_ALGORITHM_REVISION,
        input_revision=CANONICAL_CALIBRATION_INPUT_REVISION,
        metric_pooling=CANONICAL_CALIBRATION_METRIC_POOLING,
        lead_calendar_revision=LEAD_CALENDAR_REVISION,
        lambda_=10.0, min_train_weight=20, beta_bounds=(0.0, 1.0),
        logit_clip=CLIP_D, probability_clip=(P_CLIP_LO, P_CLIP_HI), refit_seconds=21600.0,
    )
    manifest = CanonicalTrainingManifest.build(
        scope_hash=scope.as_payload()["scope_hash"], corpus_revision=CANONICAL_CORPUS_REVISION,
        training_cutoff="2026-08-25T00:00:00+00:00", row_count=20, event_count=20,
        weight_sum=20.0, max_fill_available_at="2026-08-24T00:00:00+00:00",
        max_label_available_at="2026-08-24T00:00:00+00:00", input_hash="a" * 64,
    )
    artifact = ResidualCalibratorArtifact(
        alpha={"day0": 0.17, "day1": 0.09, "day2plus": 0.01}, beta=0.4,
        lambda_=10.0, clip_d=CLIP_D, p_clip=(P_CLIP_LO, P_CLIP_HI),
        lead_buckets=LEAD_BUCKETS, training_cutoff=manifest.training_cutoff,
        n_train=20, n_excluded=0, excluded_reasons={}, param_hash="",
        lead_calendar_revision=LEAD_CALENDAR_REVISION,
        city_timezone_snapshot=(("Warsaw", "Europe/Warsaw"),), training_manifest=manifest,
    )
    artifact = replace(artifact, param_hash=_param_hash(
        alpha=artifact.alpha, beta=artifact.beta, lambda_=artifact.lambda_, clip_d=artifact.clip_d,
        p_clip=artifact.p_clip, lead_buckets=artifact.lead_buckets,
        training_cutoff=artifact.training_cutoff, lead_calendar_revision=artifact.lead_calendar_revision,
        city_timezone_snapshot=artifact.city_timezone_snapshot,
    ))
    entry_at = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
    entry = corrected_probability(
        artifact, p0=0.30, q_raw=0.60, city="Warsaw", target_date=date(2026, 8, 27),
        decision_at=entry_at, side=side,
    )
    assert entry is not None
    token = "no-token" if side == "NO" else "yes-token"
    correction = PayoffQCorrection(
        family_key="Warsaw|2026-08-27|high", bin_id="bin-a", token_id=token, side=side,
        raw_q=0.60, corrected_q=entry[0], p0=0.30, lead_bucket=entry[1], alpha_lead=entry[2],
        beta=artifact.beta, lambda_=artifact.lambda_, training_cutoff=artifact.training_cutoff,
        n_train=artifact.n_train, param_hash=artifact.param_hash, calibration_policy=policy,
        fit_scope=scope, training_manifest=manifest,
    )
    receipt = {
        "decision_log_id": 7, "decision_log_mode": "global_single_order_auction",
        "receipt_hash": "b" * 64, "execution_binding_hash": "c" * 64,
        "artifact_summary_hash": "d" * 64, "schema_version": 22, "winner_event_id": "event-a",
        "winner_candidate_id": "candidate-a", "winner_actuation_identity": "actuation-a",
        "selection_epoch_identity": "epoch-a",
    }
    certificate = {
        "global_auction_receipt": receipt, "direction": "buy_no" if side == "NO" else "buy_yes",
        "token_id": token, "global_token_id": token, "global_family_key": correction.family_key,
        "global_bin_id": correction.bin_id, "market_anchored_correction": correction.as_cert_fields(),
        "event_id": "event-a", "final_intent_id": "intent-a",
    }
    trade.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?)",
        ("position-a", "ENTRY_ORDER_FILLED", 1, "cert-a", json.dumps({"decision_log_id": 7})),
    )
    trade.execute("INSERT INTO position_decision_attribution VALUES (?,?,?,?)", ("position-a", "ENTRY", "ATTRIBUTED", "cert-a"))
    from src.decision_kernel.canonicalization import stable_hash

    world.execute(
        "INSERT INTO decision_certificates VALUES (?,?,?,?,?,?)",
        ("cert-a", "ActionableTradeCertificate", "LIVE", "VERIFIED", json.dumps(certificate), stable_hash(certificate)),
    )
    audit = {
        "revision": "canonical_entry_fit_artifact_audit_v1",
        "consulted_scopes": {f"{scope.as_payload()['scope_hash']}:{artifact.param_hash}": {
            "status": "AVAILABLE", "param_hash": artifact.param_hash, "artifact": asdict(artifact),
            "scope": scope.as_payload(), "policy": policy.as_payload(),
        }},
        "unavailable_scopes": {},
    }
    monkeypatch.setattr(
        live_fit,
        "_load_held_audit_context",
        lambda *_args, **_kwargs: {"market_anchored_fit_artifact_audit": audit},
    )
    return trade, world, artifact, token, side, correction


def test_held_entry_reader_binds_entry_provenance_and_applies_current_no_q(monkeypatch):
    trade, world, artifact, token, side, correction = _held_entry_reader_fixture(monkeypatch)
    try:
        monkeypatch.setattr(
            sqlite3, "connect",
            lambda *_args, **_kwargs: pytest.fail("held reader must borrow its handles"),
        )
        binding = live_fit.load_held_entry_calibration(
            trade, position_id="position-a", token_id=token, side=side, world_conn=world,
        )
        assert binding.fit_scope.execution_contract == "FOK_FULL_OR_ZERO"
        assert binding.artifact.param_hash == correction.param_hash
        current = binding.corrected_probability(
            family_key=correction.family_key, bin_id=correction.bin_id, token_id=token, side=side,
            raw_q=0.40, p0=0.20, city="Warsaw", target_date=date(2026, 8, 26),
            decision_at=datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
        )
        assert current.raw_q == pytest.approx(0.40)
        assert current.corrected_q != pytest.approx(correction.corrected_q)
        assert current.alpha_lead == pytest.approx(-artifact.alpha["day0"])
    finally:
        trade.close()
        world.close()


def test_held_entry_reader_accepts_normal_entry_writer_provenance(monkeypatch):
    trade, world, _artifact, token, side, correction = _held_entry_reader_fixture(monkeypatch)
    try:
        trade.execute("DELETE FROM position_events")
        position = Position(
            trade_id="position-a", market_id="market-a", city="Warsaw", cluster="cluster-a",
            target_date="2026-08-27", bin_label="bin-a", direction="buy_no",
            entered_at="2026-08-26T12:00:00+00:00", order_posted_at="2026-08-26T12:00:00+00:00",
            strategy_key="market_anchored", env="live",
        )
        events, _projection = build_entry_canonical_write(
            position, phase_after=ACTIVE, decision_id="cert-a",
        )
        assert all("decision_log_id" not in json.loads(event["payload_json"]) for event in events)
        trade.executemany(
            """
            INSERT INTO position_events (position_id, event_type, sequence_no, decision_id, payload_json)
            VALUES (:position_id, :event_type, :sequence_no, :decision_id, :payload_json)
            """,
            events,
        )
        binding = live_fit.load_held_entry_calibration(
            trade, position_id="position-a", token_id=token, side=side, world_conn=world,
        )
        assert binding.decision_log_id == 7
        assert binding.decision_certificate_hash == "cert-a"
        assert binding.family_key == correction.family_key
    finally:
        trade.close()
        world.close()


def test_held_entry_reader_allows_unbounded_repeated_entry_identity(monkeypatch):
    trade, world, _artifact, token, side, _correction = _held_entry_reader_fixture(monkeypatch)
    try:
        trade.executemany(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            [
                ("position-a", "ENTRY_ORDER_FILLED", sequence, "cert-a", "{}")
                for sequence in range(2, 22)
            ],
        )
        assert live_fit.load_held_entry_calibration(
            trade, position_id="position-a", token_id=token, side=side, world_conn=world,
        ).decision_certificate_hash == "cert-a"
    finally:
        trade.close()
        world.close()


@pytest.mark.parametrize("mixed_writer_and_recovery", [False, True])
def test_held_entry_reader_authenticates_edli_opening_decision_identity(monkeypatch, mixed_writer_and_recovery):
    trade, world, _artifact, token, side, _correction = _held_entry_reader_fixture(monkeypatch)
    try:
        decision_id = f"edli_exec_cmd:event-a:intent-a:{token}:buy_no"
        trade.execute("DELETE FROM position_events")
        trade.executemany(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            [
                ("position-a", event_type, sequence, "cert-a" if mixed_writer_and_recovery and sequence == 1 else decision_id, "{}")
                for sequence, event_type in enumerate(
                    ("POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED"), start=1,
                )
            ],
        )
        assert live_fit.load_held_entry_calibration(
            trade, position_id="position-a", token_id=token, side=side, world_conn=world,
        ).decision_log_id == 7
    finally:
        trade.close()
        world.close()


def test_held_entry_reader_rejects_ninth_conflicting_entry_identity(monkeypatch):
    trade, world, _artifact, token, side, _correction = _held_entry_reader_fixture(monkeypatch)
    try:
        trade.executemany(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            [
                ("position-a", "ENTRY_ORDER_FILLED", sequence, "cert-a", "{}")
                for sequence in range(2, 10)
            ] + [("position-a", "ENTRY_ORDER_FILLED", 10, "other-cert", "{}")],
        )
        with pytest.raises(live_fit.PayoffQCorrectionUnavailable):
            live_fit.load_held_entry_calibration(
                trade, position_id="position-a", token_id=token, side=side, world_conn=world,
            )
    finally:
        trade.close()
        world.close()


def test_held_entry_reader_rejects_malformed_optional_receipt_identity(monkeypatch):
    trade, world, _artifact, token, side, _correction = _held_entry_reader_fixture(monkeypatch)
    try:
        trade.execute(
            "UPDATE position_events SET payload_json = ? WHERE position_id = ?",
            (json.dumps({"decision_log_id": "7"}), "position-a"),
        )
        with pytest.raises(live_fit.PayoffQCorrectionUnavailable):
            live_fit.load_held_entry_calibration(
                trade, position_id="position-a", token_id=token, side=side, world_conn=world,
            )
    finally:
        trade.close()
        world.close()


@pytest.mark.parametrize(("encoded", "raw_limit"), [
    (zlib.compress(b"x" * 9), 8),
    (zlib.compress(b"{}") + b"trailing-data", None),
])
def test_held_audit_decoder_rejects_bounded_and_trailing_compressed_payloads(
    monkeypatch, encoded, raw_limit,
):
    if raw_limit is not None:
        monkeypatch.setattr(live_fit, "_ENTRY_AUDIT_MAX_RAW_BYTES", raw_limit)
    with pytest.raises(live_fit.PayoffQCorrectionUnavailable):
        live_fit._decode_held_audit_payload(
            base64.b64encode(encoded).decode(), expected_hash=hashlib.sha256(b"{}").hexdigest(),
        )


@pytest.mark.parametrize("mutation", ["token", "scope", "param", "correction"])
def test_held_entry_reader_rejects_any_unbound_entry_proof(monkeypatch, mutation):
    trade, world, _artifact, token, side, _correction = _held_entry_reader_fixture(monkeypatch)
    try:
        if mutation == "token":
            token = "wrong-token"
        elif mutation == "scope":
            audit = live_fit._load_held_audit_context(None)["market_anchored_fit_artifact_audit"]
            next(iter(audit["consulted_scopes"].values()))["scope"]["metric"] = "low"
        elif mutation == "param":
            audit = live_fit._load_held_audit_context(None)["market_anchored_fit_artifact_audit"]
            next(iter(audit["consulted_scopes"].values()))["param_hash"] = "f" * 64
        else:
            payload = json.loads(world.execute("SELECT payload_json FROM decision_certificates").fetchone()[0])
            payload["market_anchored_correction"]["q_corrected"] = 0.01
            world.execute("UPDATE decision_certificates SET payload_json = ?", (json.dumps(payload),))
        with pytest.raises(live_fit.PayoffQCorrectionUnavailable):
            live_fit.load_held_entry_calibration(trade, position_id="position-a", token_id=token, side=side, world_conn=world)
    finally:
        trade.close()
        world.close()


def test_held_audit_delta_reconstructs_only_authenticated_parent(monkeypatch):
    base = {"revision": "canonical_entry_fit_artifact_audit_v1", "consulted_scopes": {}, "unavailable_scopes": {}}
    middle = {**base, "consulted_scopes": {"scope:param": {"param_hash": "param"}}}
    current = {**middle, "unavailable_scopes": {"scope:unavailable": {"reason": "missing"}}}
    base_bytes = live_fit._canonical_json_bytes(base) if hasattr(live_fit, "_canonical_json_bytes") else json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
    middle_delta = {"removed_keys": [], "replacements": {"consulted_scopes": middle["consulted_scopes"]}}
    middle_delta_bytes = json.dumps(middle_delta, sort_keys=True, separators=(",", ":")).encode()
    current_delta = {"removed_keys": [], "replacements": {"unavailable_scopes": current["unavailable_scopes"]}}
    current_delta_bytes = json.dumps(current_delta, sort_keys=True, separators=(",", ":")).encode()
    middle_bytes = json.dumps(middle, sort_keys=True, separators=(",", ":")).encode()
    current_bytes = json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    summaries = {
        1: {"audit_context_encoding": "zlib+base64+canonical-json-object-v1", "audit_context_sha256": hashlib.sha256(base_bytes).hexdigest(), "audit_context_zlib_b64": base64.b64encode(zlib.compress(base_bytes)).decode()},
        2: {"audit_context_encoding": "zlib+base64+canonical-json-object-v1", "audit_context_delta_encoding": "zlib+base64+canonical-json-object-delta-v1", "audit_context_sha256": hashlib.sha256(middle_bytes).hexdigest(), "audit_context_delta_sha256": hashlib.sha256(middle_delta_bytes).hexdigest(), "audit_context_delta_zlib_b64": base64.b64encode(zlib.compress(middle_delta_bytes)).decode(), "audit_context_base_decision_log_id": 1, "audit_context_base_mode": "global_single_order_auction", "audit_context_base_receipt_hash": "base", "audit_context_base_sha256": hashlib.sha256(base_bytes).hexdigest(), "audit_context_delta_chain_depth": 1},
        3: {"audit_context_encoding": "zlib+base64+canonical-json-object-v1", "audit_context_delta_encoding": "zlib+base64+canonical-json-object-delta-v1", "audit_context_sha256": hashlib.sha256(current_bytes).hexdigest(), "audit_context_delta_sha256": hashlib.sha256(current_delta_bytes).hexdigest(), "audit_context_delta_zlib_b64": base64.b64encode(zlib.compress(current_delta_bytes)).decode(), "audit_context_base_decision_log_id": 2, "audit_context_base_mode": "global_single_order_auction_delta", "audit_context_base_receipt_hash": "middle", "audit_context_base_sha256": hashlib.sha256(middle_bytes).hexdigest(), "audit_context_delta_chain_depth": 2},
    }
    monkeypatch.setattr(live_fit, "_receipt_summary", lambda _conn, *, decision_log_id, **_kwargs: summaries[decision_log_id])
    assert live_fit._load_held_audit_context(None, decision_log_id=3, expected_mode="global_single_order_auction_delta", expected_receipt_hash="child") == current


def _current_held_artifact(entry, *, cutoff=NOW, beta=0.5, scope_hash=None):
    manifest = entry.training_manifest
    manifest = CanonicalTrainingManifest.build(
        scope_hash=scope_hash or manifest.scope_hash, corpus_revision=manifest.corpus_revision,
        training_cutoff=cutoff.isoformat(), row_count=manifest.row_count,
        event_count=manifest.event_count, weight_sum=manifest.weight_sum,
        max_fill_available_at=manifest.max_fill_available_at,
        max_label_available_at=manifest.max_label_available_at, input_hash=manifest.input_hash,
    )
    artifact = replace(entry, beta=beta, training_cutoff=cutoff.isoformat(), training_manifest=manifest)
    return replace(artifact, param_hash=_param_hash(
        alpha=artifact.alpha, beta=artifact.beta, lambda_=artifact.lambda_, clip_d=artifact.clip_d,
        p_clip=artifact.p_clip, lead_buckets=artifact.lead_buckets,
        training_cutoff=artifact.training_cutoff, lead_calendar_revision=artifact.lead_calendar_revision,
        city_timezone_snapshot=artifact.city_timezone_snapshot,
    ))


@pytest.mark.parametrize('side', ['YES', 'NO'])
def test_held_policy_refits_without_rewriting_entry_and_responds_to_falling_q(monkeypatch, side):
    trade, world, entry, token, side, entry_correction = _held_entry_reader_fixture(monkeypatch, side=side)
    try:
        binding = live_fit.load_held_entry_calibration(
            trade, position_id='position-a', token_id=token, side=side, world_conn=world,
        )
        # A beta=0 entry fit ignores raw-q declines. The same adaptive policy
        # can learn beta>0 without changing entry attribution or risk limits.
        frozen = replace(binding, artifact=_current_held_artifact(entry, beta=0.0))
        current = _current_held_artifact(entry, beta=0.5)
        calls = []
        def get_artifact(**kwargs):
            calls.append(kwargs)
            return current
        provider = SimpleNamespace(calibration_policy=binding.calibration_policy, artifact=get_artifact)
        refreshed = frozen.at_decision(provider, decision_at=NOW, current_raw_revision=binding.fit_scope.raw_probability_revision, deadline_monotonic=123.0)
        kwargs = dict(family_key=binding.family_key, bin_id=binding.bin_id, token_id=token,
                      side=side, p0=0.30, city='Warsaw', target_date=date(2026, 8, 28), decision_at=NOW)
        assert frozen.corrected_probability(raw_q=.9, **kwargs).corrected_q == pytest.approx(
            frozen.corrected_probability(raw_q=.1, **kwargs).corrected_q)
        assert refreshed.corrected_probability(raw_q=.1, **kwargs).corrected_q < .30
        assert refreshed.corrected_probability(raw_q=.9, **kwargs).corrected_q > .30
        assert refreshed.decision_certificate_hash == binding.decision_certificate_hash
        assert refreshed.calibration_policy is binding.calibration_policy
        assert refreshed.fit_scope is binding.fit_scope
        assert binding.artifact.param_hash == entry_correction.param_hash
        assert refreshed.artifact is current
        assert calls == [dict(scope=binding.fit_scope, now=NOW, minimum_cutoff=NOW, deadline_monotonic=123.0)]
    finally:
        trade.close()
        world.close()


@pytest.mark.parametrize('invalid', ['policy', 'missing', 'future', 'expired', 'before_entry', 'scope'])
def test_held_policy_rejects_unavailable_or_noncausal_current_fit(monkeypatch, invalid):
    trade, world, entry, token, side, _ = _held_entry_reader_fixture(monkeypatch)
    try:
        binding = live_fit.load_held_entry_calibration(
            trade, position_id='position-a', token_id=token, side=side, world_conn=world,
        )
        policy = binding.calibration_policy
        cutoff = {'future': NOW + timedelta(seconds=1), 'expired': NOW - timedelta(hours=6),
                  'before_entry': datetime(2026, 8, 24, 12, tzinfo=timezone.utc)}.get(invalid, NOW)
        artifact = _current_held_artifact(entry, cutoff=cutoff, scope_hash='f' * 64 if invalid == 'scope' else None)
        if invalid == 'policy':
            policy = replace(policy, lambda_=policy.lambda_ + 1)
        provider = SimpleNamespace(calibration_policy=policy, artifact=lambda **_: None if invalid == 'missing' else artifact)
        with pytest.raises(live_fit.PayoffQCorrectionUnavailable, match='CURRENT_'):
            binding.at_decision(provider, decision_at=NOW, current_raw_revision=binding.fit_scope.raw_probability_revision)
        assert binding.artifact is not artifact
    finally:
        trade.close()
        world.close()


def test_canonical_attached_handles_share_fit_and_refresh_older_than_entry_cache(monkeypatch, tmp_path):
    world, trade, _, forecast = _canonical_corpus_fixture(return_forecast=True, forecast_lineage=True)
    handles = []
    attached = None
    try:
        forecast.commit()
        for source, name in zip((world, trade, forecast), ('world', 'trade', 'forecast'), strict=True):
            conn = sqlite3.connect(tmp_path / (name + '.db'))
            conn.row_factory = sqlite3.Row
            source.backup(conn)
            handles.append(conn)
        attached = sqlite3.connect(tmp_path / 'trade.db')
        attached.execute('ATTACH DATABASE ? AS world', (str(tmp_path / 'world.db'),))
        attached.execute('ATTACH DATABASE ? AS forecasts', (str(tmp_path / 'forecast.db'),))
        cache, corpus_cache = MarketAnchoredArtifactCache(), live_fit.CanonicalCorpusCache()
        common = dict(city_timezones=_TEST_CITY_TIMEZONES, min_train_rows=1, cache=cache, corpus_cache=corpus_cache)
        direct = CanonicalMarketAnchoredFitProvider(lambda: tuple(handles), **common)
        monitor = CanonicalMarketAnchoredFitProvider(lambda: (attached, attached, attached),
                    world_schema='world', forecast_schema='forecasts', **common)
        original_load = live_fit.load_canonical_fit_corpus
        calls = []
        def load(*args, **kwargs):
            calls.append(kwargs)
            return original_load(*args, **kwargs)
        monkeypatch.setattr(live_fit, 'load_canonical_fit_corpus', load)
        monkeypatch.setattr(sqlite3, 'connect', lambda *_, **__: pytest.fail('must borrow DB handles'))
        first = direct.artifact(scope=_canonical_scope(), now=NOW)
        assert first is not None
        assert monitor.artifact(scope=_canonical_scope(), now=NOW + timedelta(minutes=1)) is first
        assert len(calls) == 1
        # A position admitted by another process with a newer fit must not wait
        # six hours for this process's older but otherwise fresh cache to drain.
        newer_cut = NOW + timedelta(minutes=2)
        refreshed = monitor.artifact(scope=_canonical_scope(), now=newer_cut, minimum_cutoff=newer_cut)
        assert refreshed is not None and refreshed.training_cutoff == newer_cut.isoformat()
        assert len(calls) == 2 and calls[-1]['world_schema'] == 'world'
        assert calls[-1]['forecast_schema'] == 'forecasts'
        assert direct.artifact(scope=_canonical_scope(), now=newer_cut) is refreshed
        assert monitor.artifact(scope=_canonical_scope(), now=newer_cut, minimum_cutoff=newer_cut + timedelta(seconds=1)) is None
        assert monitor.artifact(scope=_canonical_scope(), now=newer_cut, deadline_monotonic=time.monotonic()-1) is None
        assert len(calls) == 2
    finally:
        for conn in (*handles, world, trade, forecast, attached):
            if conn is not None:
                conn.close()


@pytest.mark.parametrize('side', ['YES', 'NO'])
@pytest.mark.parametrize('available', [True, False])
@pytest.mark.parametrize('day0', [False, True])
def test_monitor_uses_same_decision_clock_for_current_fit_and_correction(monkeypatch, side, available, day0):
    from src.state import portfolio as portfolio_module
    from tests.test_exit_market_anchored_q import _held_position, _exit_context

    trade, world, entry, token, side, correction = _held_entry_reader_fixture(monkeypatch, side=side)
    try:
        current = _current_held_artifact(entry)
        calls = []
        def artifact(**kwargs):
            calls.append(kwargs)
            return current if available else None
        fit_provider = SimpleNamespace(calibration_policy=correction.calibration_policy, artifact=artifact)
        provider = live_fit.HeldEntryCalibrationProvider(
            trade, world_conn=world, fit_provider=fit_provider, deadline_monotonic=123.0,
        )
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        monkeypatch.setattr(portfolio_module, 'datetime', Clock)
        position = _held_position('buy_yes' if side == 'YES' else 'buy_no', target_date='2026-08-28')
        position.trade_id = 'position-a'
        context = _exit_context(fresh_prob=.1, current_market_price=.3, best_bid=.3)
        context = replace(context, probability_receipt={'probability_semantics_revision': correction.fit_scope.raw_probability_revision})
        if day0:
            from src.events.day0_authority import bind_day0_probability_semantics
            context = replace(context, day0_active=True, probability_receipt={
                'probability_semantics_revision': correction.fit_scope.raw_probability_revision,
                'q_version': bind_day0_probability_semantics('current-day0-q'),
            })
        with live_fit.active_provider_scope(provider):
            q, evidence_ok, source = position._exit_q_mean_and_source(context)
        if day0:
            assert calls == []
        else:
            assert calls[0]['now'] == NOW and calls[0]['deadline_monotonic'] == 123.0
        if available or day0:
            expected = corrected_probability(entry if day0 else current, q_raw=.1, p0=.3, city='Warsaw',
                target_date=date(2026, 8, 28), decision_at=NOW, side=side)[0]
            assert float(q) == pytest.approx(expected)
            assert evidence_ok and source == 'market_anchored'
        else:
            assert not evidence_ok and source == 'entry_calibration_unavailable'
    finally:
        trade.close()
        world.close()


@pytest.mark.parametrize('revision', [None, '', 'day0_hourly_ens_source_clock_carrier_v15'])
def test_unidentified_or_changed_raw_revision_keeps_entry_parameters_without_adaptive_authority(monkeypatch, revision):
    trade, world, entry, token, side, correction = _held_entry_reader_fixture(monkeypatch)
    try:
        binding = live_fit.load_held_entry_calibration(
            trade, position_id='position-a', token_id=token, side=side, world_conn=world,
        )
        provider = SimpleNamespace(calibration_policy=binding.calibration_policy,
            artifact=lambda **_: pytest.fail('a same-entry-scope refit cannot prove cross-revision transport'))
        result = binding.at_decision(provider, decision_at=NOW, current_raw_revision=revision)
        assert result is binding and result.adaptive_authority is False
        assert result.artifact.param_hash == correction.param_hash
    finally:
        trade.close()
        world.close()
