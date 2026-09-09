# Created: 2026-08-27
# Last reused or audited: 2026-09-08
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

import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.calibration.market_anchored_live_fit as live_fit
from src.calibration.market_anchored_live_fit import (
    MarketAnchoredArtifactCache,
    MarketAnchoredFitProvider,
    _sqlite_fit_deadline,
    corrected_probability,
    load_fit_rows,
)
from src.calibration.market_anchored_residual import (
    CLIP_D,
    LEAD_BUCKETS,
    P_CLIP_HI,
    P_CLIP_LO,
    ResidualCalibratorArtifact,
)

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
    assert set(artifact.alpha) == {"day0", "day1", "day2"}


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
        [_row(i, settled_at=NOW - timedelta(days=3), lead_days=7) for i in range(6)]
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
    assert corrected_probability(**{**kwargs, "target_date": date(2026, 9, 3)}) is None


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


def _synthetic_artifact(*, alpha_day1: float, beta: float) -> ResidualCalibratorArtifact:
    alpha = {bucket: (alpha_day1 if bucket == "day1" else 0.0) for bucket in LEAD_BUCKETS}
    return ResidualCalibratorArtifact(
        alpha=alpha,
        beta=beta,
        lambda_=10.0,
        clip_d=CLIP_D,
        p_clip=(P_CLIP_LO, P_CLIP_HI),
        lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-08-25T00:00:00Z",
        n_train=100,
        n_excluded=0,
        excluded_reasons={},
        param_hash="synthetic",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("city-0", "UTC"),),
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


def _canonical_corpus_fixture(*, side="YES", corrected=True, metric="high", size=10.0):
    """Real certificate hashing and canonical economic revisions in private DBs."""
    import json
    from src.decision_kernel.certificate import build_certificate, certificate_payload_json
    from src.decision_kernel.ledger import DecisionCertificateLedger

    world = sqlite3.connect(":memory:")
    trade = sqlite3.connect(":memory:")
    decision = NOW - timedelta(days=3)
    parent = build_certificate(
        certificate_type="ProbabilityEvidenceCertificate", semantic_key="fixture-parent",
        claim_type="fixture", mode="LIVE", decision_time=decision,
        source_available_at=decision, agent_received_at=decision, persisted_at=decision,
        payload={}, authority_id="fixture", authority_version="1", algorithm_id="fixture", algorithm_version="1",
    )
    token = "11" if side == "YES" else "12"
    economics = {
        "payoff_q_point": .52 if corrected else .70,
        "market_anchored_correction": ({"applied": True, "q_raw": .70, "q_corrected": .52, "p0": .35}
                                       if corrected else {"applied": False}),
        "global_execution_mode": "TAKER_LIMIT", "decision_p0": .35,
        "decision_p0_source": "snapshot", "global_book_hash": "book-hash",
        "global_candidate_id": "candidate", "global_token_id": token,
        "global_jit_execution_curve_identity": "curve-hash",
    }
    payload = {"candidate_id": "candidate", "condition_id": "condition", "token_id": token,
               "q_live": .52 if corrected else .70, "direction": "buy_yes" if side == "YES" else "buy_no", "city": "Austin", "target_date": (decision.date()+timedelta(days=1)).isoformat(),
               "temperature_metric": metric, "qkernel_execution_economics": economics}
    certificate = build_certificate(
        certificate_type="ActionableTradeCertificate", semantic_key="fixture-entry", claim_type="fixture",
        mode="LIVE", decision_time=decision, source_available_at=decision, agent_received_at=decision,
        persisted_at=decision, payload=payload, parent_certificates=(parent,),
        authority_id="fixture", authority_version="1", algorithm_id="fixture", algorithm_version="1",
    )
    ledger = DecisionCertificateLedger(world)
    # Fixture isolates corpus validation; execution verifier has its own suite.
    ledger.insert_idempotent(parent, preverified=True)
    ledger.insert_idempotent(certificate, preverified=True)
    trade.executescript("""
      CREATE TABLE venue_commands(command_id TEXT, token_id TEXT, created_at TEXT,
        venue_order_id TEXT, snapshot_id TEXT, intent_kind TEXT, side TEXT);
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
    """)
    trade.execute("INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?)", ("command", token, decision.isoformat(), "order", "snapshot", "ENTRY", "BUY"))
    trade.execute("INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?,?)", (
        "snapshot", "condition", "11", "12", token, .35, "book-hash", decision.isoformat(), json.dumps({"YES":"11","NO":"12"})))
    trade.execute("INSERT INTO position_decision_attribution VALUES (?,?,?,?)", ("command",certificate.certificate_hash,"ENTRY",decision.isoformat()))
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
    return world, trade, certificate_payload_json(certificate)


def _read_canonical(world, trade, cutoff=NOW):
    return live_fit.load_canonical_fit_corpus(world, trade, training_cutoff=cutoff,
        city_timezone_snapshot=tuple(sorted(_TEST_CITY_TIMEZONES.items())))


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
        fit_row, = corpus.fit_rows(metric="high", execution_mode="TAKER_LIMIT")
        assert fit_row.q_raw == pytest.approx(.70 if side=="YES" else .30)
        assert fit_row.p0 == pytest.approx(.35 if side=="YES" else .65)
        assert fit_row.y == 1 and fit_row.w == 1
        assert corpus.fit_rows(metric="low", execution_mode="TAKER_LIMIT") == []
        assert corpus.fit_rows(metric="high", execution_mode="MAKER_REST") == []
    finally:
        world.close()
        trade.close()


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
    records = tuple(dict(metric="high",execution_mode="MAKER_REST",event_key=("Austin","2026-08-01","high"),
        lead_bucket="day1",side="YES",p0=.4,q_raw=.6,payout=1,confirmed_shares=shares) for shares in (10,30))
    corpus = live_fit.CanonicalFitCorpus(records, {}, 2, NOW.isoformat())
    rows = corpus.fit_rows(metric="high",execution_mode="MAKER_REST")
    assert [row.w for row in rows] == [.25,.75]
    assert sum(row.w for row in rows) == 1
