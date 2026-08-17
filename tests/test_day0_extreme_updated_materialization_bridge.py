# Created: 2026-07-19
# Last reused/audited: 2026-07-30
# Authority basis: operator directive 2026-07-19 (Day0 is a zero-sum race against the market
#   book) + docs/evidence/upstream_physical_2026_07_17/day0_latency_chain_measurement.md (the
#   measured bottleneck is the ~40-min SCHEDULED posterior recompute cadence, HOP 2b p50 39.9 min
#   / p90 90 min — fetch and event delivery are already fast). Sibling of
#   src.data.replacement_cycle_advance_trigger's single-family cycle-advance reseed (Task #32
#   family) — this is the SAME seed transport, bridged from event EMISSION instead of from a
#   reactive stale-posterior processing failure.
"""Event-driven Day0 recompute bridge tests.

``enqueue_day0_extreme_updated_materialization_seed`` (src/data/replacement_cycle_advance_trigger.py)
is called right after a DAY0_EXTREME_UPDATED event commits (ingest_main.py's fast METAR source
clock, and reactor.py's catch-up scan lane). It must:

  (a) force exactly ONE live materialization seed for the family per fresh observation, reusing
      the EXISTING single-family cycle-advance seed transport verbatim (same seed builder, same
      seed_dir, same ``cycle_advance_enqueues`` idempotency marker);
  (b) dedup a repeat call carrying the SAME observation_time via the existing monotone guard
      already proven in test_cycle_monotone_materialization.py (no new seed, no row churn), but
      advance on a STRICTLY NEWER observation_time even with no model-cycle change (the same-day
      exit-blindness fix, REQ-20260623-184115);
  (c) be fail-soft end to end — a missing config, no canonical observed extreme, or any internal
      fault returns a status dict and never raises into the event-emission path.
"""
from __future__ import annotations

import json
import importlib
import multiprocessing
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import src.data.replacement_cycle_advance_trigger as cycle_advance
import src.data.replacement_forecast_live_materialization_queue as materialization_queue
import src.data.replacement_forecast_production as forecast_production
import src.data.replacement_forecast_seed_discovery as seed_discovery
from src.data.replacement_forecast_materializer import (
    expected_replacement_dependency_identity_by_role,
)
import src.state.db as state_db
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc


def test_canonical_manifest_read_excludes_future_available_artifact() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY,
            source_id TEXT,
            product_id TEXT,
            data_version TEXT,
            artifact_path TEXT,
            sha256 TEXT,
            byte_size INTEGER,
            source_cycle_time TEXT,
            source_available_at TEXT,
            captured_at TEXT,
            request_url TEXT,
            request_params_json TEXT,
            artifact_metadata_json TEXT,
            training_allowed INTEGER
        )
        """
    )
    identity = expected_replacement_dependency_identity_by_role("high")[
        "openmeteo_ifs9_anchor"
    ]
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts
            (source_id, product_id, data_version, artifact_path, sha256,
             byte_size, source_cycle_time, source_available_at, captured_at,
             request_url, request_params_json, artifact_metadata_json,
             training_allowed)
        VALUES (?, ?, ?, '/tmp/future-anchor.json', ?, 1, ?, ?, ?,
                'https://example.invalid/anchor', '{"request":true}',
                '{"city":"Shanghai","target_date":"2026-07-19"}', 0)
        """,
        (
            identity.source_id,
            identity.product_id,
            identity.data_version,
            "0" * 64,
            "2026-07-19T00:00:00+00:00",
            "2026-07-19T06:59:59.900000+00:00",
            "2026-07-19T06:59:59.900000+00:00",
        ),
    )

    assert cycle_advance._family_manifests_from_db(
        conn,
        city="Shanghai",
        identity=identity,
        computed_at=datetime(2026, 7, 19, 6, 59, 59, 500000, tzinfo=UTC),
    ) == ()
    available = cycle_advance._family_manifests_from_db(
        conn,
        city="Shanghai",
        identity=identity,
        computed_at=datetime(2026, 7, 19, 6, 59, 59, 900000, tzinfo=UTC),
    )
    assert len(available) == 1
    assert available[0].product_metadata["artifact_id"] == 1
    conn.close()


def _queue_config(tmp_path: Path) -> dict[str, object]:
    return {
        "forecast_db": tmp_path / "forecasts.db",
        "seed_dir": tmp_path / "seeds",
        "seed_processed_dir": tmp_path / "seed_processed",
        "seed_failed_dir": tmp_path / "seed_failed",
        "raw_manifest_dir": tmp_path / "raw",
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "inflight",
        "processed_dir": tmp_path / "processed",
        "failed_dir": tmp_path / "failed",
    }


def _day0_payload(observation_time: str) -> dict[str, object]:
    return {
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": observation_time,
        "day0_observed_extreme_sample_count": 4,
        "day0_observed_extreme_unit": "C",
    }


def _fake_build_seed_factory():
    """Stand in for the real seed builder (network/manifest-independent for this bridge unit
    test — the seed-content shape itself is covered by test_cycle_monotone_materialization.py)."""
    calls = {"count": 0}

    def _fake_build_seed(_conn_arg, **kwargs):
        calls["count"] += 1
        path = Path(
            kwargs.get("output_path")
            or Path(kwargs["seed_path"]) / f"Shanghai.seed.{calls['count']}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "day0_observed_extreme_observation_time": kwargs.get(
                        "day0_observed_extreme_observation_time"
                    ),
                }
            ),
            encoding="utf-8",
        )
        return path

    return _fake_build_seed, calls


def _prepare_forecast_db(tmp_path: Path) -> Path:
    """A schema-only forecast DB plus one anchor-leg raw artifact so
    freshest_materializable_cycle has a high-water mark to report."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_iso = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    meaningful = {
        "source_id": cycle_advance._ANCHOR_LEG_SOURCE_ID,
        "source_cycle_time": cycle_iso,
    }
    values: dict[str, object] = {}
    for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)"):
        name, notnull, pk = row[1], row[3], row[5]
        if pk:
            continue
        if name in meaningful:
            values[name] = meaningful[name]
        elif notnull:
            if name.endswith("_json"):
                values[name] = "{}"
            elif name in ("byte_size", "training_allowed"):
                values[name] = 0
            elif name == "runtime_layer":
                values[name] = "live"
            elif name.endswith("_at") or name.endswith("_time"):
                values[name] = cycle_iso
            else:
                values[name] = "x"
    names = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO raw_forecast_artifacts ({names}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    conn.commit()
    conn.close()
    return db_path


def _multiprocess_day0_ingest_owner(
    cfg: dict[str, object],
    reports,
    release,
    materializer_called,
) -> None:
    """Publish one exact Day0 seed from a process with no materialization authority."""

    forecast_production._replacement_forecast_live_materialization_queue_config = (
        lambda: cfg
    )
    seed_discovery._day0_observed_extreme_seed_payload = (
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00")
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    cycle_advance.family_materializable_cycle = (
        lambda *args, **kwargs: (cycle, ())
    )

    def _write_seed(_conn_arg, **kwargs):
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "day0_observed_extreme_observation_time": kwargs.get(
                        "day0_observed_extreme_observation_time"
                    ),
                }
            ),
            encoding="utf-8",
        )
        return path

    cycle_advance._build_and_write_advance_seed = _write_seed
    materialization_queue._run_materialization_item = (
        lambda _item: materializer_called.set()
        or subprocess.CompletedProcess([], 0, stdout="", stderr="")
    )
    reports.put(
        cycle_advance._materialize_day0_extreme_updated_seed(
            city="Shanghai",
            target_date="2026-07-19",
            metric="high",
            computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
            held_position=False,
        )
    )
    release.wait(5.0)


def _multiprocess_forecast_materialization_owner(
    seed_file: str,
    dummy_files: tuple[str, ...],
    running,
    peak_running,
    four_started,
    exact_retry_started,
    release,
) -> None:
    """Drain only inside the forecast-live owner under its existing four-worker cap."""

    exact_path = Path(seed_file)

    def _run_owned_item(item):
        with running.get_lock():
            running.value += 1
            peak_running.value = max(peak_running.value, running.value)
            if running.value == materialization_queue.DEFAULT_MATERIALIZATION_MAX_WORKERS:
                four_started.set()
        try:
            if item.input_json == exact_path:
                exact_retry_started.set()
                item.input_json.unlink()
            else:
                assert release.wait(5.0)
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        finally:
            with running.get_lock():
                running.value -= 1

    materialization_queue._run_materialization_item = _run_owned_item
    pending = [
        materialization_queue._PendingMaterialization(
            input_json=Path(path),
            command=(),
            request_payload=None,
            marker_path=None,
            attempt_fingerprint=None,
        )
        for path in (*dummy_files, seed_file)
    ]
    materialization_queue._run_materialization_batch(pending)


def _fetch_enqueue_row(db_path: Path) -> sqlite3.Row:
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        "SELECT day0_observed_extreme_observation_time, day0_conditioning_identity_json, seed_file "
        "FROM cycle_advance_enqueues WHERE city='Shanghai' AND target_date='2026-07-19' "
        "AND metric='high'"
    ).fetchone()
    check.close()
    return row


def _record_missing_day0_owner(
    db_path: Path,
    cfg: Mapping[str, object],
    *,
    seed_name: str,
) -> tuple[sqlite3.Connection, dict[str, object], Path, str, str]:
    payload = _day0_payload("2026-07-19T05:00:00+00:00")
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    seed_file = Path(cfg["seed_dir"]) / seed_name
    identity = cycle_advance._day0_conditioning_identity(
        source=payload["day0_observed_extreme_source"],
        observation_time=payload["day0_observed_extreme_observation_time"],
        observed_extreme_c=payload["day0_observed_extreme_c"],
        unit=payload["day0_observed_extreme_unit"],
    )
    assert identity is not None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(seed_file),
        reason="MISSING_LIVE_POSTERIOR",
        day0_observed_extreme_observation_time=payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payload["day0_observed_extreme_unit"],
    )
    conn.commit()
    return conn, payload, seed_file, identity, cycle


def _missing_day0_owner_is_retained(
    conn: sqlite3.Connection,
    *,
    payload: Mapping[str, object],
    cycle: str,
) -> bool:
    return cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 6, tzinfo=UTC),
        day0_observed_extreme_observation_time=str(
            payload["day0_observed_extreme_observation_time"]
        ),
        day0_observed_extreme_source=str(payload["day0_observed_extreme_source"]),
        day0_observed_extreme_c=float(payload["day0_observed_extreme_c"]),
        day0_observed_extreme_unit=str(payload["day0_observed_extreme_unit"]),
    )


def _insert_live_posterior(
    db_path: Path,
    *,
    cycle_iso: str,
    computed_at: str,
    source_id: str = cycle_advance.SOURCE_ID,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date,
             temperature_metric, source_cycle_time, source_available_at,
             computed_at, q_json, q_lcb_json, posterior_method,
             dependency_source_run_ids_json, provenance_json, runtime_layer,
             training_allowed)
        VALUES (?, 'pid', 'dv', 'Shanghai', '2026-07-19', 'high', ?, ?, ?,
                '{}', '{}', 'm', '{}', '{}', 'live', 0)
        """,
        (source_id, cycle_iso, cycle_iso, computed_at),
    )
    conn.commit()
    conn.close()


def _insert_materialized_day0_posterior(
    db_path: Path,
    *,
    cycle_iso: str,
    computed_at: str,
    payload: Mapping[str, object],
) -> None:
    """Model the queue/materializer's committed provenance for one drained seed."""
    _insert_live_posterior(
        db_path,
        cycle_iso=cycle_iso,
        computed_at=computed_at,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET provenance_json = ?
         WHERE posterior_id = (
             SELECT MAX(posterior_id) FROM forecast_posteriors
         )
        """,
        (
            json.dumps(
                {
                    "openmeteo_anchor_artifact_id": 1,
                    "day0_conditioning": {
                        "source": payload["day0_observed_extreme_source"],
                        "observation_time": payload[
                            "day0_observed_extreme_observation_time"
                        ],
                        "observed_extreme_c": payload["day0_observed_extreme_c"],
                        "unit": payload["day0_observed_extreme_unit"],
                    }
                }
            ),
        ),
    )
    conn.commit()
    conn.close()


def test_day0_extreme_bridge_enqueues_exactly_one_seed_and_dedups_same_observation_time(
    tmp_path, monkeypatch
) -> None:
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    observation_time = "2026-07-19T05:00:00+00:00"
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload(observation_time),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    report_1 = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        held_position=False,
    )
    assert report_1["status"] == "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
    assert report_1["enqueued"] is True
    assert calls["count"] == 1, "exactly one seed built for the fresh observation"

    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["day0_observed_extreme_observation_time"] == observation_time
    first_seed_file = row["seed_file"]

    # REPEAT call carrying the SAME observation_time must dedup: no new seed built, the
    # existing cycle_advance_enqueues row (and its seed file) is left untouched.
    report_2 = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        held_position=False,
    )
    assert report_2["status"] == "CYCLE_ADVANCE_ALREADY_ENQUEUED"
    assert calls["count"] == 1, "repeat with the same observation_time must not build a second seed"

    row_after = _fetch_enqueue_row(cfg["forecast_db"])
    assert row_after["seed_file"] == first_seed_file


def test_day0_ingest_process_only_publishes_seed_for_bounded_forecast_owner(
    tmp_path,
) -> None:
    """Two live owners still expose only forecast-live's four materialization workers."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ingest_release = ctx.Event()
    owner_release = ctx.Event()
    four_started = ctx.Event()
    exact_retry_started = ctx.Event()
    ingest_materializer_called = ctx.Event()
    reports = ctx.Queue()
    running = ctx.Value("i", 0)
    peak_running = ctx.Value("i", 0)

    ingest_owner = ctx.Process(
        target=_multiprocess_day0_ingest_owner,
        args=(cfg, reports, ingest_release, ingest_materializer_called),
        name="day0-ingest-owner",
    )
    ingest_owner.start()
    report = reports.get(timeout=5.0)
    seed_file = Path(report["seed_file"])
    assert report["enqueued"] is True
    assert seed_file.is_file()
    assert not ingest_materializer_called.is_set()
    assert not hasattr(
        materialization_queue,
        "enqueue_day0_exact_seed_fast_drain",
    )

    dummy_files = tuple(
        str(tmp_path / f"normal-{index}.json")
        for index in range(materialization_queue.DEFAULT_MATERIALIZATION_MAX_WORKERS)
    )
    forecast_owner = ctx.Process(
        target=_multiprocess_forecast_materialization_owner,
        args=(
            str(seed_file),
            dummy_files,
            running,
            peak_running,
            four_started,
            exact_retry_started,
            owner_release,
        ),
        name="forecast-live-materialization-owner",
    )
    forecast_owner.start()
    assert four_started.wait(5.0)
    assert ingest_owner.is_alive()
    assert forecast_owner.is_alive()
    assert peak_running.value <= materialization_queue.DEFAULT_MATERIALIZATION_MAX_WORKERS
    assert not exact_retry_started.is_set()
    assert seed_file.is_file(), "saturated owner must leave the exact seed retryable"

    owner_release.set()
    assert exact_retry_started.wait(5.0)
    forecast_owner.join(5.0)
    assert forecast_owner.exitcode == 0
    assert not seed_file.exists()
    assert peak_running.value <= materialization_queue.DEFAULT_MATERIALIZATION_MAX_WORKERS

    ingest_release.set()
    ingest_owner.join(5.0)
    assert ingest_owner.exitcode == 0


def test_day0_extreme_bridge_advances_on_strictly_newer_observation_time(
    tmp_path, monkeypatch
) -> None:
    """A newer observed extreme re-seeds after the exact prior owner drains."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    report_1 = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        held_position=False,
    )
    assert report_1["enqueued"] is True
    Path(_fetch_enqueue_row(cfg["forecast_db"])["seed_file"]).unlink()

    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T06:00:00+00:00"),
    )
    report_2 = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 6, 1, tzinfo=UTC),
        held_position=False,
    )
    assert calls["count"] == 2, "a strictly newer observation_time must force a fresh seed"
    assert report_2["enqueued"] is True

    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["day0_observed_extreme_observation_time"] == "2026-07-19T06:00:00+00:00"


def test_day0_extreme_bridge_reseeds_for_every_conditioning_identity_change(
    tmp_path, monkeypatch
) -> None:
    """Every changed condition re-seeds after the preceding exact owner drains."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(
        cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ())
    )
    payload = _day0_payload("2026-07-19T05:00:00.132000+00:00")
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: dict(payload),
    )
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    variants = (
        {"day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00"},
        {"day0_observed_extreme_source": "wu_api+same_station_fast_tail"},
        {"day0_observed_extreme_c": 21.25},
        {"day0_observed_extreme_unit": "F"},
    )
    for offset, changed in enumerate(({}, *variants), start=1):
        payload.update(changed)
        report = cycle_advance._materialize_day0_extreme_updated_seed(
            city="Shanghai",
            target_date="2026-07-19",
            metric="high",
            computed_at=datetime(2026, 7, 19, 5, offset, tzinfo=UTC),
            held_position=True,
        )
        assert report["enqueued"] is True
        Path(_fetch_enqueue_row(cfg["forecast_db"])["seed_file"]).unlink()

    assert calls["count"] == 5
    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["day0_observed_extreme_observation_time"] == (
        "2026-07-19T05:00:00.900000+00:00"
    )
    assert json.loads(row["day0_conditioning_identity_json"]) == {
        "observation_time": "2026-07-19T05:00:00.900000+00:00",
        "observed_extreme_c": 21.25,
        "source": "wu_api+same_station_fast_tail",
        "unit": "F",
    }
    assert not Path(row["seed_file"]).exists()


def test_day0_bridge_publishes_only_the_monotonic_cas_owner(tmp_path, monkeypatch) -> None:
    """A late older bridge call cannot leave a queue-visible seed behind."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(
        cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ())
    )
    payload = _day0_payload("2026-07-19T05:00:00.900000+00:00")
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: dict(payload),
    )
    fake_build_seed, _calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    newer = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        held_position=True,
    )
    newer_seed = Path(str(newer["seed_file"]))
    assert newer["enqueued"] is True
    assert newer_seed.is_file()

    payload.update(
        {
            "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
            "day0_observed_extreme_source": "late_alternate_source",
            "day0_observed_extreme_c": 20.5,
            "day0_observed_extreme_unit": "F",
        }
    )
    older = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        held_position=True,
    )
    assert older["enqueued"] is False
    assert newer_seed.is_file()
    assert tuple((Path(cfg["seed_dir"])).glob("*.json")) == (newer_seed,)
    assert not tuple((Path(cfg["seed_dir"]) / ".cycle-advance-staging").glob("*.json"))
    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["seed_file"] == str(newer_seed)
    assert row["day0_observed_extreme_observation_time"] == (
        "2026-07-19T05:00:00.900000+00:00"
    )


def test_cycle_advance_loser_never_deletes_the_winner_seed(tmp_path) -> None:
    """Same-identity contention cleans only the loser's UUID-private staging path."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    winner_stage, winner_seed = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "winner.json",
    )
    winner_stage.parent.mkdir(parents=True)
    winner_stage.write_text("winner", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(winner_seed),
        **identity,
    ) is True
    conn.commit()
    assert cycle_advance._publish_staged_cycle_advance_seed_if_owned(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        staged_seed_file=winner_stage,
        visible_seed_file=winner_seed,
        identity=cycle_advance._day0_conditioning_identity(
            source=identity["day0_observed_extreme_source"],
            observation_time=identity["day0_observed_extreme_observation_time"],
            observed_extreme_c=identity["day0_observed_extreme_c"],
            unit=identity["day0_observed_extreme_unit"],
        ),
    ) is True

    loser_stage, loser_seed = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "winner.json",
    )
    loser_stage.write_text("loser", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(loser_seed),
        **identity,
    ) is False
    cycle_advance._discard_unpublished_cycle_advance_stage(loser_stage)
    conn.close()
    assert winner_seed.read_text(encoding="utf-8") == "winner"
    assert not loser_stage.exists()
    assert not loser_seed.exists()


def test_cycle_advance_recovers_committed_staging_after_publish_crash(tmp_path) -> None:
    """A committed owner can atomically publish its hidden seed on the next bridge check."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    staged, visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "recovery.json",
    )
    staged.parent.mkdir(parents=True)
    staged.write_text("recover", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(visible),
        **identity,
    ) is True
    conn.commit()

    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        **identity,
    ) is True
    conn.close()
    assert visible.read_text(encoding="utf-8") == "recover"
    assert not staged.exists()


def test_cycle_advance_recovers_non_day0_committed_staging_after_publish_crash(tmp_path) -> None:
    """The marker-owned non-Day0 stage is published before generic dedup suppresses it."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    staged, visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "non-day0-recovery.json",
    )
    staged.parent.mkdir(parents=True)
    staged.write_text("recover-non-day0", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(visible),
    ) is True
    conn.commit()

    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
    ) is True
    conn.close()
    assert visible.read_text(encoding="utf-8") == "recover-non-day0"
    assert not staged.exists()
    assert tuple(seed_dir.glob("*.json")) == (visible,)


def test_cycle_advance_reclaims_missing_non_day0_owned_stage_without_posterior(
    tmp_path, monkeypatch
) -> None:
    """A build that produced no artifact releases its exact marker before another writer runs."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    _staged, missing_visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "non-day0-missing.json",
    )
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(missing_visible),
    ) is True
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (datetime(2026, 7, 19, 0, tzinfo=UTC), ()),
    )
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", lambda *_args, **_kwargs: None)
    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=tmp_path / "raw",
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
    )
    assert report["status"] == "CYCLE_ADVANCE_MANIFEST_MISSING"

    # The builder returned None after reclaim. The delete must have committed, rather than leave a
    # null seed marker that blocks both this retry and unrelated writers.
    check = sqlite3.connect(db_path)
    row = check.execute(
        "SELECT seed_file FROM cycle_advance_enqueues "
        "WHERE city='Shanghai' AND target_date='2026-07-19' AND metric='high'"
    ).fetchone()
    check.close()
    assert row is None

    other = sqlite3.connect(db_path, timeout=0.1)
    assert cycle_advance._record_enqueue(
        other,
        city="Austin",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(seed_dir / "other-scope.json"),
    ) is True
    other.commit()
    other.close()

    def _build_seed(_conn_arg, **kwargs):
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return path

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _build_seed)
    retry = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=tmp_path / "raw",
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 3, tzinfo=UTC),
    )
    assert retry["enqueued"] is True
    assert Path(str(retry["seed_file"])).is_file()


def test_cycle_advance_keeps_missing_non_day0_owned_stage_when_posterior_covers(tmp_path) -> None:
    """A missing owned seed is terminal only after a posterior consumed its cycle."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    _staged, missing_visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "non-day0-covered.json",
    )
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(missing_visible),
    ) is True
    conn.commit()
    conn.close()
    _insert_live_posterior(
        db_path,
        cycle_iso=cycle,
        computed_at="2026-07-19T05:02:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 3, tzinfo=UTC),
    ) is True
    row = conn.execute(
        "SELECT seed_file FROM cycle_advance_enqueues "
        "WHERE city='Shanghai' AND target_date='2026-07-19' AND metric='high'"
    ).fetchone()
    conn.close()
    assert row["seed_file"] == str(missing_visible)


def test_day0_missing_seed_requires_matching_identity_and_target_cycle_coverage(tmp_path) -> None:
    """C1 posterior with the same Day0 identity must reclaim then republish C2."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    c1 = "2026-07-19T00:00:00+00:00"
    c2 = "2026-07-19T06:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    _stage, missing_visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "day0-c2.json",
    )
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=c1,
        target_cycle_iso=c2,
        held_position=True,
        seed_file=str(missing_visible),
        **identity,
    ) is True
    conn.commit()
    conn.close()
    _insert_live_posterior(
        db_path,
        cycle_iso=c1,
        computed_at="2026-07-19T05:01:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "day0_conditioning": {
                        "source": identity["day0_observed_extreme_source"],
                        "observation_time": identity[
                            "day0_observed_extreme_observation_time"
                        ],
                        "observed_extreme_c": identity["day0_observed_extreme_c"],
                        "unit": identity["day0_observed_extreme_unit"],
                    }
                }
            ),
        ),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=c2,
        as_of=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        **identity,
    ) is False
    staged, visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "day0-c2-retry.json",
    )
    staged.parent.mkdir(parents=True)
    staged.write_text("retry", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=c1,
        target_cycle_iso=c2,
        held_position=True,
        seed_file=str(visible),
        reason="DAY0_OBSERVATION_ADVANCED",
        replace_existing_seed_file=True,
        **identity,
    ) is True
    conn.commit()
    assert cycle_advance._publish_staged_cycle_advance_seed_if_owned(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=c2,
        staged_seed_file=staged,
        visible_seed_file=visible,
        identity=cycle_advance._day0_conditioning_identity(
            source=identity["day0_observed_extreme_source"],
            observation_time=identity["day0_observed_extreme_observation_time"],
            observed_extreme_c=identity["day0_observed_extreme_c"],
            unit=identity["day0_observed_extreme_unit"],
        ),
    ) is True
    marker = conn.execute(
        "SELECT target_cycle_time, seed_file FROM cycle_advance_enqueues "
        "WHERE city='Shanghai' AND target_date='2026-07-19' AND metric='high'"
    ).fetchone()
    assert marker["target_cycle_time"] == c2
    assert marker["seed_file"] == str(visible)
    conn.close()
    assert visible.read_text(encoding="utf-8") == "retry"


def test_day0_missing_seed_rejects_same_identity_other_source_posterior(tmp_path) -> None:
    """A same-cycle live posterior from another source cannot complete a replacement marker."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T06:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    _stage, missing_visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "day0-other-source.json",
    )
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(missing_visible),
        **identity,
    ) is True
    conn.commit()
    conn.close()
    _insert_live_posterior(
        db_path,
        source_id="other_live_source",
        cycle_iso=cycle,
        computed_at="2026-07-19T05:01:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "day0_conditioning": {
                        "source": identity["day0_observed_extreme_source"],
                        "observation_time": identity[
                            "day0_observed_extreme_observation_time"
                        ],
                        "observed_extreme_c": identity["day0_observed_extreme_c"],
                        "unit": identity["day0_observed_extreme_unit"],
                    }
                }
            ),
        ),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        **identity,
    ) is False
    conn.close()


def test_non_day0_missing_seed_rejects_future_posterior_as_of(tmp_path) -> None:
    """A posterior computed after the enqueue decision cannot suppress reclaim."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seed_dir = tmp_path / "seeds"
    cycle = "2026-07-19T00:00:00+00:00"
    _stage, missing_visible = cycle_advance._staged_cycle_advance_seed_paths(
        seed_path=seed_dir,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        seed_name=lambda *_args, **_kwargs: "future-covered.json",
    )
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(missing_visible),
    ) is True
    conn.commit()
    conn.close()
    _insert_live_posterior(
        db_path,
        cycle_iso=cycle,
        computed_at="2026-07-19T06:00:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 0, tzinfo=UTC),
    ) is False
    assert conn.execute(
        "SELECT 1 FROM cycle_advance_enqueues "
        "WHERE city='Shanghai' AND target_date='2026-07-19' AND metric='high'"
    ).fetchone() is None
    conn.close()


def test_queue_quarantines_preexisting_stale_day0_upgrade_seed(tmp_path, monkeypatch) -> None:
    """Forward cleanup: an old root JSON cannot bypass coverage after marker correction."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    cycle = "2026-07-19T00:00:00+00:00"
    winner = seed_dir / "winner.json"
    winner.write_text("{}", encoding="utf-8")
    newer_identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(winner),
        **newer_identity,
    ) is True
    conn.commit()
    conn.close()

    stale = seed_dir / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "computed_at": "2026-07-19T05:02:00+00:00",
                "source_cycle_time": cycle,
                "baseline_source_run_id": "baseline:0",
                "openmeteo_source_run_id": "openmeteo:0",
                "openmeteo_payload_json": "payload.json",
                "precision_metadata_json": "precision.json",
                "bins": [{"bin_id": "warm"}],
                "upgrade_trigger": "day0_observation_advanced",
                "day0_observed_extreme_source": "late_alternate_source",
                "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
                "day0_observed_extreme_c": 20.5,
                "day0_observed_extreme_unit": "F",
            }
        ),
        encoding="utf-8",
    )

    def unexpected_builder(*_args, **_kwargs):
        raise AssertionError("stale Day0 seed reached request construction")

    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        unexpected_builder,
    )
    processed, failed, _reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=10,
    )
    assert not failed
    assert len(processed) == 1
    assert not tuple((tmp_path / "requests").glob("*.json"))
    receipt = next((tmp_path / "seed_processed").glob("*.receipt.json"))
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == (
        "SKIPPED_STALE_DAY0_ENQUEUE_OWNER"
    )


def test_current_day0_owner_uses_latest_enqueue_not_consumed_source_cycle(
    tmp_path,
) -> None:
    """A newer target-cycle marker may intentionally reuse the consumed source cycle."""
    db_path = _prepare_forecast_db(tmp_path)
    old_seed = tmp_path / "old.json"
    new_seed = tmp_path / "new.json"
    old_seed.write_text("{}", encoding="utf-8")
    new_seed.write_text("{}", encoding="utf-8")
    consumed_cycle = "2026-07-19T00:00:00+00:00"
    new_identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:05:00+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=consumed_cycle,
        target_cycle_iso=consumed_cycle,
        held_position=True,
        seed_file=str(old_seed),
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time="2026-07-19T05:00:00+00:00",
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_unit="C",
    ) is True
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=consumed_cycle,
        target_cycle_iso="2026-07-19T12:00:00+00:00",
        held_position=True,
        seed_file=str(new_seed),
        reason="DAY0_OBSERVATION_ADVANCED",
        **new_identity,
    ) is True
    conn.commit()
    conn.close()

    ownership = materialization_queue._upgrade_day0_seed_has_current_enqueue_ownership(
        forecast_db=db_path,
        seed_file=new_seed,
        seed={
            "city": "Shanghai",
            "target_date": "2026-07-19",
            "temperature_metric": "high",
            "source_cycle_time": consumed_cycle,
            "upgrade_trigger": "day0_observation_advanced",
            **new_identity,
        },
    )

    assert ownership.ownership is materialization_queue._Day0EnqueueOwnership.CURRENT
    assert ownership.witness is not None
    assert ownership.witness["target_cycle_time"] == "2026-07-19T12:00:00+00:00"


def test_queue_defers_current_day0_upgrade_seed_when_marker_read_is_transient(
    tmp_path, monkeypatch
) -> None:
    """A marker-read outage defers a current seed; the next healthy pass drains it."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    seed = seed_dir / "current.json"
    seed.write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "computed_at": "2026-07-19T05:02:00+00:00",
                "source_cycle_time": cycle,
                "baseline_source_run_id": "baseline:0",
                "openmeteo_source_run_id": "openmeteo:0",
                "openmeteo_payload_json": "payload.json",
                "precision_metadata_json": "precision.json",
                "bins": [{"bin_id": "warm"}],
                "upgrade_trigger": "day0_observation_advanced",
                **identity,
            }
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(seed),
        **identity,
    ) is True
    conn.commit()
    conn.close()

    def unexpected_builder(*_args, **_kwargs):
        raise AssertionError("indeterminate marker read reached request construction")

    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        unexpected_builder,
    )
    original_connect = state_db._connect
    monkeypatch.setattr(
        state_db,
        "_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )
    processed, failed, reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=10,
    )
    assert not processed
    assert not failed
    assert seed.is_file()
    assert not tuple((tmp_path / "seed_processed").glob("*.json"))
    assert not tuple((tmp_path / "seed_failed").glob("*.json"))
    assert not tuple((tmp_path / "requests").glob("*.json"))
    assert reasons == ["REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE"]

    monkeypatch.setattr(state_db, "_connect", original_connect)
    built: list[Mapping[str, object]] = []

    def ready_builder(payload, **_kwargs):
        built.append(payload)
        return SimpleNamespace(
            ok=True,
            status="READY",
            reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
            request={
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "source_cycle_time": cycle,
            },
        )

    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        ready_builder,
    )
    processed, failed, _reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=10,
    )
    assert len(processed) == 1
    assert not failed
    assert len(built) == 1
    assert not seed.exists()
    request_file = next((tmp_path / "requests").glob("*.json"))
    request_payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert request_payload["day0_enqueue_owner_witness"] == {
        "city": "Shanghai",
        "target_date": "2026-07-19",
        "metric": "high",
        "target_cycle_time": cycle,
        "seed_file": str(seed),
        "conditioning_identity": cycle_advance._day0_conditioning_identity(
            source=identity["day0_observed_extreme_source"],
            observation_time=identity["day0_observed_extreme_observation_time"],
            observed_extreme_c=identity["day0_observed_extreme_c"],
            unit=identity["day0_observed_extreme_unit"],
        ),
    }


def test_queue_revalidates_day0_owner_immediately_before_request_publish(
    tmp_path, monkeypatch
) -> None:
    """A marker swap after the first check cannot publish the old owner's request."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    cycle = "2026-07-19T00:00:00+00:00"
    owner_a = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    owner_b = {
        "day0_observed_extreme_source": "wu_api_same_time_revision",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.25,
        "day0_observed_extreme_unit": "C",
    }
    seed = seed_dir / "owner-a.json"
    seed.write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "computed_at": "2026-07-19T05:02:00+00:00",
                "source_cycle_time": cycle,
                "baseline_source_run_id": "baseline:0",
                "openmeteo_source_run_id": "openmeteo:0",
                "openmeteo_payload_json": "payload.json",
                "precision_metadata_json": "precision.json",
                "bins": [{"bin_id": "warm"}],
                "upgrade_trigger": "day0_observation_advanced",
                **owner_a,
            }
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(seed),
        **owner_a,
    ) is True
    conn.commit()
    conn.close()

    def swap_owner_after_build(*_args, **_kwargs):
        swap = sqlite3.connect(db_path)
        swap.row_factory = sqlite3.Row
        assert cycle_advance._record_enqueue(
            swap,
            city="Shanghai",
            target_date="2026-07-19",
            metric="high",
            consumed_cycle_iso=cycle,
            target_cycle_iso=cycle,
            held_position=True,
            seed_file=str(tmp_path / "owner-b.json"),
            reason="DAY0_OBSERVATION_ADVANCED",
            replace_existing_seed_file=True,
            **owner_b,
        ) is True
        swap.commit()
        swap.close()
        return SimpleNamespace(
            ok=True,
            status="READY",
            reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
            request={
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "source_cycle_time": cycle,
            },
        )

    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        swap_owner_after_build,
    )
    processed, failed, _reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=1,
    )
    assert len(processed) == 1
    assert not failed
    assert not tuple((tmp_path / "requests").glob("*.json"))
    receipt = next((tmp_path / "seed_processed").glob("*.receipt.json"))
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == (
        "SKIPPED_STALE_DAY0_ENQUEUE_OWNER"
    )


def test_queue_defers_legacy_null_day0_identity_without_stale_receipt(tmp_path, monkeypatch) -> None:
    """A legacy marker with no persisted identity is not authoritative stale evidence."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    seed = seed_dir / "legacy-null.json"
    seed.write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
                "computed_at": "2026-07-19T05:02:00+00:00",
                "source_cycle_time": cycle,
                "baseline_source_run_id": "baseline:0",
                "openmeteo_source_run_id": "openmeteo:0",
                "openmeteo_payload_json": "payload.json",
                "precision_metadata_json": "precision.json",
                "bins": [{"bin_id": "warm"}],
                "upgrade_trigger": "day0_observation_advanced",
                **identity,
            }
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(seed),
        **identity,
    ) is True
    conn.execute(
        "UPDATE cycle_advance_enqueues SET day0_conditioning_identity_json = NULL"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy indeterminate seed reached request construction")
        ),
    )
    processed, failed, reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=2,
    )
    assert not processed
    assert not failed
    assert seed.is_file()
    assert not tuple((tmp_path / "seed_processed").glob("*.receipt.json"))
    assert "REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE" in reasons
    assert (tmp_path / ".replacement-day0-enqueue.cursor").read_text(encoding="utf-8").strip() == (
        seed.name
    )


def test_queue_scans_past_indeterminate_day0_prefix_without_starving_current_seed(
    tmp_path, monkeypatch
) -> None:
    """The actionable limit excludes deferred ownership inspections."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }

    def write_seed(name: str, target_date: str) -> Path:
        path = seed_dir / name
        path.write_text(
            json.dumps(
                {
                    "city": "Shanghai",
                    "target_date": target_date,
                    "temperature_metric": "high",
                    "computed_at": "2026-07-19T05:02:00+00:00",
                    "source_cycle_time": cycle,
                    "baseline_source_run_id": "baseline:0",
                    "openmeteo_source_run_id": "openmeteo:0",
                    "openmeteo_payload_json": "payload.json",
                    "precision_metadata_json": "precision.json",
                    "bins": [{"bin_id": "warm"}],
                    "upgrade_trigger": "day0_observation_advanced",
                    **identity,
                }
            ),
            encoding="utf-8",
        )
        return path

    indeterminate = (
        write_seed("00.indeterminate.json", "2026-07-17"),
        write_seed("01.indeterminate.json", "2026-07-18"),
    )
    current = write_seed("99.current.json", "2026-07-19")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(current),
        **identity,
    ) is True
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        materialization_queue, "_cycle_advance_seed_priority_map", lambda *_args: {}
    )
    built: list[Mapping[str, object]] = []

    def ready_builder(payload, **_kwargs):
        built.append(payload)
        return SimpleNamespace(
            ok=True,
            status="READY",
            reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
            request={
                "city": str(payload["city"]),
                "target_date": str(payload["target_date"]),
                "temperature_metric": "high",
                "source_cycle_time": cycle,
            },
        )

    monkeypatch.setattr(
        materialization_queue,
        "build_replacement_forecast_materialization_request",
        ready_builder,
    )

    processed, failed, reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=len(indeterminate),
    )
    assert len(processed) == 1
    assert not failed
    assert len(built) == 1
    assert not current.exists()
    assert all(path.is_file() for path in indeterminate)
    assert "REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE" in reasons

    later_current = write_seed("99.current-next.json", "2026-07-20")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-20",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(later_current),
        **identity,
    ) is True
    conn.commit()
    conn.close()
    processed, failed, reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        forecast_db=db_path,
        limit=len(indeterminate),
    )
    assert len(processed) == 1
    assert not failed
    assert len(built) == 2
    assert not later_current.exists()
    assert all(path.is_file() for path in indeterminate)
    assert "REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE" in reasons


def test_queue_rotates_bounded_indeterminate_inspections_across_reload(
    tmp_path, monkeypatch
) -> None:
    """A large retained backlog has bounded DB reads yet cannot starve a tail owner."""
    db_path = _prepare_forecast_db(tmp_path)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    request_dir = tmp_path / "requests"
    cycle = "2026-07-19T00:00:00+00:00"
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }

    def write_seed(name: str, target_date: str) -> Path:
        path = seed_dir / name
        path.write_text(
            json.dumps(
                {
                    "city": "Shanghai",
                    "target_date": target_date,
                    "temperature_metric": "high",
                    "computed_at": "2026-07-19T05:02:00+00:00",
                    "source_cycle_time": cycle,
                    "baseline_source_run_id": "baseline:0",
                    "openmeteo_source_run_id": "openmeteo:0",
                    "openmeteo_payload_json": "payload.json",
                    "precision_metadata_json": "precision.json",
                    "bins": [{"bin_id": "warm"}],
                    "upgrade_trigger": "day0_observation_advanced",
                    **identity,
                }
            ),
            encoding="utf-8",
        )
        return path

    indeterminate = tuple(
        write_seed(f"{index:03d}.indeterminate.json", "2026-07-17")
        for index in range(100)
    )
    current = write_seed("999.current.json", "2026-07-19")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(current),
        **identity,
    ) is True
    conn.commit()
    conn.close()

    priority_loads: list[Path] = []

    def configure_queue() -> None:
        original_priority_load = materialization_queue._load_request_payload_for_coalescing

        def counting_priority_load(path: Path):
            priority_loads.append(path)
            return original_priority_load(path)

        monkeypatch.setattr(
            materialization_queue,
            "_load_request_payload_for_coalescing",
            counting_priority_load,
        )
        monkeypatch.setattr(
            materialization_queue,
            "build_replacement_forecast_materialization_request",
            lambda payload, **_kwargs: SimpleNamespace(
                ok=True,
                status="READY",
                reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
                request={
                    "city": str(payload["city"]),
                    "target_date": str(payload["target_date"]),
                    "temperature_metric": "high",
                    "source_cycle_time": cycle,
                },
            ),
        )

    configure_queue()
    real_connect = state_db._connect
    connect_calls: list[object] = []

    def counting_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(state_db, "_connect", counting_connect)
    real_connect_read_only = state_db._connect_read_only
    read_only_calls: list[object] = []
    priority_queries: list[str] = []

    def counting_connect_read_only(*args, **kwargs):
        read_only_calls.append((args, kwargs))
        inner = real_connect_read_only(*args, **kwargs)

        class CountingConnection:
            def execute(self, sql, *execute_args, **execute_kwargs):
                priority_queries.append(str(sql))
                return inner.execute(sql, *execute_args, **execute_kwargs)

            def __getattr__(self, name):
                return getattr(inner, name)

        return CountingConnection()

    monkeypatch.setattr(state_db, "_connect_read_only", counting_connect_read_only)
    limit = 2
    inspection_cap = max(
        limit * materialization_queue._DAY0_ENQUEUE_OWNERSHIP_INSPECTION_MULTIPLIER,
        materialization_queue._DAY0_ENQUEUE_OWNERSHIP_MIN_INSPECTIONS,
    )
    processed, failed, reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=request_dir,
        forecast_db=db_path,
        limit=limit,
    )
    assert not processed
    assert not failed
    assert len(connect_calls) <= inspection_cap
    assert len(priority_loads) <= inspection_cap
    assert len(read_only_calls) <= 1
    assert len(priority_queries) <= inspection_cap
    assert current.is_file()
    assert all(path.is_file() for path in indeterminate)
    assert "REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE" in reasons
    assert "REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_LIMIT_REACHED" in reasons
    cursor = tmp_path / ".replacement-day0-enqueue.cursor"
    first_cursor = cursor.read_text(encoding="utf-8").strip()
    assert first_cursor == "007.indeterminate.json"

    importlib.reload(materialization_queue)
    configure_queue()
    processed, failed, _reasons = materialization_queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=request_dir,
        forecast_db=db_path,
        limit=limit,
    )
    assert not processed
    assert not failed
    assert cursor.read_text(encoding="utf-8").strip() == "015.indeterminate.json"

    max_passes = (len(indeterminate) + 1 + inspection_cap - 1) // inspection_cap
    for _ in range(max_passes - 2):
        processed, failed, _reasons = materialization_queue._prepare_seed_requests(
            seed_dir=seed_dir,
            seed_processed_dir=tmp_path / "seed_processed",
            seed_failed_dir=tmp_path / "seed_failed",
            request_dir=request_dir,
            forecast_db=db_path,
            limit=limit,
        )
        assert not failed
        if processed:
            break
    assert not current.exists()
    assert all(path.is_file() for path in indeterminate)


def test_day0_conditioning_marker_allows_same_time_revisions_but_never_regresses_time(
    tmp_path,
) -> None:
    """A late older condition cannot replace a newer marker or its seed."""
    db_path = _prepare_forecast_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cycle_iso = "2026-07-19T00:00:00+00:00"

    def record(seed_name: str, **identity: object) -> bool:
        seed_file = tmp_path / seed_name
        seed_file.write_text("{}", encoding="utf-8")
        return cycle_advance._record_enqueue(
            conn,
            city="Shanghai",
            target_date="2026-07-19",
            metric="high",
            consumed_cycle_iso=cycle_iso,
            target_cycle_iso=cycle_iso,
            held_position=True,
            seed_file=str(seed_file),
            reason=None,
            **identity,
        )

    newer = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    assert record("newer.json", **newer) is True

    older = {
        "day0_observed_extreme_source": "late_alternate_source",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 20.5,
        "day0_observed_extreme_unit": "F",
    }
    assert record("older.json", **older) is False
    row = conn.execute(
        "SELECT day0_observed_extreme_observation_time, day0_conditioning_identity_json, seed_file "
        "FROM cycle_advance_enqueues"
    ).fetchone()
    assert row["day0_observed_extreme_observation_time"] == newer[
        "day0_observed_extreme_observation_time"
    ]
    assert row["seed_file"] == str(tmp_path / "newer.json")

    same_time_revisions = (
        {"day0_observed_extreme_source": "wu_api+same_station_fast_tail"},
        {"day0_observed_extreme_c": 21.25},
        {"day0_observed_extreme_unit": "F"},
    )
    current = newer
    for index, revision in enumerate(same_time_revisions, start=1):
        current = {**current, **revision}
        assert record(f"same-time-{index}.json", **current) is True

    row = conn.execute(
        "SELECT day0_observed_extreme_observation_time, day0_conditioning_identity_json, seed_file "
        "FROM cycle_advance_enqueues"
    ).fetchone()
    conn.close()
    assert row["day0_observed_extreme_observation_time"] == newer[
        "day0_observed_extreme_observation_time"
    ]
    assert json.loads(row["day0_conditioning_identity_json"]) == {
        "observation_time": "2026-07-19T05:00:00.900000+00:00",
        "observed_extreme_c": 21.25,
        "source": "wu_api+same_station_fast_tail",
        "unit": "F",
    }
    assert row["seed_file"] == str(tmp_path / "same-time-3.json")


def test_day0_request_coalescing_keeps_distinct_conditioning_identities(tmp_path) -> None:
    """The request drain cannot discard a fresh Day0 condition as a duplicate cycle."""
    base = {
        "city": "Shanghai",
        "target_date": "2026-07-19",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-19T00:00:00+00:00",
        "baseline_source_run_id": "baseline:0",
        "openmeteo_source_run_id": "openmeteo:0",
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps({**base, "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00"}),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps({**base, "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.900000+00:00"}),
        encoding="utf-8",
    )

    remaining, superseded = materialization_queue._coalesce_superseded_materialization_requests(
        (first, second), processed_path=tmp_path / "processed"
    )

    assert set(remaining) == {first, second}
    assert superseded == ()


def test_day0_drained_marker_with_active_provisional_posterior_does_not_reenqueue(
    tmp_path,
) -> None:
    """A drained marker completes when active provisional provenance consumed its identity."""
    db_path = _prepare_forecast_db(tmp_path)
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    seed = tmp_path / "drained.seed.json"
    seed.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(seed),
        **identity,
    )
    conn.commit()
    seed.unlink()

    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        **identity,
    ) is False
    conn.close()

    _insert_live_posterior(
        db_path,
        cycle_iso=cycle,
        computed_at="2026-07-19T05:01:00+00:00",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "openmeteo_anchor_artifact_id": 1,
                    "day0_provisional_observation": {
                        "active": True,
                        "source": identity["day0_observed_extreme_source"],
                        "observation_time": identity[
                            "day0_observed_extreme_observation_time"
                        ],
                        "observed_extreme_c": identity["day0_observed_extreme_c"],
                        "unit": identity["day0_observed_extreme_unit"],
                    },
                    "day0_conditioning": {
                        "source": "stale_fallback",
                        "observation_time": "2026-07-19T05:00:00+00:00",
                        "observed_extreme_c": 0.0,
                        "unit": "F",
                    },
                }
            ),
        ),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        **identity,
    ) is True
    conn.close()


def test_day0_drained_marker_rejects_future_posterior_as_of(tmp_path) -> None:
    """A future-dated posterior cannot complete this bridge decision."""
    db_path = _prepare_forecast_db(tmp_path)
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    identity = {
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-19T05:00:00.132000+00:00",
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_unit": "C",
    }
    seed = tmp_path / "drained.seed.json"
    seed.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle,
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(seed),
        **identity,
    )
    conn.commit()
    seed.unlink()
    _insert_live_posterior(
        db_path,
        cycle_iso=cycle,
        computed_at="2026-07-19T05:03:00+00:00",
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "day0_conditioning": {
                        "source": identity["day0_observed_extreme_source"],
                        "observation_time": identity[
                            "day0_observed_extreme_observation_time"
                        ],
                        "observed_extreme_c": identity["day0_observed_extreme_c"],
                        "unit": identity["day0_observed_extreme_unit"],
                    }
                }
            ),
        ),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        **identity,
    ) is False
    conn.close()


def test_day0_extreme_bridge_reseeds_new_observation_on_consumed_model_cycle(
    tmp_path, monkeypatch
) -> None:
    """Observation time, not only model cycle, is part of posterior identity."""
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    _insert_live_posterior(
        db_path,
        cycle_iso=cycle.isoformat(),
        computed_at="2026-07-19T05:05:00+00:00",
    )
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T06:00:00+00:00"),
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    # Another family has advanced the global cycle high-water mark. Shanghai
    # still needs same-cycle re-materialization because its observation clock
    # advanced independently.
    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: datetime(2026, 7, 19, 6, tzinfo=UTC),
    )
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        fake_build_seed,
    )

    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 6, 1, tzinfo=UTC),
        held_position=False,
    )

    assert report["status"] == "DAY0_OBSERVATION_ADVANCE_ENQUEUED"
    assert report["enqueued"] is True
    assert report["consumed_cycle"] == cycle.isoformat()
    assert report["target_cycle"] == cycle.isoformat()
    assert calls["count"] == 1
    row = _fetch_enqueue_row(db_path)
    assert row["day0_observed_extreme_observation_time"] == (
        "2026-07-19T06:00:00+00:00"
    )


def test_cycle_poll_catches_up_every_new_day0_identity_on_one_model_cycle(
    tmp_path, monkeypatch
) -> None:
    """A→B→C causal observation clocks must each reach a posterior on one model cycle.

    The bridge may miss a ledger publication (for example a second writer's
    durable catch-up).  The bounded poll is therefore the durable liveness
    backstop: after each queue drain commits the matching posterior, the next
    canonical identity must re-enter the same-cycle seed transport.
    """
    db_path = _prepare_forecast_db(tmp_path)
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payloads = [
        _day0_payload("2026-07-19T05:00:00+00:00"),
        _day0_payload("2026-07-19T05:01:00+00:00"),
        _day0_payload("2026-07-19T05:02:00+00:00"),
    ]
    current = {"payload": payloads[0]}
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: dict(current["payload"]),
    )
    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    _insert_materialized_day0_posterior(
        db_path,
        cycle_iso=cycle.isoformat(),
        computed_at="2026-07-19T05:00:30+00:00",
        payload=payloads[0],
    )
    conn = sqlite3.connect(db_path)
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso=cycle.isoformat(),
        target_cycle_iso=cycle.isoformat(),
        held_position=True,
        seed_file=str(seed_dir / "a-drained.json"),
        day0_observed_extreme_observation_time=payloads[0][
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payloads[0]["day0_observed_extreme_source"],
        day0_observed_extreme_c=payloads[0]["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payloads[0]["day0_observed_extreme_unit"],
    )
    conn.commit()
    conn.close()

    for index, payload in enumerate(payloads[1:], start=1):
        current["payload"] = payload
        report = cycle_advance.enqueue_cycle_advance_reseeds(
            forecast_db=db_path,
            seed_dir=seed_dir,
            raw_manifest_dir=raw_dir,
            computed_at=datetime(2026, 7, 19, 5, index, tzinfo=UTC),
            limit=1,
            scopes=(("Shanghai", "2026-07-19", "high"),),
            manifests=(),
        )
        assert report["day0_observation_advances_detected"] == 1
        assert report["seeds_enqueued"] == 1
        marker = _fetch_enqueue_row(db_path)
        assert marker["day0_observed_extreme_observation_time"] == payload[
            "day0_observed_extreme_observation_time"
        ]
        marker_seed = Path(marker["seed_file"])
        assert json.loads(marker_seed.read_text(encoding="utf-8"))[
            "day0_observed_extreme_observation_time"
        ] == payload["day0_observed_extreme_observation_time"]
        marker_seed.unlink()
        _insert_materialized_day0_posterior(
            db_path,
            cycle_iso=cycle.isoformat(),
            computed_at=f"2026-07-19T05:0{index}:30+00:00",
            payload=payload,
        )

    assert calls["count"] == 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    identity = cycle_advance._day0_conditioning_identity(
        source=payloads[2]["day0_observed_extreme_source"],
        observation_time=payloads[2]["day0_observed_extreme_observation_time"],
        observed_extreme_c=payloads[2]["day0_observed_extreme_c"],
        unit=payloads[2]["day0_observed_extreme_unit"],
    )
    assert identity is not None
    assert cycle_advance._latest_posterior_matches_day0_conditioning(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        identity=identity,
        target_cycle_iso=cycle.isoformat(),
        as_of=datetime(2026, 7, 19, 5, 3, tzinfo=UTC),
    )
    latest = conn.execute(
        "SELECT MAX(posterior_id) FROM forecast_posteriors"
    ).fetchone()[0]
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (latest,),
        ).fetchone()[0]
    )
    provenance["openmeteo_anchor_artifact_id"] = None
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), latest),
    )
    assert not cycle_advance._latest_posterior_matches_day0_conditioning(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        identity=identity,
        target_cycle_iso=cycle.isoformat(),
        as_of=datetime(2026, 7, 19, 5, 3, tzinfo=UTC),
    )
    conn.close()


def test_cycle_poll_keeps_later_claimed_owner_past_batch_timeout_until_terminal(
    tmp_path, monkeypatch
) -> None:
    """A request beyond worker width cannot inherit the whole batch's 270s death clock.

    The queue can claim more requests than its four workers can start. The fifth
    exact witness therefore remains owner even after claimed_at + 240s + 30s;
    only the queue's terminal move/removal releases it for re-enqueue.
    """
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    payload = _day0_payload("2026-07-19T05:00:00+00:00")
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: dict(payload),
    )
    monkeypatch.setattr(cycle_advance, "freshest_materializable_cycle", lambda _conn: cycle)
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    owned_seed = Path(cfg["seed_dir"]) / "consumed.enqueue-owner.json"
    identity = cycle_advance._day0_conditioning_identity(
        source=payload["day0_observed_extreme_source"],
        observation_time=payload["day0_observed_extreme_observation_time"],
        observed_extreme_c=payload["day0_observed_extreme_c"],
        unit=payload["day0_observed_extreme_unit"],
    )
    assert identity is not None
    conn = sqlite3.connect(db_path)
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=cycle.isoformat(),
        held_position=False,
        seed_file=str(owned_seed),
        reason="MISSING_LIVE_POSTERIOR",
        day0_observed_extreme_observation_time=payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payload["day0_observed_extreme_unit"],
    )
    conn.commit()
    conn.close()

    claim_dir = Path(cfg["inflight_dir"]) / "claimed-owner"
    claim_dir.mkdir(parents=True)
    request_names = [f"earlier-{index}.json" for index in range(4)] + [owned_seed.name]
    (claim_dir / "_claim.json").write_text(
        json.dumps(
            {
                "claimed_at": "2026-07-19T05:00:00+00:00",
                "request_names": request_names,
            }
        ),
        encoding="utf-8",
    )
    for request_name in request_names[:-1]:
        (claim_dir / request_name).write_text("{}", encoding="utf-8")
    claimed_request = claim_dir / owned_seed.name
    claimed_request.write_text(
        json.dumps(
            {
                "computed_at": "2026-07-19T05:00:00+00:00",
                "day0_enqueue_owner_witness": {
                    "city": "Shanghai",
                    "target_date": "2026-07-19",
                    "metric": "high",
                    "target_cycle_time": cycle.isoformat(),
                    "seed_file": str(owned_seed),
                    "conditioning_identity": identity,
                },
            }
        ),
        encoding="utf-8",
    )

    for seconds in (15, 30, 45):
        report = cycle_advance.enqueue_cycle_advance_reseeds(
            forecast_db=db_path,
            seed_dir=cfg["seed_dir"],
            raw_manifest_dir=cfg["raw_manifest_dir"],
            computed_at=datetime(2026, 7, 19, 5, 0, seconds, tzinfo=UTC),
            limit=1,
            scopes=(("Shanghai", "2026-07-19", "high"),),
            manifests=(),
        )
        assert report["seeds_enqueued"] == 0
        assert report["already_enqueued"] == 1
        assert _fetch_enqueue_row(db_path)["seed_file"] == str(owned_seed)
    assert calls["count"] == 0

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=cfg["seed_dir"],
        raw_manifest_dir=cfg["raw_manifest_dir"],
        computed_at=datetime(2026, 7, 19, 5, 4, 31, tzinfo=UTC),
        limit=1,
        scopes=(("Shanghai", "2026-07-19", "high"),),
        manifests=(),
    )
    assert report["seeds_enqueued"] == 0
    assert report["already_enqueued"] == 1
    assert calls["count"] == 0
    assert _fetch_enqueue_row(db_path)["seed_file"] == str(owned_seed)

    claimed_request.unlink()
    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=cfg["seed_dir"],
        raw_manifest_dir=cfg["raw_manifest_dir"],
        computed_at=datetime(2026, 7, 19, 5, 4, 45, tzinfo=UTC),
        limit=1,
        scopes=(("Shanghai", "2026-07-19", "high"),),
        manifests=(),
    )
    marker = _fetch_enqueue_row(db_path)
    assert report["seeds_enqueued"] == 1
    assert calls["count"] == 1
    assert marker["seed_file"] != str(owned_seed)
    assert Path(marker["seed_file"]).is_file()


def test_new_day0_revision_waits_for_exact_inflight_owner_then_replaces_it(
    tmp_path, monkeypatch
) -> None:
    """A newer observation cannot invalidate the request currently materializing."""
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    old_payload = _day0_payload("2026-07-19T05:00:00+00:00")
    new_payload = _day0_payload("2026-07-19T05:05:00+00:00")
    new_conditioning = {
        key: new_payload[key]
        for key in (
            "day0_observed_extreme_observation_time",
            "day0_observed_extreme_source",
            "day0_observed_extreme_c",
            "day0_observed_extreme_unit",
        )
    }
    owned_seed = Path(cfg["seed_dir"]) / "old-revision.enqueue-owner.json"
    old_identity = cycle_advance._day0_conditioning_identity(
        source=old_payload["day0_observed_extreme_source"],
        observation_time=old_payload["day0_observed_extreme_observation_time"],
        observed_extreme_c=old_payload["day0_observed_extreme_c"],
        unit=old_payload["day0_observed_extreme_unit"],
    )
    assert old_identity is not None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(owned_seed),
        reason="MISSING_LIVE_POSTERIOR",
        day0_observed_extreme_observation_time=old_payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=old_payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=old_payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=old_payload["day0_observed_extreme_unit"],
    )
    conn.commit()

    claim_dir = Path(cfg["inflight_dir"]) / "claimed-old-revision"
    claim_dir.mkdir(parents=True)
    claimed_request = claim_dir / owned_seed.name
    claimed_request.write_text(
        json.dumps(
            {
                "day0_enqueue_owner_witness": {
                    "city": "Shanghai",
                    "target_date": "2026-07-19",
                    "metric": "high",
                    "target_cycle_time": cycle,
                    "seed_file": str(owned_seed),
                    "conditioning_identity": old_identity,
                }
            }
        ),
        encoding="utf-8",
    )

    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        **new_conditioning,
    ) is True
    marker = conn.execute(
        "SELECT seed_file, day0_conditioning_identity_json "
        "FROM cycle_advance_enqueues"
    ).fetchone()
    assert marker["seed_file"] == str(owned_seed)
    assert marker["day0_conditioning_identity_json"] == old_identity

    claimed_request.unlink()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        **new_conditioning,
    ) is False
    assert conn.execute("SELECT COUNT(*) FROM cycle_advance_enqueues").fetchone()[0] == 0
    conn.close()


def test_new_day0_revision_waits_for_legacy_pending_owner_then_replaces_it(
    tmp_path, monkeypatch
) -> None:
    """A witnessless legacy request retains its exact seed owner until terminal."""
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    old_payload = _day0_payload("2026-07-19T05:00:00+00:00")
    new_payload = _day0_payload("2026-07-19T05:05:00+00:00")
    owned_seed = Path(cfg["seed_dir"]) / "legacy.enqueue-owner.json"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=cycle,
        held_position=True,
        seed_file=str(owned_seed),
        reason="MISSING_LIVE_POSTERIOR",
        day0_observed_extreme_observation_time=old_payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=old_payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=old_payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=old_payload["day0_observed_extreme_unit"],
    )
    conn.execute(
        "UPDATE cycle_advance_enqueues SET day0_conditioning_identity_json = NULL"
    )
    conn.commit()

    pending = Path(cfg["request_dir"]) / owned_seed.name
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "target_date": "2026-07-19",
                "temperature_metric": "high",
            }
        ),
        encoding="utf-8",
    )
    new_conditioning = {
        key: new_payload[key]
        for key in (
            "day0_observed_extreme_observation_time",
            "day0_observed_extreme_source",
            "day0_observed_extreme_c",
            "day0_observed_extreme_unit",
        )
    }

    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        **new_conditioning,
    ) is True
    marker = conn.execute(
        "SELECT seed_file, day0_conditioning_identity_json "
        "FROM cycle_advance_enqueues"
    ).fetchone()
    assert marker["seed_file"] == str(owned_seed)
    assert marker["day0_conditioning_identity_json"] is None

    pending.unlink()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        **new_conditioning,
    ) is False
    assert conn.execute("SELECT COUNT(*) FROM cycle_advance_enqueues").fetchone()[0] == 0
    conn.close()


def test_day0_owner_claim_lock_closes_pending_to_inflight_move_race(
    tmp_path, monkeypatch
) -> None:
    """A legal queue claim cannot move the request during owner classification."""
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = "2026-07-19T00:00:00+00:00"
    seed_file = Path(cfg["seed_dir"]) / "owner.enqueue-interleaving.json"
    identity = cycle_advance._day0_conditioning_identity(
        source="wu_icao_history",
        observation_time="2026-07-19T05:00:00+00:00",
        observed_extreme_c=21.0,
        unit="C",
    )
    assert identity is not None
    pending = Path(cfg["request_dir"]) / seed_file.name
    pending.parent.mkdir()
    pending.write_text(
        json.dumps(
            {
                "computed_at": "2026-07-19T05:00:00+00:00",
                "day0_enqueue_owner_witness": {
                    "city": "Shanghai",
                    "target_date": "2026-07-19",
                    "metric": "high",
                    "target_cycle_time": cycle,
                    "seed_file": str(seed_file),
                    "conditioning_identity": identity,
                },
            }
        ),
        encoding="utf-8",
    )
    claim_dir = Path(cfg["inflight_dir"]) / "interleaving-claim"
    claim_dir.mkdir(parents=True)
    (claim_dir / "_claim.json").write_text(
        json.dumps({"claimed_at": "2026-07-19T05:00:00+00:00"}),
        encoding="utf-8",
    )

    lock_path = Path(cfg["request_dir"]).parent / ".materialization_queue.lock"
    with materialization_queue._queue_lock(lock_path) as acquired:
        assert acquired is True
        blocked = cycle_advance._day0_enqueue_owner_request_check(
            city="Shanghai",
            target_date="2026-07-19",
            metric="high",
            target_cycle_iso=cycle,
            seed_file=str(seed_file),
            identity=identity,
        )
        assert blocked.state is cycle_advance._Day0EnqueueOwnerRequestState.INDETERMINATE
        assert blocked.reason == "DAY0_ENQUEUE_OWNER_REQUEST_QUEUE_LOCK_BUSY"
        pending.replace(claim_dir / seed_file.name)

    claimed = cycle_advance._day0_enqueue_owner_request_check(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        seed_file=str(seed_file),
        identity=identity,
    )
    assert claimed.state is cycle_advance._Day0EnqueueOwnerRequestState.ACTIVE


def test_aged_pending_day0_owner_survives_until_terminal_request_move(
    tmp_path, monkeypatch
) -> None:
    """An unclaimed request has no materializer timeout and releases only when terminal/missing."""
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    payload = _day0_payload("2026-07-19T05:00:00+00:00")
    seed_file = Path(cfg["seed_dir"]) / "owner.enqueue-pending.json"
    identity = cycle_advance._day0_conditioning_identity(
        source=payload["day0_observed_extreme_source"],
        observation_time=payload["day0_observed_extreme_observation_time"],
        observed_extreme_c=payload["day0_observed_extreme_c"],
        unit=payload["day0_observed_extreme_unit"],
    )
    assert identity is not None
    conn = sqlite3.connect(db_path)
    assert cycle_advance._record_enqueue(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=cycle,
        held_position=False,
        seed_file=str(seed_file),
        reason="MISSING_LIVE_POSTERIOR",
        day0_observed_extreme_observation_time=payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payload["day0_observed_extreme_unit"],
    )
    conn.commit()
    pending = Path(cfg["request_dir"]) / seed_file.name
    pending.parent.mkdir()
    pending.write_text(
        json.dumps(
            {
                "computed_at": "2026-07-19T00:00:00+00:00",
                "day0_enqueue_owner_witness": {
                    "city": "Shanghai",
                    "target_date": "2026-07-19",
                    "metric": "high",
                    "target_cycle_time": cycle,
                    "seed_file": str(seed_file),
                    "conditioning_identity": identity,
                },
            }
        ),
        encoding="utf-8",
    )

    as_of = datetime(2026, 7, 19, 5, 4, 31, tzinfo=UTC)
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=as_of,
        day0_observed_extreme_observation_time=payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payload["day0_observed_extreme_unit"],
    ) is True
    pending.unlink()
    assert cycle_advance._already_enqueued(
        conn,
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        target_cycle_iso=cycle,
        as_of=as_of,
        day0_observed_extreme_observation_time=payload[
            "day0_observed_extreme_observation_time"
        ],
        day0_observed_extreme_source=payload["day0_observed_extreme_source"],
        day0_observed_extreme_c=payload["day0_observed_extreme_c"],
        day0_observed_extreme_unit=payload["day0_observed_extreme_unit"],
    ) is False
    conn.close()


def test_day0_owner_config_read_failure_is_indeterminate_and_retains_marker(
    tmp_path, monkeypatch, caplog
) -> None:
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    conn, payload, seed_file, _identity, cycle = _record_missing_day0_owner(
        db_path,
        cfg,
        seed_name="owner.enqueue-config-error.json",
    )
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )

    assert _missing_day0_owner_is_retained(conn, payload=payload, cycle=cycle) is True
    assert _fetch_enqueue_row(db_path)["seed_file"] == str(seed_file)
    assert (
        "DAY0_ENQUEUE_OWNER_REQUEST_CONFIG_READ_FAILED:RuntimeError"
        in caplog.text
    )
    conn.close()


def test_day0_owner_directory_scan_failure_is_indeterminate_and_retains_marker(
    tmp_path, monkeypatch, caplog
) -> None:
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    conn, payload, seed_file, _identity, cycle = _record_missing_day0_owner(
        db_path,
        cfg,
        seed_name="owner.enqueue-directory-error.json",
    )
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    original_iterdir = Path.iterdir

    def fail_inflight_scan(path: Path):
        if path == Path(cfg["inflight_dir"]):
            raise PermissionError("scan denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_inflight_scan)
    assert _missing_day0_owner_is_retained(conn, payload=payload, cycle=cycle) is True
    assert _fetch_enqueue_row(db_path)["seed_file"] == str(seed_file)
    assert (
        "DAY0_ENQUEUE_OWNER_REQUEST_INFLIGHT_SCAN_FAILED:PermissionError"
        in caplog.text
    )
    conn.close()


def test_day0_owner_inflight_entry_stat_failure_is_indeterminate_and_retains_marker(
    tmp_path, monkeypatch, caplog
) -> None:
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    conn, payload, seed_file, _identity, cycle = _record_missing_day0_owner(
        db_path,
        cfg,
        seed_name="owner.enqueue-entry-stat-error.json",
    )
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    batch = Path(cfg["inflight_dir"]) / "claimed"
    batch.mkdir(parents=True)
    claimed = batch / seed_file.name
    claimed.write_text(
        json.dumps(
            {
                "computed_at": "2026-07-19T05:00:00+00:00",
                "day0_enqueue_owner_witness": {
                    "city": "Shanghai",
                    "target_date": "2026-07-19",
                    "metric": "high",
                    "target_cycle_time": cycle,
                    "seed_file": str(seed_file),
                    "conditioning_identity": _identity,
                },
            }
        ),
        encoding="utf-8",
    )
    original_stat = Path.stat

    def fail_claimed_batch_stat(path: Path, *args, **kwargs):
        if path == batch:
            raise PermissionError("entry stat denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_claimed_batch_stat)
    assert _missing_day0_owner_is_retained(conn, payload=payload, cycle=cycle) is True
    assert _fetch_enqueue_row(db_path)["seed_file"] == str(seed_file)
    assert (
        "DAY0_ENQUEUE_OWNER_REQUEST_INFLIGHT_ENTRY_FAILED:PermissionError"
        in caplog.text
    )
    conn.close()


def test_day0_owner_json_failure_is_indeterminate_and_retains_marker(
    tmp_path, monkeypatch, caplog
) -> None:
    db_path = _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    conn, payload, seed_file, _identity, cycle = _record_missing_day0_owner(
        db_path,
        cfg,
        seed_name="owner.enqueue-json-error.json",
    )
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    pending = Path(cfg["request_dir"]) / seed_file.name
    pending.parent.mkdir()
    pending.write_text("{", encoding="utf-8")

    assert _missing_day0_owner_is_retained(conn, payload=payload, cycle=cycle) is True
    assert _fetch_enqueue_row(db_path)["seed_file"] == str(seed_file)
    assert (
        "DAY0_ENQUEUE_OWNER_REQUEST_PENDING_JSON_INVALID:JSONDecodeError"
        in caplog.text
    )
    conn.close()


def test_day0_extreme_bridge_not_configured_is_failsoft(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": None, "seed_dir": None, "raw_manifest_dir": None},
    )
    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_NOT_CONFIGURED"


def test_day0_extreme_bridge_no_observed_extreme_is_failsoft(tmp_path, monkeypatch) -> None:
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery, "_day0_observed_extreme_seed_payload", lambda **_kwargs: None,
    )
    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_NO_OBSERVED_EXTREME"


def test_day0_extreme_bridge_materializes_zero_observation_state(
    tmp_path, monkeypatch
) -> None:
    """A typed zero-observation fact is evidence, not an unsupported kwarg."""

    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: {
            "day0_observation_state": "zero_target_date_observations"
        },
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    captured: dict[str, object] = {}

    def _capture_seed(_conn_arg, **kwargs):
        captured.update(kwargs)
        seed_file = Path(kwargs["seed_path"]) / "zero-observation.seed.json"
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        _capture_seed,
    )

    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 0, 1, tzinfo=UTC),
        held_position=False,
    )

    assert report["status"] == "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
    assert report["enqueued"] is True
    assert (
        captured["day0_observation_state"]
        == "zero_target_date_observations"
    )


def test_day0_extreme_bridge_config_lookup_failure_is_failsoft(monkeypatch) -> None:
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        _raise,
    )
    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_FAILSOFT_SKIPPED"
    assert "error" in report


def test_day0_extreme_bridge_auto_detects_held_position(tmp_path, monkeypatch) -> None:
    """held_position=None auto-detects via the coworker's held-family helper (2b5ae40a3): a
    family with money at risk is tagged held for priority draining even when the caller
    (event emission) does not itself know about held positions."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, _calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    import src.events.reactor as reactor_mod

    monkeypatch.setattr(
        reactor_mod,
        "_edli_current_held_position_family_keys",
        lambda: {("Shanghai", "2026-07-19", "high")},
    )

    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
    )
    assert report["held_position"] is True


def test_day0_extreme_bridge_held_autodetect_failure_defaults_false(tmp_path, monkeypatch) -> None:
    """A held-family read failure must not crash the bridge — fall back to non-held so the seed
    still gets written (priority tagging is best-effort, never a gate on whether to seed)."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    import src.events.reactor as reactor_mod

    def _raise():
        raise RuntimeError("trades db unreachable")

    monkeypatch.setattr(reactor_mod, "_edli_current_held_position_family_keys", _raise)

    report = cycle_advance._materialize_day0_extreme_updated_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
    )
    assert report["held_position"] is False
    assert report["enqueued"] is True
    assert calls["count"] == 1


def test_async_bridge_returns_immediately_and_replays_newer_coalesced_fact(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[datetime] = []

    def _materialize(**kwargs):
        calls.append(kwargs["computed_at"])
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2.0)
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(cycle_advance, "_materialize_day0_extreme_updated_seed", _materialize)
    first_at = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    second_at = datetime(2026, 7, 20, 5, 0, 1, tzinfo=UTC)

    begin = time.monotonic()
    first = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai", target_date="2026-07-20", metric="high",
        computed_at=first_at, held_position=False,
    )
    elapsed_ms = (time.monotonic() - begin) * 1000.0
    assert first["status"] == "DAY0_EXTREME_BRIDGE_QUEUED"
    assert elapsed_ms < 50.0
    assert started.wait(timeout=1.0)

    second = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai", target_date="2026-07-20", metric="high",
        computed_at=second_at, held_position=False,
    )
    assert second["status"] == "DAY0_EXTREME_BRIDGE_COALESCED"
    release.set()

    assert cycle_advance._wait_for_day0_materialization_bridge_idle(2.0)
    assert calls == [first_at, second_at]


def test_held_bridge_lane_is_not_blocked_by_slow_entry_family(monkeypatch) -> None:
    entry_started = threading.Event()
    release_entry = threading.Event()
    held_done = threading.Event()

    def _materialize(**kwargs):
        if kwargs["city"] == "SlowEntry":
            entry_started.set()
            assert release_entry.wait(timeout=2.0)
        else:
            held_done.set()
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(cycle_advance, "_materialize_day0_extreme_updated_seed", _materialize)
    cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="SlowEntry", target_date="2026-07-20", metric="low", held_position=False,
    )
    assert entry_started.wait(timeout=1.0)

    held = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Tokyo", target_date="2026-07-20", metric="high", held_position=True,
    )
    assert held["held_lane"] is True
    assert held_done.wait(timeout=0.5), "held family must have a reserved worker lane"

    release_entry.set()
    assert cycle_advance._wait_for_day0_materialization_bridge_idle(2.0)


def test_running_entry_is_promoted_to_held_lane_on_coalesced_replay(monkeypatch) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def _materialize(**kwargs):
        calls.append((kwargs["held_position"], threading.current_thread().name))
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2.0)
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(
        cycle_advance,
        "_materialize_day0_extreme_updated_seed",
        _materialize,
    )
    cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="PromotedCity",
        target_date="2026-07-20",
        metric="low",
        held_position=False,
    )
    assert first_started.wait(timeout=1.0)

    promoted = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="PromotedCity",
        target_date="2026-07-20",
        metric="low",
        held_position=True,
    )
    assert promoted["held_lane"] is False
    release_first.set()

    assert cycle_advance._wait_for_day0_materialization_bridge_idle(2.0)
    assert calls == [
        (False, "day0-materialization-entry"),
        (True, "day0-materialization-held"),
    ]


def test_default_route_is_nonblocking_entry_lane(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def _materialize(**_kwargs):
        started.set()
        assert release.wait(timeout=2.0)
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(
        cycle_advance,
        "_materialize_day0_extreme_updated_seed",
        _materialize,
    )
    monkeypatch.setattr(
        cycle_advance,
        "_day0_bridge_held_position_keys",
        lambda _keys: set(),
    )

    begin = time.monotonic()
    entry = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="EntryCity", target_date="2026-07-20", metric="high",
    )
    elapsed_ms = (time.monotonic() - begin) * 1000.0

    assert entry["held_lane"] is None
    assert entry["priority_classification_pending"] is True
    assert elapsed_ms < 50.0
    assert started.wait(timeout=1.0)
    release.set()
    assert cycle_advance._wait_for_day0_materialization_bridge_idle(1.0)


def test_default_fast_path_classifies_held_before_execution_queue(monkeypatch) -> None:
    entry_started = threading.Event()
    release_entry = threading.Event()
    held_done = threading.Event()

    def _materialize(**kwargs):
        if kwargs["city"] == "SlowEntry":
            entry_started.set()
            assert release_entry.wait(timeout=2.0)
        else:
            held_done.set()
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(
        cycle_advance,
        "_materialize_day0_extreme_updated_seed",
        _materialize,
    )
    monkeypatch.setattr(
        cycle_advance,
        "_day0_bridge_held_position_keys",
        lambda keys: {key for key in keys if key[0] == "FastHeld"},
    )
    cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="SlowEntry",
        target_date="2026-07-20",
        metric="high",
        held_position=False,
    )
    assert entry_started.wait(timeout=1.0)

    begin = time.monotonic()
    queued = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="FastHeld",
        target_date="2026-07-20",
        metric="low",
    )
    elapsed_ms = (time.monotonic() - begin) * 1000.0

    assert queued["priority_classification_pending"] is True
    assert elapsed_ms < 50.0
    assert held_done.wait(timeout=0.5), "classified held work must use reserved lane"
    release_entry.set()
    assert cycle_advance._wait_for_day0_materialization_bridge_idle(2.0)


def test_reactor_catchup_routes_current_held_family_to_reserved_lane(
    monkeypatch,
) -> None:
    import src.events.reactor as reactor

    rows = (
        ("HeldCity", "2026-07-20", "low"),
        ("EntryCity", "2026-07-20", "high"),
    )

    class _WorldRead:
        def execute(self, *_args, **_kwargs):
            return type("_Rows", (), {"fetchall": lambda self: rows})()

        def close(self):
            return None

    calls = []
    monkeypatch.setattr(reactor, "get_world_connection_read_only", _WorldRead)
    monkeypatch.setattr(
        reactor,
        "_edli_current_held_position_family_keys",
        lambda: {("HeldCity", "2026-07-20", "low")},
    )
    monkeypatch.setattr(
        cycle_advance,
        "enqueue_day0_extreme_updated_materialization_seed",
        lambda **kwargs: calls.append(kwargs) or {"status": "TEST_QUEUED"},
    )

    reactor._edli_bridge_day0_extreme_materialization_seeds(("event-1",))

    assert calls == [
        {
            "city": "EntryCity",
            "target_date": "2026-07-20",
            "metric": "high",
            "held_position": False,
        },
        {
            "city": "HeldCity",
            "target_date": "2026-07-20",
            "metric": "low",
            "held_position": True,
        },
    ]


def test_async_bridge_retries_transient_failure_without_new_event(monkeypatch) -> None:
    attempts = []

    def _materialize(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return {"status": "CYCLE_ADVANCE_FAILSOFT_SKIPPED"}
        return {"status": "TEST_DONE"}

    monkeypatch.setattr(
        cycle_advance,
        "_materialize_day0_extreme_updated_seed",
        _materialize,
    )
    monkeypatch.setattr(cycle_advance, "_DAY0_BRIDGE_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(cycle_advance, "_DAY0_BRIDGE_RETRY_MAX_SECONDS", 0.02)

    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="RetryCity",
        target_date="2026-07-20",
        metric="low",
        held_position=False,
    )

    assert report["status"] == "DAY0_EXTREME_BRIDGE_QUEUED"
    assert cycle_advance._wait_for_day0_materialization_bridge_idle(1.0)
    assert len(attempts) == 2
    assert cycle_advance._day0_bridge_status_retryable(
        "CYCLE_ADVANCE_FORECAST_DB_MISSING"
    ) is True
