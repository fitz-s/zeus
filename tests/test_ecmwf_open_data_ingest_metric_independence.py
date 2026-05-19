# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PIPELINE_REVIEW.md §7 + PR #190 root-cause
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody tests — ECMWFOpenDataIngest metric independence (HIGH/LOW path isolation).
# Reuse: Run when modifying ecmwf_open_data_ingest.py, _fetch_db_payload, or ensemble_client ingest dispatch.
"""Antibody tests: ECMWFOpenDataIngest metric independence.

Root cause (PIPELINE_REVIEW.md §7): PR #190 introduced a fail-closed require-both-metrics
rule in _fetch_db_payload (lines 222-234 of ecmwf_open_data_ingest.py). When LOW-OK rows
are missing (91% of LOW rows are REJECTED_BOUNDARY_AMBIGUOUS per boundary-policy §7.3),
HIGH-OK rows for the same target_date are dropped — killing all HIGH-track entries.

Fix: ECMWFOpenDataIngest(temperature_metric='high') queries only HIGH rows, with no
LOW-row dependency. ECMWFOpenDataIngest(temperature_metric='low') queries only LOW rows.
No opposite-metric substitution in either single-metric path.

Antibody contracts (sed-flip verifiable):
  A1: HIGH-only DB seed → ECMWFOpenDataIngest(metric='high').fetch() assembles successfully.
  A2: LOW-only DB seed → ECMWFOpenDataIngest(metric='low').fetch() assembles successfully.
  A3: HIGH-only DB seed → ECMWFOpenDataIngest(metric='low').fetch() raises ValueError (no substitution).
  A4: LOW-only DB seed → ECMWFOpenDataIngest(metric='high').fetch() raises ValueError (no substitution).
  A5: HIGH-only DB seed → ECMWFOpenDataIngest(metric=None).fetch() raises ValueError (combined mode drops incomplete date).

Meta-verify: sed-flip the metric-independence path back to the old `if high_vec is None or
low_vec is None: continue` and confirm A1 goes RED.  Restore fix → A1 GREEN.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


_ANCHOR = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_DB_FETCH_DATETIME = _ANCHOR - timedelta(hours=1)
_DB_FETCH_TIME = _DB_FETCH_DATETIME.strftime("%Y-%m-%dT%H:%M:%S+00:00")
_RECORDED_AT = _DB_FETCH_DATETIME.strftime("%Y-%m-%d %H:%M:%S")
_ISSUE_TIME = "2026-05-19T00:00:00+00:00"
_AVAILABLE_AT = "2026-05-19T06:00:00+00:00"
_TARGET_DATE = "2026-05-19"
_DECISION_TIME = _DB_FETCH_DATETIME + timedelta(minutes=30)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (
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
    data_version TEXT NOT NULL,
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
    source_run_id TEXT
);
"""

_INSERT_SQL = """
INSERT INTO ensemble_snapshots_v2
    (city, target_date, temperature_metric, available_at, fetch_time,
     members_json, data_version, causality_status, authority,
     recorded_at, members_unit, issue_time, source_id)
VALUES (?, ?, ?, ?, ?, ?, ?, 'OK', 'VERIFIED', ?, 'degC', ?, 'ecmwf_open_data')
"""


def _make_members(base: float = 20.0, n: int = 51) -> list[float]:
    return [base + 0.1 * i for i in range(n)]


def _make_db(tmp_path: Path, metrics: list[str]) -> Path:
    """Create a file-backed SQLite DB seeded with the requested metrics only."""
    db_path = tmp_path / f"{'_'.join(metrics)}_forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_CREATE_TABLE_SQL)
    for metric in metrics:
        base = 20.0 if metric == "high" else 10.0
        dv = (
            "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
            if metric == "high"
            else "ecmwf_opendata_mn2t3_local_calendar_day_min_v1"
        )
        conn.execute(
            _INSERT_SQL,
            (
                "Amsterdam",
                _TARGET_DATE,
                metric,
                _AVAILABLE_AT,
                _DB_FETCH_TIME,
                json.dumps(_make_members(base)),
                dv,
                _RECORDED_AT,
                _ISSUE_TIME,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _patch_db(monkeypatch, db_path: Path) -> None:
    import src.data.ecmwf_open_data_ingest as eod
    import src.state.db as state_db

    def _fake_conn(*_a, **_k):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(state_db, "get_forecasts_connection", _fake_conn)
    monkeypatch.setattr(eod, "get_forecasts_connection", _fake_conn)


@pytest.fixture
def fake_city():
    return SimpleNamespace(
        name="Amsterdam",
        timezone="Europe/Amsterdam",
        lat=52.37,
        lon=4.9,
        settlement_unit="C",
    )


# ---------------------------------------------------------------------------
# A1: HIGH-only seed → metric='high' succeeds
# ---------------------------------------------------------------------------

def test_high_only_db_high_metric_succeeds(fake_city, tmp_path, monkeypatch):
    """A1 (LIVE ENTRY BLOCKER antibody): HIGH-OK rows alone must assemble a valid bundle.

    This test catches the exact blocker: _fetch_db_payload dropped HIGH-OK dates when
    LOW-OK was missing.  With the metric-independence fix, HIGH-only ingest MUST succeed.

    Sed-flip verification: restore the old `if high_vec is None or low_vec is None: continue`
    (remove the metric-specific path) and this test goes RED (bundle is None → ValueError).
    """
    db = _make_db(tmp_path, ["high"])
    _patch_db(monkeypatch, db)

    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city, temperature_metric="high")
    bundle = ingest.fetch(_DECISION_TIME, tuple(range(24)))

    assert bundle is not None, "HIGH-only fetch returned None — cross-metric coupling still broken"
    assert bundle.source_id == "ecmwf_open_data"
    assert len(bundle.ensemble_members) == 51
    assert bundle.raw_payload["synthesised_from"] == "ensemble_snapshots_v2.ecmwf_open_data.high_only"
    assert len(bundle.raw_payload["times"]) == 24


# ---------------------------------------------------------------------------
# A2: LOW-only seed → metric='low' succeeds
# ---------------------------------------------------------------------------

def test_low_only_db_low_metric_succeeds(fake_city, tmp_path, monkeypatch):
    """A2: LOW-OK rows alone must assemble a valid bundle when metric='low'.

    Symmetric to A1 — ensures the metric-independent path works for the low track too.
    """
    db = _make_db(tmp_path, ["low"])
    _patch_db(monkeypatch, db)

    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city, temperature_metric="low")
    bundle = ingest.fetch(_DECISION_TIME, tuple(range(24)))

    assert bundle is not None, "LOW-only fetch returned None — low metric independence broken"
    assert bundle.source_id == "ecmwf_open_data"
    assert len(bundle.ensemble_members) == 51
    assert bundle.raw_payload["synthesised_from"] == "ensemble_snapshots_v2.ecmwf_open_data.low_only"
    assert len(bundle.raw_payload["times"]) == 24


# ---------------------------------------------------------------------------
# A3: HIGH-only seed → metric='low' raises (no opposite-metric substitution)
# ---------------------------------------------------------------------------

def test_high_only_db_low_metric_raises(fake_city, tmp_path, monkeypatch):
    """A3: Requesting LOW from a HIGH-only DB must raise ValueError (no substitution).

    Verifies that the metric-independence fix does NOT introduce opposite-metric
    substitution — a date with only HIGH cannot serve as LOW.
    """
    db = _make_db(tmp_path, ["high"])
    _patch_db(monkeypatch, db)

    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city, temperature_metric="low")
    with pytest.raises(ValueError, match="No VERIFIED ecmwf_open_data rows"):
        ingest.fetch(_DECISION_TIME, tuple(range(24)))


# ---------------------------------------------------------------------------
# A4: LOW-only seed → metric='high' raises (no opposite-metric substitution)
# ---------------------------------------------------------------------------

def test_low_only_db_high_metric_raises(fake_city, tmp_path, monkeypatch):
    """A4: Requesting HIGH from a LOW-only DB must raise ValueError (no substitution)."""
    db = _make_db(tmp_path, ["low"])
    _patch_db(monkeypatch, db)

    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city, temperature_metric="high")
    with pytest.raises(ValueError, match="No VERIFIED ecmwf_open_data rows"):
        ingest.fetch(_DECISION_TIME, tuple(range(24)))


# ---------------------------------------------------------------------------
# A5: HIGH-only seed → metric=None (combined) raises (incomplete date in combined mode)
# ---------------------------------------------------------------------------

def test_high_only_db_combined_mode_raises(fake_city, tmp_path, monkeypatch):
    """A5: Combined mode (metric=None) with HIGH-only DB must raise (date dropped, no rows).

    Ensures the backward-compatible combined path still enforces no-opposite-substitution.
    A date with only HIGH is dropped in combined mode because LOW is missing.
    """
    db = _make_db(tmp_path, ["high"])
    _patch_db(monkeypatch, db)

    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest

    ingest = ECMWFOpenDataIngest(city=fake_city, temperature_metric=None)
    with pytest.raises(ValueError, match="No VERIFIED ecmwf_open_data rows"):
        ingest.fetch(_DECISION_TIME, tuple(range(24)))


# ---------------------------------------------------------------------------
# A6: ensemble_client threads temperature_metric to ingest constructor
# ---------------------------------------------------------------------------

def test_ensemble_client_threads_metric_to_ingest_constructor(fake_city, tmp_path, monkeypatch):
    """A6: fetch_ensemble with temperature_metric='high' must instantiate ECMWFOpenDataIngest(metric='high').

    Verifies that ensemble_client._fetch_registered_ingest_ensemble correctly passes
    temperature_metric to the ingest class — so the metric-independence fix actually
    fires end-to-end from fetch_ensemble call.
    """
    db = _make_db(tmp_path, ["high"])
    _patch_db(monkeypatch, db)

    import src.data.ensemble_client as ec
    from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest
    from src.data.forecast_source_registry import ForecastSourceSpec

    ec._clear_cache()

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
        temperature_metric="high",
    )

    assert result is not None, (
        "fetch_ensemble with temperature_metric='high' and HIGH-only DB returned None — "
        "metric not threaded to ingest constructor"
    )
    assert result["source_id"] == "ecmwf_open_data"
    assert result["n_members"] == 51
