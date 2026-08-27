# Created: 2026-08-27
# Last reused or audited: 2026-08-27
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

import pytest

from src.calibration.market_anchored_live_fit import (
    MarketAnchoredFitProvider,
    corrected_probability,
    load_fit_rows,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


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
            graded_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO settlement_attribution (
            attribution_id, q_in_bin, market_in_bin_prob, settled_in_bin,
            direction, decision_posterior_computed_at, target_date,
            settled_at, graded_at
        ) VALUES (
            :attribution_id, :q_in_bin, :market_in_bin_prob, :settled_in_bin,
            :direction, :decision_posterior_computed_at, :target_date,
            :settled_at, :graded_at
        )
        """,
        rows,
    )
    conn.commit()
    return conn


def _row(index: int, *, settled_at: datetime, lead_days: int = 1) -> dict:
    decision_day = date(2026, 8, 1) + timedelta(days=index % 5)
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
    }


def _settled_rows(count: int, *, settled_at: datetime | None = None) -> list[dict]:
    when = settled_at or (NOW - timedelta(days=3))
    return [_row(i, settled_at=when) for i in range(count)]


def test_fit_returns_none_below_min_train_rows():
    conn = _memory_db(_settled_rows(5))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20)

    assert provider.artifact(now=NOW) is None


def test_fit_produces_artifact_at_min_train_rows():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20)

    artifact = provider.artifact(now=NOW)

    assert artifact is not None
    assert artifact.n_train == 40
    assert set(artifact.alpha) == {"day0", "day1", "day2"}


def test_unreachable_database_fails_open_to_none():
    def explode():
        raise sqlite3.OperationalError("unable to open database file")

    provider = MarketAnchoredFitProvider(explode, min_train_rows=1)

    assert provider.artifact(now=NOW) is None


def test_rows_settling_after_the_cutoff_never_train():
    """The walk-forward law: an outcome that had not resolved cannot inform."""

    conn = _memory_db(_settled_rows(40, settled_at=NOW + timedelta(days=1)))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=1)

    assert provider.artifact(now=NOW) is None

    rows = load_fit_rows(conn, training_cutoff=NOW + timedelta(days=2))
    assert len(rows) == 40


def test_training_cutoff_is_the_fit_instant():
    conn = _memory_db(_settled_rows(40))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20)

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
        connect, min_train_rows=20, ttl=timedelta(hours=6)
    )

    first = provider.artifact(now=NOW)
    cached = provider.artifact(now=NOW + timedelta(hours=5, minutes=59))
    assert len(fits) == 1
    assert cached is first

    refit = provider.artifact(now=NOW + timedelta(hours=6, minutes=1))
    assert len(fits) == 2
    assert refit is not None
    assert refit.training_cutoff != first.training_cutoff


def test_failed_fit_is_cached_so_a_dead_db_is_not_redialled_per_candidate():
    attempts: list[int] = []

    def explode():
        attempts.append(1)
        raise sqlite3.OperationalError("database is locked")

    provider = MarketAnchoredFitProvider(explode, min_train_rows=1)

    assert provider.artifact(now=NOW) is None
    assert provider.artifact(now=NOW + timedelta(minutes=1)) is None
    assert len(attempts) == 1


def test_rows_missing_decision_time_or_target_date_are_skipped():
    rows = _settled_rows(4)
    rows[0]["decision_posterior_computed_at"] = None
    rows[1]["target_date"] = None
    conn = _memory_db(rows)

    assert len(load_fit_rows(conn, training_cutoff=NOW)) == 2


def test_unmodeled_lead_is_excluded_from_training():
    conn = _memory_db(
        [_row(i, settled_at=NOW - timedelta(days=3), lead_days=7) for i in range(6)]
    )

    assert load_fit_rows(conn, training_cutoff=NOW) == []


def test_graded_at_substitutes_for_a_missing_settled_at():
    rows = _settled_rows(2)
    for row in rows:
        row["graded_at"] = row["settled_at"]
        row["settled_at"] = None
    conn = _memory_db(rows)

    assert len(load_fit_rows(conn, training_cutoff=NOW)) == 2


def test_corrected_probability_shrinks_an_overconfident_q_toward_the_market():
    conn = _memory_db(_settled_rows(60))
    provider = MarketAnchoredFitProvider(lambda: conn, min_train_rows=20)
    artifact = provider.artifact(now=NOW)

    applied = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        decision_date=date(2026, 8, 26),
        target_date=date(2026, 8, 27),
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
            decision_date=date(2026, 8, 26),
            target_date=date(2026, 8, 27),
        )
        is None
    )


def test_corrected_probability_fails_closed_on_unmodeled_lead():
    conn = _memory_db(_settled_rows(40))
    artifact = MarketAnchoredFitProvider(
        lambda: conn, min_train_rows=20
    ).artifact(now=NOW)

    assert (
        corrected_probability(
            artifact,
            p0=0.35,
            q_raw=0.9,
            decision_date=date(2026, 8, 20),
            target_date=date(2026, 8, 27),
        )
        is None
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_corrected_probability_fails_closed_on_non_finite_inputs(bad):
    conn = _memory_db(_settled_rows(40))
    artifact = MarketAnchoredFitProvider(
        lambda: conn, min_train_rows=20
    ).artifact(now=NOW)

    assert (
        corrected_probability(
            artifact,
            p0=bad,
            q_raw=0.9,
            decision_date=date(2026, 8, 26),
            target_date=date(2026, 8, 27),
        )
        is None
    )
