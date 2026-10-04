# Created: 2026-10-04
# Last reused or audited: 2026-10-04
# Lifecycle: created=2026-10-04; last_reviewed=2026-10-04; last_reused=2026-10-04
# Purpose: A cycle-advance marker's queued request is classified from its own
#   scope fields when the queue wrote no Day0 witness (non-Day0 seeds), so a
#   committed ENS run is never held behind an owner the checker cannot name.
# Reuse: Run when the cycle-advance owner checker, superseded-baseline reseed,
#   or periodic enqueue dedup changes.
# Authority basis: live 2026-10-04 PENDING_WITNESS_INVALID loop (51/180 families
#   on 06Z/00Z q 85 min after the 12Z committed ENS wake).
"""Witnessless cycle-advance owner requests are decidable, never indeterminate."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.data.replacement_cycle_advance_trigger as cycle_advance
import src.data.replacement_forecast_production as forecast_production
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc
STATE = cycle_advance._Day0EnqueueOwnerRequestState
CITY, DATE, METRIC = "Tokyo", "2026-10-06", "high"
CYCLE = "2026-10-04T12:00:00+00:00"
OLD_RUN = "ecmwf_open_data:mx2t6_high:2026-10-04T06Z:v3"
NEW_RUN = "ecmwf_open_data:mx2t6_high:2026-10-04T12Z:v3"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = {
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "inflight",
        "seed_dir": tmp_path / "seeds",
    }
    for key in ("request_dir", "seed_dir"):
        cfg[key].mkdir()
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    return cfg


def _seed(cfg) -> Path:
    return cfg["seed_dir"] / f"{CITY}.{DATE}.{METRIC}.20261004T201812Z.enqueue-abc.json"


def _request(cfg, seed: Path, **fields) -> Path:
    payload = {
        "city": CITY,
        "target_date": DATE,
        "temperature_metric": METRIC,
        "source_cycle_time": CYCLE,
        "baseline_source_run_id": OLD_RUN,
        "upgrade_trigger": "newer_cycle_ingested",
        **fields,
    }
    path = cfg["request_dir"] / seed.name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _check(seed: Path, identity: str | None = None):
    return cycle_advance._day0_enqueue_owner_request_check(
        city=CITY, target_date=DATE, metric=METRIC, target_cycle_iso=CYCLE,
        seed_file=str(seed), identity=identity,
    )


def test_witnessless_non_day0_request_is_its_markers_active_owner(cfg) -> None:
    seed = _seed(cfg)
    _request(cfg, seed)
    check = _check(seed)
    assert check.state is STATE.ACTIVE
    assert check.baseline_source_run_id == OLD_RUN


@pytest.mark.parametrize(
    "fields",
    (
        {"source_cycle_time": "2026-10-04T06:00:00+00:00"},
        {"city": "Osaka"},
        {"temperature_metric": "low"},
        # A Day0-conditioned request cannot own a marker that recorded none.
        {
            "day0_observed_extreme_source": "wu_icao_history",
            "day0_observed_extreme_observation_time": "2026-10-04T05:00:00+00:00",
            "day0_observed_extreme_c": 21.0,
            "day0_observed_extreme_unit": "C",
        },
    ),
)
def test_witnessless_request_for_another_scope_is_other_owner(cfg, fields) -> None:
    seed = _seed(cfg)
    _request(cfg, seed, **fields)
    assert _check(seed).state is STATE.INACTIVE


def test_day0_witness_semantics_unchanged(cfg) -> None:
    seed = _seed(cfg)
    identity = cycle_advance._day0_conditioning_identity(
        source="wu_icao_history", observation_time="2026-10-04T05:00:00+00:00",
        observed_extreme_c=21.0, unit="C",
    )
    witness = {
        "city": CITY, "target_date": DATE, "metric": METRIC,
        "target_cycle_time": CYCLE, "seed_file": str(seed),
        "conditioning_identity": identity,
    }
    _request(cfg, seed, day0_enqueue_owner_witness=witness)
    assert _check(seed, identity).state is STATE.ACTIVE
    assert _check(seed, "other-identity").state is STATE.INACTIVE
    _request(cfg, seed, day0_enqueue_owner_witness="not-an-object")
    assert _check(seed, identity).state is STATE.INDETERMINATE


@pytest.mark.parametrize(
    "raw",
    ("not json", "[]", json.dumps({"city": CITY, "target_date": DATE})),
)
def test_unreadable_request_stays_indeterminate(cfg, raw) -> None:
    seed = _seed(cfg)
    (cfg["request_dir"] / seed.name).write_text(raw, encoding="utf-8")
    assert _check(seed).state is STATE.INDETERMINATE


def _committed_db(tmp_path: Path, seed: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "forecast.db")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_advance._ensure_day0_conditioning_identity_column(conn)
    conn.execute("CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_cycle_time TEXT)")
    conn.execute(
        """CREATE TABLE ensemble_snapshots (
            source_run_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
            source_id TEXT, model_version TEXT, authority TEXT, causality_status TEXT,
            boundary_ambiguous INTEGER, forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER)"""
    )
    conn.execute("INSERT INTO source_run VALUES (?, ?)", (NEW_RUN, CYCLE))
    conn.execute(
        """INSERT INTO ensemble_snapshots VALUES (?, ?, ?, ?, 'ecmwf_open_data',
           'ecmwf_ens', 'VERIFIED', 'OK', 0, 'FULLY_INSIDE_TARGET_LOCAL_DAY', 1)""",
        (NEW_RUN, CITY, DATE, METRIC),
    )
    conn.execute(
        """INSERT INTO cycle_advance_enqueues (enqueued_at, city, target_date, metric,
           consumed_cycle_time, target_cycle_time, held_position, seed_file)
           VALUES ('2026-10-04T20:18:12+00:00', ?, ?, ?, '2026-10-04T06:00:00+00:00', ?, 0, ?)""",
        (CITY, DATE, METRIC, CYCLE, str(seed)),
    )
    conn.commit()
    return conn


@pytest.mark.parametrize(
    ("queued_baseline", "expected"),
    ((OLD_RUN, "seed"), (NEW_RUN, None)),
)
def test_committed_run_reseeds_past_a_witnessless_pending_owner(
    tmp_path, cfg, queued_baseline, expected,
) -> None:
    """Live loop: the consumed seed's request has no witness; the committed run
    must replace it (older baseline) or be recognized as delivered (same run)."""
    seed = _seed(cfg)
    _request(cfg, seed, baseline_source_run_id=queued_baseline)
    conn = _committed_db(tmp_path, seed)
    result = cycle_advance._superseded_baseline_seed_file(
        conn, city=CITY, target_date=DATE, metric=METRIC, target_cycle_iso=CYCLE,
        required_baseline_source_run_id=NEW_RUN,
        decision_time=datetime(2026, 10, 4, 21, tzinfo=UTC),
    )
    assert result == (str(seed) if expected == "seed" else None)
    conn.close()


def test_committed_run_retries_only_on_unreadable_owner(tmp_path, cfg) -> None:
    seed = _seed(cfg)
    (cfg["request_dir"] / seed.name).write_text("not json", encoding="utf-8")
    conn = _committed_db(tmp_path, seed)
    with pytest.raises(cycle_advance._CycleAdvanceRetryPending, match="JSON_INVALID"):
        cycle_advance._superseded_baseline_seed_file(
            conn, city=CITY, target_date=DATE, metric=METRIC, target_cycle_iso=CYCLE,
            required_baseline_source_run_id=NEW_RUN,
            decision_time=datetime(2026, 10, 4, 21, tzinfo=UTC),
        )
    conn.close()


def test_superseded_seed_replacement_is_a_marker_cas(tmp_path, cfg) -> None:
    seed = _seed(cfg)
    conn = _committed_db(tmp_path, seed)
    common = dict(
        city=CITY, target_date=DATE, metric=METRIC,
        consumed_cycle_iso="2026-10-04T06:00:00+00:00", target_cycle_iso=CYCLE,
        held_position=False, replace_existing_seed_file=True,
    )
    assert not cycle_advance._record_enqueue(
        conn, seed_file="/q/seeds/racer.json", superseded_seed_file="/q/seeds/foreign.json",
        **common,
    )
    assert cycle_advance._record_enqueue(
        conn, seed_file="/q/seeds/new.json", superseded_seed_file=str(seed), **common,
    )
    row = conn.execute("SELECT seed_file FROM cycle_advance_enqueues").fetchone()
    assert row["seed_file"] == "/q/seeds/new.json"
    conn.close()


def test_periodic_lane_does_not_duplicate_a_queued_owner(tmp_path, cfg) -> None:
    """The consumed seed's pending request owns the marker: no fresh seed beside it."""
    seed = _seed(cfg)
    conn = _committed_db(tmp_path, seed)
    decide = lambda: cycle_advance._enqueue_decision(  # noqa: E731
        conn, city=CITY, target_date=DATE, metric=METRIC, target_cycle_iso=CYCLE,
        as_of=datetime(2026, 10, 4, 21, tzinfo=UTC),
    )
    request = _request(cfg, seed)
    assert decide() is cycle_advance._CycleAdvanceEnqueueDecision.ALREADY_ENQUEUED
    request.unlink()
    assert decide() is cycle_advance._CycleAdvanceEnqueueDecision.ADMIT
    conn.close()
