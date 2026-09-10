# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice A3 (snapshot stamping fail-closed +
#                  _normalize_temperature_metric fail-closed root fix)
"""Slice A3 relationship + function tests.

PR #19 finding F7: snapshot stamping silently defaults missing identity
to `high`, hiding LOW writers and malformed upstream as HIGH in the
canonical ensemble_snapshots table that calibration_pairs replay reads
back.

A3 closes this in two places:

1. _normalize_temperature_metric (single legal str→MetricIdentity
   conversion point) was the silent-default ROOT — None / "" / garbage
   would all silently become HIGH. Now raises ValueError on invalid input.

2. _store_ens_snapshot writer seam now refuses to INSERT when
   ens.temperature_metric is missing or malformed, rather than stamping
   the row 'high'.

L91 MarketCandidate.temperature_metric default = "high" is intentionally
kept (legitimate explicit default for callers; high blast radius to
remove). A3b future packet may revisit if needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.evaluator import _normalize_temperature_metric, _store_ens_snapshot
from src.types.metric_identity import MetricIdentity


# -----------------------------------------------------------------------------
# Normalizer fail-closed: invalid inputs raise instead of silently HIGHing
# -----------------------------------------------------------------------------


def test_normalize_temperature_metric_raises_on_none():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric(None)


def test_normalize_temperature_metric_raises_on_empty_string():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("")


def test_normalize_temperature_metric_raises_on_whitespace_only():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("   ")


def test_normalize_temperature_metric_raises_on_garbage():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("medium")
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("hgih")  # typo


# -----------------------------------------------------------------------------
# Behavior preservation: valid inputs still work, case + whitespace tolerant
# -----------------------------------------------------------------------------


def test_normalize_temperature_metric_accepts_high_low():
    high = _normalize_temperature_metric("high")
    low = _normalize_temperature_metric("low")
    assert isinstance(high, MetricIdentity)
    assert isinstance(low, MetricIdentity)
    assert high.temperature_metric == "high"
    assert low.temperature_metric == "low"


def test_normalize_temperature_metric_case_and_whitespace_tolerant():
    assert _normalize_temperature_metric("HIGH").temperature_metric == "high"
    assert _normalize_temperature_metric(" Low ").temperature_metric == "low"
    assert _normalize_temperature_metric("HiGh").temperature_metric == "high"


# -----------------------------------------------------------------------------
# Snapshot writer fail-closed: missing/malformed ens.temperature_metric raises
# -----------------------------------------------------------------------------


def _fake_city(name: str = "NYC"):
    """Minimal city stand-in (only .name attribute is referenced)."""
    return SimpleNamespace(name=name)


def _fake_ens_with_metric(metric_value: str | None = "high"):
    """Build a minimal ENS object the writer reads from.

    Mirrors the production shape: ens.temperature_metric is a MetricIdentity,
    plus member_extrema (numpy array), spread_float(), is_bimodal(), etc.
    The writer references all of these; we only need it to get past the
    metric assertion to test the fail-closed gate.
    """
    if metric_value is None:
        metric = None
    elif metric_value == "missing_inner":
        metric = SimpleNamespace()  # has no .temperature_metric attr
    else:
        metric = MetricIdentity.from_raw(metric_value)
    import numpy as np
    return SimpleNamespace(
        temperature_metric=metric,
        member_extrema=np.array([72.0, 73.0]),
        spread_float=lambda: 1.0,
        is_bimodal=lambda: 0,
    )


def _snapshot_row_count(conn, city: str = "NYC") -> int:
    """Count rows currently in ensemble_snapshots for a city (v1.F20: legacy removed)."""
    import sqlite3
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM ensemble_snapshots WHERE city = ?", (city,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        # Only swallow "no such table" — other errors (SQL typo, schema mismatch)
        # should propagate so they don't mask real test failures.
        return 0


def _make_test_conn():
    """Return an in-memory connection with world + v2 forecast schema tables."""
    import sqlite3
    from src.state.db import init_schema
    from src.state.schema.v2_schema import _create_ensemble_snapshots
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    # K1 split: ensemble_snapshots lives in zeus-forecasts.db; create it in
    # the monolithic test conn so _store_ens_snapshot has a v2 target.
    _create_ensemble_snapshots(conn)
    return conn


def test_store_ens_snapshot_does_not_write_when_metric_missing(caplog):
    """ens with temperature_metric=None: writer must NOT INSERT a HIGH-stamped row.

    The writer is best-effort and wrapped in try/except (so snapshot failure
    doesn't crash the evaluation flow). The A3 antibody asserts the missing
    -metric branch raises internally → row count stays zero AND a warning
    surfaces in the log. Pre-A3 behavior would silently INSERT a row stamped
    `temperature_metric='high'`.
    """
    import logging
    conn = _make_test_conn()
    ens = _fake_ens_with_metric(metric_value=None)
    ens_result = {
        "fetch_time": "2026-04-15T12:00:00Z",
        "model": "ecmwf_ens",
        "issue_time": "2026-04-15T00:00:00Z",
    }
    assert _snapshot_row_count(conn) == 0
    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        _store_ens_snapshot(conn, _fake_city(), "2026-04-16", ens, ens_result)
    assert _snapshot_row_count(conn) == 0, (
        "snapshot must NOT be written when ens.temperature_metric is missing; "
        "pre-A3 would have silently stamped 'high' here."
    )
    assert any(
        "requires ens.temperature_metric" in rec.message
        for rec in caplog.records
    ), "fail-closed warning must surface in evaluator logs"


def test_store_ens_snapshot_does_not_write_when_inner_metric_missing(caplog):
    """ens.temperature_metric present but lacks .temperature_metric attr: still fail-closed."""
    import logging
    conn = _make_test_conn()
    ens = _fake_ens_with_metric(metric_value="missing_inner")
    ens_result = {
        "fetch_time": "2026-04-15T12:00:00Z",
        "model": "ecmwf_ens",
        "issue_time": "2026-04-15T00:00:00Z",
    }
    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        _store_ens_snapshot(conn, _fake_city(), "2026-04-16", ens, ens_result)
    assert _snapshot_row_count(conn) == 0, (
        "snapshot must NOT be written when ens.temperature_metric is malformed."
    )
    assert any(
        "requires ens.temperature_metric" in rec.message
        for rec in caplog.records
    )


def _coordinate_snapshot_fixture(conn):
    """Seed one canonical Open Data row and its ingest identity envelope."""
    import numpy as np

    city = SimpleNamespace(
        name="NYC",
        settlement_unit="F",
        timezone="America/New_York",
    )
    target_date = "2026-04-16"
    import hashlib
    from src.config import runtime_coordinate_manifest_json
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    dataset_id = (
        "ecmwf_opendata_mx2t3_local_calendar_day_max__coordsha_"
        + manifest_sha
    )
    payload_hash = hashlib.sha256(b"canonical payload provenance").hexdigest()
    issue_time = "2026-04-15T00:00:00+00:00"
    available_at = "2026-04-15T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, available_at, fetch_time,
            lead_hours, members_json, model_version, dataset_id, source_id,
            source_run_id, manifest_hash, members_unit, provenance_json
        ) VALUES (?, ?, 'high', 'mx2t3_local_calendar_day_max', 'high_temp',
                  ?, ?, ?, 0, ?, 'ecmwf_ens', ?, 'ecmwf_open_data',
                  ?, ?, ?, ?)
        """,
        (
            city.name,
            target_date,
            issue_time,
            available_at,
            available_at,
            json.dumps([72.0, 73.0]),
            dataset_id,
            "run-1",
            payload_hash,
            "degF",
            json.dumps({"manifest_sha256": manifest_sha}),
        ),
    )
    conn.commit()
    snapshot_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    ens = _fake_ens_with_metric("high")
    ens_result = {
        "source_id": "ecmwf_open_data",
        "coordinate_manifest_sha": manifest_sha,
        "snapshot_identity_by_metric_target_date": {
            "high": {
                target_date: {
                    "snapshot_id": snapshot_id,
                    "dataset_id": dataset_id,
                    "source_run_id": "run-1",
                    "manifest_hash": payload_hash,
                    "coordinate_manifest_sha": manifest_sha,
                    "issue_time": issue_time,
                    "available_at": available_at,
                }
            }
        },
        "members_unit": "degF",
        "model": "ecmwf_ens",
        "fetch_time": available_at,
        "issue_time": issue_time,
    }
    assert np.array_equal(ens.member_extrema, np.array([72.0, 73.0]))
    return city, target_date, ens, ens_result, snapshot_id


def test_coordinate_open_data_reuses_canonical_snapshot_without_writing():
    conn = _make_test_conn()
    city, target_date, ens, ens_result, snapshot_id = _coordinate_snapshot_fixture(conn)
    before = conn.execute(
        "SELECT COUNT(*), MIN(members_json), MAX(dataset_id) FROM ensemble_snapshots"
    ).fetchone()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    returned = _store_ens_snapshot(conn, city, target_date, ens, ens_result)

    conn.set_trace_callback(None)
    after = conn.execute(
        "SELECT COUNT(*), MIN(members_json), MAX(dataset_id) FROM ensemble_snapshots"
    ).fetchone()
    assert returned == str(snapshot_id)
    assert tuple(after) == tuple(before)
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in statements
    )


@pytest.mark.parametrize("mismatch", ["dataset", "source", "members", "manifest"])
def test_coordinate_open_data_identity_mismatch_returns_empty_without_write(
    mismatch,
    caplog,
):
    import logging
    import numpy as np

    conn = _make_test_conn()
    city, target_date, ens, ens_result, _snapshot_id = _coordinate_snapshot_fixture(conn)
    if mismatch == "dataset":
        ens_result["snapshot_identity_by_metric_target_date"]["high"][target_date][
            "dataset_id"
        ] = "ecmwf_opendata_mx2t3_local_calendar_day_max"
    elif mismatch == "source":
        ens_result["source_id"] = "openmeteo_ecmwf_ifs_9km"
    elif mismatch == "members":
        ens.member_extrema = np.array([72.0, 74.0])
    else:
        ens_result["coordinate_manifest_sha"] = "b" * 64

    before = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]
    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        returned = _store_ens_snapshot(conn, city, target_date, ens, ens_result)
    after = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]

    assert returned == ""
    assert after == before
    assert any("coordinate-bound ENS" in rec.message for rec in caplog.records)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("city", "Boston"),
        ("target_date", "2026-04-17"),
        ("temperature_metric", "low"),
        ("source_id", "openmeteo_ecmwf_ifs_9km"),
        ("source_run_id", "run-2"),
        ("manifest_hash", "b" * 64),
        ("members_unit", "degC"),
    ],
)
def test_coordinate_open_data_canonical_row_scope_mismatch_is_read_only(
    field_name,
    bad_value,
):
    conn = _make_test_conn()
    city, target_date, ens, ens_result, snapshot_id = _coordinate_snapshot_fixture(conn)
    conn.execute(
        f"UPDATE ensemble_snapshots SET {field_name} = ? WHERE snapshot_id = ?",
        (bad_value, snapshot_id),
    )
    conn.commit()
    before = conn.execute(
        "SELECT COUNT(*), MIN(members_json), MAX(dataset_id) FROM ensemble_snapshots"
    ).fetchone()

    returned = _store_ens_snapshot(conn, city, target_date, ens, ens_result)

    after = conn.execute(
        "SELECT COUNT(*), MIN(members_json), MAX(dataset_id) FROM ensemble_snapshots"
    ).fetchone()
    assert returned == ""
    assert tuple(after) == tuple(before)


# -----------------------------------------------------------------------------
# No-silent-HIGH antibody: previously, a missing metric would have been
# silently stamped HIGH. Now, the writer refuses rather than mis-stamping.
# This test pins the behavior change so a future "convenience" patch that
# restores the silent fallback would fail loudly.
# -----------------------------------------------------------------------------


def test_no_silent_high_default_path_remains_in_normalizer():
    """Belt-and-braces: confirm the normalizer's body has no silent HIGH path.

    Inspects function source so a future regression that re-introduces the
    `raw = "low" if text == "low" else "high"` pattern will fail this test
    without requiring runtime invocation.
    """
    import inspect
    from src.engine import evaluator
    src = inspect.getsource(evaluator._normalize_temperature_metric)
    assert "raise ValueError" in src, (
        "_normalize_temperature_metric must raise on invalid input; "
        "the silent-HIGH fallback is a known-bad antipattern (PR #19 F7)."
    )
    # Specifically reject the prior silent pattern.
    assert 'raw = "low" if text == "low" else "high"' not in src, (
        "_normalize_temperature_metric must NOT silently default unknown "
        "inputs to 'high'. See slice A3 commit message for context."
    )


@pytest.mark.parametrize("record_hash", [None, "", "A" * 64, "not-a-hash"])
def test_coordinate_snapshot_reuse_requires_record_hash_even_when_both_sides_match(record_hash):
    conn = _make_test_conn()
    city, target, ens, result, snapshot_id = _coordinate_snapshot_fixture(conn)
    result["snapshot_identity_by_metric_target_date"]["high"][target]["manifest_hash"] = record_hash
    conn.execute("UPDATE ensemble_snapshots SET manifest_hash=? WHERE snapshot_id=?", (record_hash, snapshot_id))
    conn.commit()
    before = tuple(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone())
    assert _store_ens_snapshot(conn, city, target, ens, result) == ""
    assert tuple(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()) == before
