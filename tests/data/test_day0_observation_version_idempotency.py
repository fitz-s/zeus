# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md
#   (Toronto NO@24 -98.94% total loss). VERIFIED ROOT: same-day held-position belief froze
#   because the day0 re-materialization idempotency (cycle_advance_enqueues, UNIQUE on
#   target_cycle_time = the MODEL cycle) blocks intraday re-enqueue. The model does not re-run
#   intraday for the settlement day, so once a family is enqueued for the day's model cycle, the
#   observed running-max climbing 22->23->24 (and PLATEAUS at 24 = new info: remaining-heating
#   window shrank) hit _already_enqueued=True -> reseed SKIPPED -> forecast_posteriors frozen at
#   01:23 UTC -> position_belief 9h guard correctly refuses the stale belief -> exit blind.
#   Consult REQ-20260623-174044-18fe71 (Pro Extended) verified fix: key the day0/held reseed
#   idempotency by the OBSERVATION VERSION (observation_available_at / observation_time), not only
#   the model cycle, so each fresh observation version re-materializes a fresh day0-conditioned
#   posterior. This does NOT touch the model-cycle idempotency for non-day0 (future-date) reseeds.
"""Relationship antibody: day0 same-day re-materialization must refresh on each new observation
version, NOT freeze at the first model-cycle enqueue.

R-OBS-VERSION: for a held day0 (settlement-day) family, _already_enqueued must allow re-enqueue
when the supplied day0 observed-extreme observation version is NEWER than the version recorded on
the existing marker — even when the model-cycle seed file still exists. A non-day0 reseed (no
observation version supplied) keeps the existing model-cycle idempotency (a present seed for the
same target cycle suppresses re-enqueue)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import src.data.replacement_cycle_advance_trigger as cycle_advance
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

_CITY = "Toronto"
_TD = "2026-06-23"
_METRIC = "high"
_CYCLE = "2026-06-23T00:00:00+00:00"  # the day's frozen model cycle (does NOT advance intraday)
_OBS_17 = "2026-06-23T17:00:00+00:00"  # running_max=23
_OBS_19 = "2026-06-23T19:00:00+00:00"  # running_max=24 (the realized loss bin)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    return conn


def _record(conn, *, seed_file: str, obs_time: str | None) -> None:
    cycle_advance._record_enqueue(
        conn,
        city=_CITY,
        target_date=_TD,
        metric=_METRIC,
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=_CYCLE,
        held_position=True,
        seed_file=seed_file,
        day0_observed_extreme_observation_time=obs_time,
    )


def test_newer_observation_version_re_enqueues_despite_existing_model_cycle_seed(tmp_path: Path):
    """The Toronto freeze: a held day0 family already enqueued for the day's model cycle (seed
    file present) MUST re-enqueue when a newer observed running-max version arrives, so the
    same-day posterior re-materializes intraday instead of freezing at the overnight prior."""
    conn = _conn()
    seed = tmp_path / "toronto.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time=_OBS_17)

    # Newer observation version (running_max advanced 23 -> 24 at 19:00) — must NOT be suppressed.
    blocked = cycle_advance._already_enqueued(
        conn,
        city=_CITY,
        target_date=_TD,
        metric=_METRIC,
        target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time=_OBS_19,
    )
    assert blocked is False  # re-enqueue allowed: fresh observation version is new information


def test_plateau_same_extreme_new_observation_version_re_enqueues(tmp_path: Path):
    """A PLATEAU (running_max unchanged at 24 but a NEW hourly observation version) is still new
    information — the remaining-heating window shrank — so it must refresh, not freeze."""
    conn = _conn()
    seed = tmp_path / "toronto.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time=_OBS_17)
    obs_20 = "2026-06-23T20:00:00+00:00"
    blocked = cycle_advance._already_enqueued(
        conn,
        city=_CITY,
        target_date=_TD,
        metric=_METRIC,
        target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time=obs_20,
    )
    assert blocked is False


def test_same_observation_version_is_idempotent_no_churn(tmp_path: Path):
    """No firehose: the SAME observation version already recorded must NOT re-enqueue."""
    conn = _conn()
    seed = tmp_path / "toronto.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time=_OBS_19)
    blocked = cycle_advance._already_enqueued(
        conn,
        city=_CITY,
        target_date=_TD,
        metric=_METRIC,
        target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time=_OBS_19,
    )
    assert blocked is True


def test_observation_version_z_and_offset_spellings_are_instant_equal(tmp_path: Path):
    """consult REQ-20260623-184115 HIGH: '...19:00:00Z' and '...19:00:00+00:00' are the SAME
    instant — must NOT churn (raw lexicographic compare would treat 'Z' > '+00:00')."""
    conn = _conn()
    seed = tmp_path / "toronto.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time="2026-06-23T19:00:00+00:00")
    blocked = cycle_advance._already_enqueued(
        conn, city=_CITY, target_date=_TD, metric=_METRIC, target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time="2026-06-23T19:00:00Z",
    )
    assert blocked is True  # same instant, different spelling → idempotent, no re-enqueue


def test_local_offset_newer_than_recorded_utc_re_enqueues(tmp_path: Path):
    """consult REQ-20260623-184115 HIGH: '2026-06-23T17:00:00-04:00' == 21:00Z is NEWER than the
    recorded 20:00Z and MUST re-enqueue — a raw string compare would wrongly suppress it
    (lexicographically '17:...' < '20:...')."""
    conn = _conn()
    seed = tmp_path / "toronto.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time="2026-06-23T20:00:00+00:00")
    blocked = cycle_advance._already_enqueued(
        conn, city=_CITY, target_date=_TD, metric=_METRIC, target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time="2026-06-23T17:00:00-04:00",
    )
    assert blocked is False  # 17:00-04:00 == 21:00Z > 20:00Z → re-enqueue


def test_record_enqueue_is_monotone_no_stale_out_of_order_regression(tmp_path: Path):
    """consult REQ-20260623-184115 HIGH: an out-of-order OLDER observation-version writer must NOT
    regress the marker version or seed-file pointer after a newer writer won."""
    conn = _conn()
    seed_new = tmp_path / "new.seed.json"
    seed_new.write_text("{}")
    seed_old = tmp_path / "old.seed.json"
    seed_old.write_text("{}")
    _record(conn, seed_file=str(seed_new), obs_time="2026-06-23T21:00:00+00:00")
    _record(conn, seed_file=str(seed_old), obs_time="2026-06-23T20:00:00+00:00")  # stale, out of order
    row = conn.execute(
        "SELECT day0_observed_extreme_observation_time AS v, seed_file AS s "
        "FROM cycle_advance_enqueues WHERE city=? AND target_date=? AND metric=? "
        "AND target_cycle_time=?",
        (_CITY, _TD, _METRIC, _CYCLE),
    ).fetchone()
    assert row["v"] == "2026-06-23T21:00:00+00:00"  # version not regressed
    assert row["s"] == str(seed_new)  # seed pointer not regressed to the stale 20:00 seed


def test_non_day0_reseed_preserves_model_cycle_idempotency(tmp_path: Path):
    """A non-day0 (future-date) reseed supplies NO observation version: the existing model-cycle
    idempotency is unchanged — a present seed for the same target cycle still suppresses."""
    conn = _conn()
    seed = tmp_path / "future.seed.json"
    seed.write_text("{}")
    _record(conn, seed_file=str(seed), obs_time=None)
    blocked = cycle_advance._already_enqueued(
        conn,
        city=_CITY,
        target_date=_TD,
        metric=_METRIC,
        target_cycle_iso=_CYCLE,
        day0_observed_extreme_observation_time=None,
    )
    assert blocked is True
