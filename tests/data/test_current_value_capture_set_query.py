# Created: 2026-10-01
# Last reused/audited: 2026-10-03
# Authority basis: live auction prepare budget (45 s cut); loader cost is round-trips, not rows.
"""One request-family capture read per snapshot serves every raw row exactly as the per-row query."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from src.data import replacement_current_value_serving as serving

RECEIPT = "openmeteo_single_model_http_capture_receipt_v1"
BODY = "openmeteo_single_model_entity_body_v1"
SCHEMA = """CREATE TABLE raw_forecast_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, product_id TEXT NOT NULL,
    data_version TEXT NOT NULL, source_cycle_time TEXT NOT NULL, source_available_at TEXT NOT NULL,
    captured_at TEXT NOT NULL, artifact_path TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
    request_url TEXT, request_params_json TEXT NOT NULL DEFAULT '{}', artifact_metadata_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL)"""
CYCLE = "2026-10-01T00:00:00+00:00"


def _insert(conn, *, source="icon_global_single_runs", cycle=CYCLE, kind=RECEIPT, params, n=[0]):
    n[0] += 1
    return conn.execute(
        "INSERT INTO raw_forecast_artifacts (source_id, product_id, data_version, source_cycle_time, source_available_at,"
        " captured_at, artifact_path, sha256, byte_size, request_url, request_params_json, recorded_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (source, source.replace("_single_runs", "::single_runs"), kind, cycle, cycle, cycle, f"/x/{n[0]}", f"{n[0]:064x}", 1,
         "https://example.invalid", params if isinstance(params, str) else json.dumps(params), cycle),
    ).lastrowid


def _row(*, lat, lon, tz, artifact_id=None, source="icon_global_single_runs", cycle=CYCLE, legacy=False):
    return {"artifact_id": artifact_id, "source_id": source, "product_id": source.replace("_single_runs", "::single_runs"),
            "source_cycle_time": cycle, "latitude_requested": lat, "longitude_requested": lon, "timezone_requested": tz,
            "elevation_param": "requested" if legacy else "nan", "downscaling_policy": "none", "endpoint_mode": "single_runs"}


def _catalog(conn):
    batched = {"latitude": "40.7,51.5,-33.9", "longitude": "-74.0,-0.1,18.4",
               "timezone": "America/New_York,Europe/London,Africa/Johannesburg"}
    ids = {
        "batched": _insert(conn, params=batched),
        "single_ny": _insert(conn, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"}),
        "int_coords": _insert(conn, params={"latitude": 40, "longitude": -74, "timezone": "America/New_York"}),
        "string_coords": _insert(conn, params={"latitude": "40.70", "longitude": "-74.0", "timezone": "America/New_York"}),
        "legacy_body": _insert(conn, kind=BODY, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"}),
        "other_cycle": _insert(conn, cycle="2026-10-01T06:00:00+00:00", params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"}),
        "other_source": _insert(conn, source="gfs_global_single_runs", params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"}),
        "malformed": _insert(conn, params="not json"),
        "tz_mismatch": _insert(conn, params={"latitude": 40.7, "longitude": -74.0, "timezone": "UTC"}),
        "null_lat": _insert(conn, params={"latitude": None, "longitude": -74.0, "timezone": "America/New_York"}),
    }
    conn.commit()
    return ids


ROWS = [
    _row(lat=40.7, lon=-74.0, tz="America/New_York"),
    _row(lat=40.7, lon=-74.0, tz="America/New_York", legacy=True),
    _row(lat=51.5, lon=-0.1, tz="Europe/London"),
    _row(lat=40, lon=-74, tz="America/New_York"),
    _row(lat=40.0, lon=-74.0, tz="America/New_York"),
    _row(lat=-33.9, lon=18.4, tz="Africa/Johannesburg", artifact_id=2),
    _row(lat=1.0, lon=1.0, tz="UTC", artifact_id=7),
    _row(lat=40.7, lon=-74.0, tz="America/New_York", cycle="2026-10-01T06:00:00+00:00"),
    _row(lat=40.7, lon=-74.0, tz="America/New_York", source="gfs_global_single_runs"),
    _row(lat=40.7, lon=-74.0, tz="UTC"),
    _row(lat="40.7", lon=-74.0, tz="America/New_York"),  # non-numeric binding keeps SQLite affinity
    _row(lat=0.0, lon=0.0, tz="UTC", artifact_id=999),  # own id absent
]


def _ids(candidates):
    return [item["artifact_id"] for item in candidates]


def _per_row(conn, row):
    legacy = (row.get("artifact_id") is None and row.get("elevation_param") == "requested"
              and row.get("downscaling_policy") == "none" and row.get("endpoint_mode") == "single_runs")
    return list(serving._physical_artifact_candidates_by_row(conn, row, legacy=legacy, deadline=time.monotonic() + 30))


@pytest.mark.parametrize("snapshot", [True, False])
def test_set_query_returns_each_rows_per_row_candidates(tmp_path, snapshot):
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    _catalog(conn)
    reader = sqlite3.connect(tmp_path / "f.db")
    if snapshot:
        reader.execute("BEGIN")
    for row in ROWS:
        expected = _per_row(reader, row)
        actual = list(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30))
        assert actual == expected, row
    # Not vacuous: the batched request serves its NY cell together with the
    # single, REAL-cast text ("40.70") and legacy-body captures of that cell;
    # the integer binding matches the REAL-cast integer request.
    assert _ids(serving._physical_artifact_candidates(reader, ROWS[1], deadline=time.monotonic() + 30)) == [5, 4, 2, 1]
    assert _ids(serving._physical_artifact_candidates(reader, ROWS[3], deadline=time.monotonic() + 30)) == [3]
    assert _ids(serving._physical_artifact_candidates(reader, ROWS[6], deadline=time.monotonic() + 30)) == [7]


def test_set_query_reads_each_family_once_per_snapshot_and_sees_new_commits(tmp_path):
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    _catalog(conn)
    reader = sqlite3.connect(tmp_path / "f.db")
    statements = []
    reader.set_trace_callback(statements.append)
    reader.execute("BEGIN")
    for _ in range(3):
        for row in ROWS[:6]:
            list(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30))
    family_reads = [sql for sql in statements if "FROM raw_forecast_artifacts a" in sql and "a.source_id=" in sql]
    assert len(family_reads) == 2 * 2  # (icon cycle, legacy 0/1) x (identity, cells), not x rows x rounds
    reader.rollback()
    added = _insert(conn, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"})
    conn.commit()
    row = ROWS[0]
    assert _ids(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30))[0] == added
    assert list(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30)) == _per_row(reader, row)


def test_open_snapshot_does_not_see_later_commits(tmp_path):
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    _catalog(conn)
    reader = sqlite3.connect(tmp_path / "f.db")
    reader.execute("BEGIN")
    before = list(serving._physical_artifact_candidates(reader, ROWS[0], deadline=time.monotonic() + 30))
    _insert(conn, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"})
    conn.commit()
    assert list(serving._physical_artifact_candidates(reader, ROWS[0], deadline=time.monotonic() + 30)) == before == _per_row(reader, ROWS[0])


def test_a_connection_that_wrote_is_never_served_from_the_memo(tmp_path):
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    _catalog(conn)
    conn.execute("BEGIN")
    list(serving._physical_artifact_candidates(conn, ROWS[0], deadline=time.monotonic() + 30))
    added = _insert(conn, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"})
    assert _ids(serving._physical_artifact_candidates(conn, ROWS[0], deadline=time.monotonic() + 30))[0] == added
    conn.rollback()
    assert added not in _ids(serving._physical_artifact_candidates(conn, ROWS[0], deadline=time.monotonic() + 30))


def test_expired_scan_budget_still_fails_closed(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    _catalog(conn)
    with pytest.raises(serving.CurrentValueServingReadUnavailable, match="scan_budget_exceeded"):
        list(serving._physical_artifact_candidates(conn, ROWS[0], deadline=time.monotonic() - 1))


def _previous_runs_row(target_date: str, hourly: str, *, artifact_id: int) -> dict:
    params = {"cell_selection": "land", "start_date": target_date, "end_date": target_date, "hourly": hourly,
              "latitude": 29.712254, "longitude": 106.651895, "models": "icon_global", "timezone": "Asia/Shanghai"}
    return {"artifact_id": artifact_id, "source_id": "icon_previous_runs", "product_id": "icon_global::previous_runs",
            "source_cycle_time": CYCLE, "latitude_requested": 29.712254, "longitude_requested": 106.651895,
            "timezone_requested": "Asia/Shanghai", "elevation_param": "nan", "downscaling_policy": "none",
            "endpoint_mode": "previous_runs", "request_params_json": json.dumps(params, separators=(",", ":"))}


@pytest.mark.parametrize("snapshot", [True, False])
def test_a_row_cites_only_captures_of_its_own_target_request(tmp_path, snapshot):
    """Live 2026-10-02: a 10-04 previous_day2 capture of the same issued cycle and
    cell landed at 17:07Z and became the 10-02 lead-0 row's latest capture, so the
    row failed its variable proof and the held Chongqing family lost two providers."""
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    cell = {"cell_selection": "land", "latitude": 29.712254, "longitude": 106.651895,
            "models": "icon_global", "timezone": "Asia/Shanghai"}
    own = _insert(conn, source="icon_previous_runs", kind=BODY,
                  params={**cell, "start_date": "2026-10-02", "end_date": "2026-10-02", "hourly": "temperature_2m"})
    other_target = _insert(conn, source="icon_previous_runs", kind=RECEIPT,
                           params={**cell, "start_date": "2026-10-04", "end_date": "2026-10-04",
                                   "hourly": "temperature_2m_previous_day2"})
    conn.execute("UPDATE raw_forecast_artifacts SET product_id='icon_global::previous_runs'")
    conn.commit()
    reader = sqlite3.connect(tmp_path / "f.db")
    if snapshot:
        reader.execute("BEGIN")
    row = _previous_runs_row("2026-10-02", "temperature_2m", artifact_id=own)
    later = _previous_runs_row("2026-10-04", "temperature_2m_previous_day2", artifact_id=None)
    # The newer, other-target receipt shares (source, product, cycle, cell) yet
    # is never a candidate for the 10-02 row; it remains one for its own row.
    assert _ids(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30)) == [own]
    assert _ids(serving._physical_artifact_candidates(reader, later, deadline=time.monotonic() + 30)) == [other_target]
    for item in (row, later):
        assert list(serving._physical_artifact_candidates(reader, item, deadline=time.monotonic() + 30)) == _per_row(reader, item)


def test_a_single_target_family_is_unchanged(tmp_path):
    """Every catalog capture names no date window; a row with no window keeps the
    exact candidate list it had before the target key existed."""
    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(SCHEMA)
    _catalog(conn)
    reader = sqlite3.connect(tmp_path / "f.db")
    row = {**ROWS[0], "request_params_json": json.dumps({"latitude": 40.7, "longitude": -74.0})}
    assert _ids(serving._physical_artifact_candidates(reader, row, deadline=time.monotonic() + 30)) == \
        _ids(serving._physical_artifact_candidates(reader, ROWS[0], deadline=time.monotonic() + 30)) == [4, 2, 1]


def _physical_pass_reader(tmp_path, *, factory=sqlite3.Connection):
    path = tmp_path / "physical-pass.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(SCHEMA)
    _catalog(writer)
    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True, factory=factory)
    raw = json.dumps({**ROWS[0], "physical_proof_cutoff": CYCLE,
                      "city": "Chongqing", "metric": "high", "target_date": "2026-10-01"})
    return writer, reader, raw


def test_physical_only_pass_reuses_exact_read_and_retains_strong_connection(tmp_path, monkeypatch):
    writer, reader, raw = _physical_pass_reader(tmp_path)
    original = serving._read_product_identity_at_cutoff_uncached
    calls = []

    def read(conn, value, **kwargs):
        calls.append((conn, value))
        return original(conn, value, **kwargs)

    monkeypatch.setattr(serving, "_read_product_identity_at_cutoff_uncached", read)
    expected = original(reader, raw)
    with serving.physical_read_pass(include_model_surface=False):
        assert [serving._read_product_identity_at_cutoff(reader, raw) for _ in range(3)] == [expected] * 3
        assert len(calls) == 1
        assert all(key[0] is reader for key in serving._PHYSICAL_READ_PASS.get())
    assert serving._PHYSICAL_READ_PASS.get() is None
    with serving.physical_read_pass(include_model_surface=False):
        assert serving._read_product_identity_at_cutoff(reader, raw) == expected
    assert len(calls) == 2
    reader.close()
    writer.close()


@pytest.mark.parametrize("field,value", [("city", "Karachi"), ("metric", "low"),
    ("target_date", "2026-10-02"), ("source_id", "gfs_global_single_runs"),
    ("raw_model_forecast_id", 22), ("model", "gfs_global"),
    ("request_params_json", '{"start_date":"2026-10-02","end_date":"2026-10-02","hourly":"temperature_2m_previous_day1"}'),
    ("source_cycle_time", "2026-10-01T06:00:00+00:00"),
    ("physical_proof_cutoff", "2026-10-01T06:00:00+00:00")])
def test_physical_pass_never_shares_different_raw_identity(tmp_path, monkeypatch, field, value):
    writer, reader, raw = _physical_pass_reader(tmp_path)
    original = serving._read_product_identity_at_cutoff_uncached
    calls = []

    def read(conn, item, **kwargs):
        calls.append(item)
        return original(conn, item, **kwargs)

    monkeypatch.setattr(serving, "_read_product_identity_at_cutoff_uncached", read)
    changed = json.dumps({**json.loads(raw), field: value})
    with serving.physical_read_pass(include_model_surface=False):
        serving._read_product_identity_at_cutoff(reader, raw)
        assert serving._read_product_identity_at_cutoff(reader, changed) == original(reader, changed)
    assert calls == [raw, changed]
    reader.close()
    writer.close()


def test_physical_pass_snapshot_rollover_and_external_commit_replay(tmp_path):
    writer, reader, raw = _physical_pass_reader(tmp_path)
    other = sqlite3.connect(f"file:{tmp_path / 'physical-pass.db'}?mode=ro", uri=True)
    with serving.physical_read_pass(include_model_surface=False):
        reader.execute("BEGIN")
        before = serving._read_product_identity_at_cutoff(reader, raw)
        added = _insert(writer, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"})
        writer.commit()
        assert serving._read_product_identity_at_cutoff(reader, raw) == before
        after = serving._read_product_identity_at_cutoff(other, raw)
        assert json.loads(after)["physical_artifact"]["artifact_id"] == added
        reader.rollback()
        reader.execute("BEGIN")
        assert serving._read_product_identity_at_cutoff(reader, raw) == after
        reader.commit()
        # Malformed newer proof is not hidden by a previously successful read.
        writer.execute("UPDATE raw_forecast_artifacts SET captured_at='malformed' WHERE artifact_id=?", (added,))
        writer.commit()
        assert serving._read_product_identity_at_cutoff(reader, raw) == serving._read_product_identity_at_cutoff_uncached(reader, raw)
        assert serving._read_product_identity_at_cutoff(reader, raw) != after
    reader.close()
    other.close()
    writer.close()


def test_physical_pass_own_write_rollback_without_data_version_change(tmp_path):
    writer, reader, raw = _physical_pass_reader(tmp_path)
    version = writer.execute("PRAGMA data_version").fetchone()[0]
    with serving.physical_read_pass(include_model_surface=False):
        before = serving._read_product_identity_at_cutoff(writer, raw)
        added = _insert(writer, params={"latitude": 40.7, "longitude": -74.0, "timezone": "America/New_York"})
        after = serving._read_product_identity_at_cutoff(writer, raw)
        assert writer.execute("PRAGMA data_version").fetchone()[0] == version
        assert json.loads(after)["physical_artifact"]["artifact_id"] == added
        assert after != before
        writer.rollback()
        assert serving._read_product_identity_at_cutoff(writer, raw) == before
        assert not serving._PHYSICAL_READ_PASS.get()
    reader.close()
    writer.close()


@pytest.mark.parametrize("unhashable", [False, True])
def test_physical_pass_exception_expired_hit_and_unhashable_connection(tmp_path, monkeypatch, unhashable):
    class UnhashableConnection(sqlite3.Connection):
        __hash__ = None

    writer, reader, raw = _physical_pass_reader(tmp_path, factory=UnhashableConnection if unhashable else sqlite3.Connection)
    original = serving._read_product_identity_at_cutoff_uncached
    calls = []

    def read(conn, item, **kwargs):
        calls.append(item)
        if len(calls) == 1:
            raise serving.CurrentValueServingReadUnavailable("private failed read")
        return original(conn, item, **kwargs)

    monkeypatch.setattr(serving, "_read_product_identity_at_cutoff_uncached", read)
    with serving.physical_read_pass(include_model_surface=False):
        with pytest.raises(serving.CurrentValueServingReadUnavailable, match="private failed"):
            serving._read_product_identity_at_cutoff(reader, raw)
        assert not serving._PHYSICAL_READ_PASS.get()
        serving._read_product_identity_at_cutoff(reader, raw)
        serving._read_product_identity_at_cutoff(reader, raw)
        assert len(calls) == (3 if unhashable else 2)
        assert bool(serving._PHYSICAL_READ_PASS.get()) is not unhashable
    assert serving._PHYSICAL_READ_PASS.get() is None
    ordinary = sqlite3.connect(f"file:{tmp_path / 'physical-pass.db'}?mode=ro", uri=True)
    with serving.physical_read_pass(include_model_surface=False):
        serving._read_product_identity_at_cutoff(ordinary, raw)
        with pytest.raises(serving.CurrentValueServingReadUnavailable, match="scan_budget_exceeded"):
            serving._read_product_identity_at_cutoff(ordinary, raw, deadline_monotonic=time.monotonic() - 1)
    ordinary.close()
    reader.close()
    writer.close()


def test_physical_pass_default_surface_and_nested_scope_compatibility():
    from src.data import openmeteo_model_surface as surface

    with serving.physical_read_pass():
        outer = serving._PHYSICAL_READ_PASS.get()
        assert surface._SURFACE_READ_PASS.get() is not None
        with serving.physical_read_pass(include_model_surface=False):
            assert serving._PHYSICAL_READ_PASS.get() is outer
    assert surface._SURFACE_READ_PASS.get() is None
    with serving.physical_read_pass(include_model_surface=False):
        assert surface._SURFACE_READ_PASS.get() is None
    assert serving._PHYSICAL_READ_PASS.get() is None
