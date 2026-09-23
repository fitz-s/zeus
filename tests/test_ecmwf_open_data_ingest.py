# Created: 2026-05-19
# Last reused/audited: 2026-09-23
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/DESIGN_PHASE3_LIVE_ROUTING_FIX.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-09-23; last_reused=2026-09-23
# Purpose: Relationship antibody verifying ECMWFOpenDataIngest DB rows → ForecastBundle provenance.
#   Antibody 1: source_id provenance — bundle.source_id == "ecmwf_open_data" (mis-provenance was the blocker)
#   Antibody 2: 51-member count preserved end-to-end through fetch → ForecastBundle → ensemble_client
#   Antibody 3: empty DB → fetch raises ValueError (fails closed, no silent empty result)
#   Antibody 4: registry wiring — forecast_source_registry has ingest_class=ECMWFOpenDataIngest
#   Antibody 5: fetch_ensemble guard cleared — no SourceNotEnabled for entry_primary role
# Reuse: standalone pytest; uses file-backed temp SQLite (via tmp_path); no live DB required
"""Relationship antibody: ECMWFOpenDataIngest DB rows → trading-side ensemble evidence."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    opendata_source_run_revision_suffix,
)


# Anchor time so freshness window (24h) is deterministic regardless of when tests run.
_ANCHOR = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_DB_FETCH_DATETIME = _ANCHOR - timedelta(hours=1)
_DB_FETCH_TIME = _DB_FETCH_DATETIME.strftime("%Y-%m-%dT%H:%M:%S+00:00")
_RECORDED_AT = _DB_FETCH_DATETIME.strftime("%Y-%m-%d %H:%M:%S")
_ISSUE_TIME = "2026-05-19T00:00:00+00:00"
_AVAILABLE_AT = "2026-05-19T06:00:00+00:00"
_TARGET_DATE = "2026-05-19"
_DECISION_TIME = _DB_FETCH_DATETIME + timedelta(minutes=30)

_CREATE_TABLE_SQL = """
CREATE TABLE ensemble_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    physical_quantity TEXT NOT NULL DEFAULT '',
    observation_field TEXT NOT NULL DEFAULT '',
    issue_time TEXT,
    valid_time TEXT,
    available_at TEXT NOT NULL,
    fetch_time TEXT NOT NULL,
    lead_hours REAL NOT NULL DEFAULT 72.0,
    members_json TEXT NOT NULL,
    p_raw_json TEXT,
    spread REAL,
    is_bimodal INTEGER,
    model_version TEXT,
    dataset_id TEXT NOT NULL,
    training_allowed INTEGER NOT NULL DEFAULT 1,
    causality_status TEXT NOT NULL DEFAULT 'OK',
    boundary_ambiguous INTEGER NOT NULL DEFAULT 0,
    ambiguous_member_count INTEGER NOT NULL DEFAULT 0,
    manifest_hash TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    authority TEXT NOT NULL DEFAULT 'VERIFIED',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    members_unit TEXT NOT NULL DEFAULT 'degC',
    members_precision REAL,
    local_day_start_utc TEXT,
    step_horizon_hours REAL,
    unit TEXT,
    source_id TEXT NOT NULL DEFAULT 'ecmwf_open_data',
    source_transport TEXT,
    source_run_id TEXT,
    contributes_to_target_extrema INTEGER NOT NULL DEFAULT 1,
    forecast_window_attribution_status TEXT NOT NULL DEFAULT 'FULLY_INSIDE_TARGET_LOCAL_DAY'
);
"""


def _seal_current_coordinates(conn):
    from src.config import runtime_coordinate_manifest_json
    from src.data.forecast_fetch_plan import data_version_for_track
    manifest = runtime_coordinate_manifest_json()
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    for metric, track in (("high", "mx2t6_high"), ("low", "mn2t6_low")):
        data_version = data_version_for_track(track, manifest)
        conn.execute(
            "UPDATE ensemble_snapshots SET dataset_id=?, manifest_hash=?, provenance_json=?, source_run_id=? WHERE temperature_metric=?",
            (data_version, hashlib.sha256((metric + manifest).encode()).hexdigest(),
             json.dumps({"manifest_sha256": digest}),
             f"ecmwf_open_data:{track}:2026-05-19T00Z:coordsha:{digest}"
             f"{opendata_source_run_revision_suffix(data_version)}", metric),
        )


def _make_members(base: float = 20.0, n: int = 51) -> list[float]:
    return [base + 0.1 * i for i in range(n)]


@pytest.fixture
def fake_city():
    return SimpleNamespace(
        name="Amsterdam",
        timezone="Europe/Amsterdam",
        lat=52.37,
        lon=4.9,
        settlement_unit="C",
    )


@pytest.fixture
def staged_forecasts_db(tmp_path: Path, monkeypatch):
    """File-backed temp SQLite DB (tmp_path) with VERIFIED ecmwf_open_data high+low rows for Amsterdam."""
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_CREATE_TABLE_SQL)

    high_members = _make_members(20.0)
    low_members = _make_members(10.0)

    for metric, data_version, members in (
        ("high", ECMWF_OPENDATA_HIGH_DATA_VERSION, high_members),
        ("low", ECMWF_OPENDATA_LOW_DATA_VERSION, low_members),
    ):
        conn.execute(
            """INSERT INTO ensemble_snapshots
               (city, target_date, temperature_metric, available_at, fetch_time,
                members_json, dataset_id, causality_status, authority,
                recorded_at, members_unit, issue_time, source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'OK', 'VERIFIED', ?, 'degC', ?, 'ecmwf_open_data')""",
            (
                "Amsterdam",
                _TARGET_DATE,
                metric,
                _AVAILABLE_AT,
                _DB_FETCH_TIME,
                json.dumps(members),
                data_version,
                _RECORDED_AT,
                _ISSUE_TIME,
            ),
        )
    _seal_current_coordinates(conn)
    conn.commit()
    conn.close()

    import src.data.ecmwf_open_data_ingest as eod
    import src.state.db as state_db

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
    monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)
    return db_path


@pytest.fixture
def empty_forecasts_db(tmp_path: Path, monkeypatch):
    """File-backed temp SQLite DB (tmp_path) with the table but zero rows."""
    db_path = tmp_path / "empty-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_CREATE_TABLE_SQL)
    _seal_current_coordinates(conn)
    conn.commit()
    conn.close()

    import src.data.ecmwf_open_data_ingest as eod
    import src.state.db as state_db

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
    monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)
    return db_path


# ---------------------------------------------------------------------------
# Antibody 1: source_id provenance
# ---------------------------------------------------------------------------

def test_fetch_bundle_source_id_is_ecmwf_open_data(fake_city, staged_forecasts_db):
    """Provenance antibody: returned bundle MUST carry source_id='ecmwf_open_data'.

    This is the primary blocker — mis-provenance (labelling Open-Meteo data as
    ecmwf_open_data) was explicitly caught by the guard.  The correct fix must
    tag data from the raw DB rows with the correct source_id.
    """
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city)
    bundle = ingest.fetch(_DECISION_TIME, tuple(range(24)))

    assert bundle.source_id == "ecmwf_open_data", (
        f"provenance broken: bundle.source_id={bundle.source_id!r}"
    )
    assert bundle.authority_tier == "FORECAST"
    assert bundle.raw_payload["source_id"] == "ecmwf_open_data"


# ---------------------------------------------------------------------------
# Antibody 2: 51-member count preserved
# ---------------------------------------------------------------------------

def test_fetch_bundle_preserves_51_members(fake_city, staged_forecasts_db):
    """51-member count must survive DB → ForecastBundle → ensemble_members tuple."""
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city)
    bundle = ingest.fetch(_DECISION_TIME, tuple(range(24)))

    assert bundle is not None
    # ensemble_members is tuple-of-rows (51 rows × n_hours)
    assert len(bundle.ensemble_members) == 51, (
        f"member count broken: {len(bundle.ensemble_members)} != 51"
    )
    raw = bundle.raw_payload
    assert len(raw["members_hourly"]) == 51
    # 1 target_date × 24 hours
    assert len(raw["times"]) == 24


# ---------------------------------------------------------------------------
# Antibody 3: empty DB → fails closed (raises, not silent None)
# ---------------------------------------------------------------------------

def test_fetch_raises_when_no_rows(fake_city, empty_forecasts_db):
    """Guard closed: fetch() on empty DB raises ValueError, never silently returns None."""
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city)
    with pytest.raises(ValueError, match="No VERIFIED ecmwf_open_data rows"):
        ingest.fetch(_DECISION_TIME, tuple(range(24)))


# ---------------------------------------------------------------------------
# Antibody 4: registry wiring
# ---------------------------------------------------------------------------

def test_registry_has_ingest_class_for_ecmwf_open_data():
    """Registry wiring: ecmwf_open_data spec must carry ingest_class=ECMWFOpenDataIngest.

    This is the structural fix for the LIVE TRADE BLOCKER.  The guard in
    ensemble_client.fetch_ensemble checks ``source_spec.ingest_class is None``
    and raises SourceNotEnabled when None.
    """
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    from src.data.forecast_source_registry import get_source

    spec = get_source("ecmwf_open_data")
    assert spec.ingest_class is not None, (
        "ecmwf_open_data has no ingest_class — the guard in ensemble_client will block all fetches"
    )
    assert spec.ingest_class is ECMWFOpenDataIngest


# ---------------------------------------------------------------------------
# Antibody 5: fetch_ensemble guard cleared (no SourceNotEnabled)
# ---------------------------------------------------------------------------

def test_fetch_ensemble_ecmwf_open_data_clears_guard(fake_city, staged_forecasts_db, monkeypatch):
    """Cross-module: fetch_ensemble('ecmwf_ifs025', role='entry_primary') must not raise.

    Before this fix: SourceNotEnabled('ecmwf_open_data has no ingest_class').
    After this fix: returns a dict with source_id='ecmwf_open_data' and 51 members.
    """
    import src.data.ensemble_client as ec
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    from src.data.forecast_source_registry import ForecastSourceSpec

    ec._clear_cache()

    # Monkeypatch gate_source/gate_source_role so no operator-decision artifact is needed.
    monkeypatch.setattr(
        ec,
        "gate_source",
        lambda _source_id: ForecastSourceSpec(
            source_id="ecmwf_open_data",
            tier="secondary",
            kind="scheduled_collector",
            model_name="ecmwf_open_data",
            ingest_class=ECMWFOpenDataIngest,
            allowed_roles=("entry_primary", "monitor_fallback", "diagnostic"),
            degradation_level="OK",
        ),
    )
    monkeypatch.setattr(ec, "gate_source_role", lambda _spec, _role: None)

    # Freeze datetime.now() in ensemble_client to _ANCHOR for deterministic freshness.
    from datetime import datetime as _real_dt
    monkeypatch.setattr(ec, "datetime", type("_FakeDT", (), {
        "now": staticmethod(lambda tz=None: _ANCHOR.replace(tzinfo=tz) if tz else _ANCHOR),
        "__getattr__": lambda self, name: getattr(_real_dt, name),
    })())

    result = ec.fetch_ensemble(
        fake_city,
        forecast_days=1,
        model="ecmwf_ifs025",
        role="entry_primary",
    )

    assert result is not None, "fetch_ensemble returned None — DB fetch silently failed"
    assert result["source_id"] == "ecmwf_open_data", (
        f"source_id mismatch: {result['source_id']!r}"
    )
    assert result["n_members"] == 51, f"member count wrong: {result['n_members']}"
    members_arr = result["members_hourly"]
    assert hasattr(members_arr, "shape")
    assert members_arr.shape[0] == 51


@pytest.mark.parametrize("metric", ["high", "low"])
def test_day0_fetch_selects_current_coordinates_and_invalidates_prior_profile_cache(
    fake_city, staged_forecasts_db, monkeypatch, metric,
):
    import src.config as config
    import src.data.ecmwf_open_data_ingest as ingest_module
    import src.data.ensemble_client as client
    from src.data.forecast_fetch_plan import data_version_for_track
    from zoneinfo import ZoneInfo

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _ANCHOR if tz is None else _ANCHOR.astimezone(tz)

    monkeypatch.setattr(client, "datetime", FrozenDatetime)
    client._clear_cache()
    manifest_a = config.runtime_coordinate_manifest_json()
    altered = json.loads(manifest_a)
    altered["cities"][0]["lat"] += 0.001
    manifest_b = json.dumps(altered, sort_keys=True, separators=(",", ":"))
    current = {"manifest": manifest_a}
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: current["manifest"])
    monkeypatch.setattr(ingest_module, "runtime_coordinate_manifest_json", lambda: current["manifest"])
    track = "mx2t6_high" if metric == "high" else "mn2t6_low"
    base_value = 20.0 if metric == "high" else 10.0
    digest_b = hashlib.sha256(manifest_b.encode()).hexdigest()
    data_version_b = data_version_for_track(track, manifest_b)
    with sqlite3.connect(staged_forecasts_db) as conn:
        # The other profile arrives later, but must not win the current query.
        conn.execute(
            """INSERT INTO ensemble_snapshots (
                city,target_date,temperature_metric,issue_time,available_at,fetch_time,
                recorded_at,members_json,dataset_id,manifest_hash,source_run_id,provenance_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fake_city.name, _TARGET_DATE, metric, _ISSUE_TIME, _AVAILABLE_AT,
             _DB_FETCH_TIME, _RECORDED_AT, json.dumps(_make_members(base_value + 30)),
             data_version_for_track(track, manifest_b), digest_b,
             f"ecmwf_open_data:{track}:2026-05-19T00Z:coordsha:{digest_b}"
             f"{opendata_source_run_revision_suffix(data_version_b)}",
             json.dumps({"manifest_sha256": digest_b})),
        )
    first = client.fetch_ensemble(fake_city, forecast_days=1, model="ecmwf_ifs025",
                                  role="entry_primary", temperature_metric=metric)
    assert first["members_hourly"][0, 0] == base_value
    assert first["data_version"] == data_version_for_track(track, manifest_a)
    identity = first["snapshot_identity_by_metric_target_date"][metric][_TARGET_DATE]
    assert identity["dataset_id"] == first["data_version"]
    assert identity["source_run_id"].endswith(
        ":coordsha:" + first["coordinate_manifest_sha"]
        + opendata_source_run_revision_suffix(first["data_version"])
    )
    assert all(datetime.fromisoformat(t).astimezone(ZoneInfo(fake_city.timezone)).date().isoformat() == _TARGET_DATE
               for t in first["times"])
    current["manifest"] = manifest_b
    second = client.fetch_ensemble(fake_city, forecast_days=1, model="ecmwf_ifs025",
                                   role="entry_primary", temperature_metric=metric)
    assert second["members_hourly"][0, 0] == base_value + 30
    assert second["coordinate_manifest_sha"] == digest_b
    assert second["data_version"] != first["data_version"]
    client._clear_cache()


@pytest.mark.parametrize("bad_field", ["dataset_id", "provenance_json", "source_run_id", "available_at", "fetch_time", "recorded_at"])
def test_day0_ingest_rejects_unbound_or_future_snapshot(fake_city, staged_forecasts_db, bad_field):
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    value = "2026-05-20T00:00:00+00:00" if bad_field in {"available_at", "fetch_time", "recorded_at"} else "old-or-mismatched"
    with sqlite3.connect(staged_forecasts_db) as conn:
        conn.execute(f"UPDATE ensemble_snapshots SET {bad_field}=?", (value,))
    with pytest.raises(ValueError, match="No VERIFIED"):
        ECMWFOpenDataIngest(city=fake_city, temperature_metric="high").fetch(_ANCHOR, range(24))


@pytest.mark.parametrize("target,expected_hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_metric_grid_preserves_local_day_across_dst(fake_city, staged_forecasts_db, target, expected_hours):
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    from zoneinfo import ZoneInfo
    now = datetime.fromisoformat(target).replace(hour=12, tzinfo=timezone.utc)
    with sqlite3.connect(staged_forecasts_db) as conn:
        conn.execute(
            """UPDATE ensemble_snapshots SET target_date=?, issue_time=?, available_at=?,
               recorded_at=?, fetch_time=?, source_run_id=replace(source_run_id, '2026-05-19', ?)""",
            (target, now.replace(hour=0).isoformat(), now.replace(hour=6).isoformat(),
             now.replace(hour=11).isoformat(), now.replace(hour=11).isoformat(), target),
        )
    bundle = ECMWFOpenDataIngest(city=fake_city, temperature_metric="high").fetch(now, range(24))
    times = [datetime.fromisoformat(value).astimezone(ZoneInfo(fake_city.timezone))
             for value in bundle.raw_payload["times"]]
    assert len(times) == expected_hours
    assert {value.date().isoformat() for value in times} == {target}
    assert all(value == _make_members(20.0)[0] for value in bundle.ensemble_members[0])


@pytest.mark.parametrize("metric", ["high", "low"])
def test_combined_bundle_preserves_and_validates_each_metric_identity(fake_city, staged_forecasts_db, metric):
    from dataclasses import replace
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    from src.data.ensemble_client import _parse_ingest_bundle
    bundle = ECMWFOpenDataIngest(city=fake_city).fetch(_ANCHOR, range(24))
    parsed = _parse_ingest_bundle(bundle, model="ecmwf_ifs025", fetch_time=_ANCHOR, role="diagnostic")
    assert set(parsed["data_version_by_metric"]) == {"high", "low"}
    raw = json.loads(json.dumps(bundle.raw_payload))
    raw["snapshot_identity_by_metric_target_date"][metric][_TARGET_DATE]["source_run_id"] = "legacy-unbound-run"
    with pytest.raises(ValueError, match="source snapshot identity mismatch"):
        _parse_ingest_bundle(replace(bundle, raw_payload=raw), model="ecmwf_ifs025",
                             fetch_time=_ANCHOR, role="diagnostic")
