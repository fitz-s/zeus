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
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.calibration.market_anchored_live_fit import (
    MarketAnchoredFitProvider,
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
        "graded_at": None,
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


def test_rows_missing_decision_time_or_target_date_are_skipped():
    rows = _settled_rows(4)
    rows[0]["decision_posterior_computed_at"] = None
    rows[1]["target_date"] = None
    conn = _memory_db(rows)

    assert len(load_fit_rows(conn, training_cutoff=NOW, city_timezone_snapshot=tuple(_TEST_CITY_TIMEZONES.items()))) == 2


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
