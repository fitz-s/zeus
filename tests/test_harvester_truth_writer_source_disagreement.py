# Created: 2026-05-08
# Last reused/audited: 2026-10-07
# Authority basis: docs/operations/task_2026-05-08_post_merge_full_chain/TASK.md
#   Phase C — fix #263 SOURCE_DISAGREEMENT isolation layer
"""Antibody: _write_settlement_truth SOURCE_DISAGREEMENT dispute reason.

Root cause (fix #263): when obs rounds to within ±tolerance of the nearest bin
edge, the disagreement is measurement/rounding variance, not a genuine
outside-bin observation. Previously all such rows were classified as
'harvester_live_obs_outside_bin', making it impossible to distinguish
source-family disagreement (one source passes, other just misses) from
observations genuinely far outside any bin.

Fix: if rounded obs is within ±tolerance of the nearest bin edge → emit
'harvester_source_disagreement_within_tolerance' (DISPUTED).
If obs is far outside (> tolerance from nearest edge) → keep 'harvester_live_obs_outside_bin'.
null-bin rows remain 'harvester_live_no_bin_info' (precedence unchanged).

Test matrix
-----------
  T1: both agree + bin contains → VERIFIED, no dispute
  T2: obs within tolerance of bin edge (just misses) → SOURCE_DISAGREEMENT
  T3: both outside bin (obs far from edge) → obs_outside_bin
  T4: both bins None → no_bin_info (precedence unchanged — regression guard)
  T5: obs within tolerance of lo edge (open-shoulder hi=None) → SOURCE_DISAGREEMENT
  T6: obs exactly at tolerance boundary → SOURCE_DISAGREEMENT (inclusive)
  T7: obs one unit beyond tolerance → obs_outside_bin
"""
from __future__ import annotations

import sqlite3
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import City
from src.ingest.harvester_truth_writer import _write_settlement_truth


def _native_gamma_witness(raw=None):
    """Replay possessed original bytes through real immutable entity capture."""
    import httpx
    from src.data.wu_hourly_client import capture_entity
    if raw is None:
        raw = (Path(__file__).resolve().parents[1] /
               "docs/operations/current/evidence/gamma_hko_20260927_high.body").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == "119cf8ac59d95b69837d91f13412b985e1f515330ecbd7f5c2892de5ea65e023"
    now = datetime.now(timezone.utc)
    capture = capture_entity(httpx.Response(200, content=raw), started_at=now,
        finished_at=now, request_url="https://gamma-api.polymarket.com/events",
        request_params={"slug": "highest-temperature-in-hong-kong-on-september-27-2026"},
        native_unit="per_market_contract")
    proof = dict(entity_bytes_b64=base64.b64encode(capture.entity).decode(),
        entity_sha256=hashlib.sha256(capture.entity).hexdigest(),
        capture_started_at_utc=capture.started_at, capture_received_at_utc=capture.finished_at,
        request_url=capture.request_url, request_params=capture.request_params)
    identity = {key: proof[key] for key in ("entity_sha256", "capture_started_at_utc",
                "capture_received_at_utc", "request_url", "request_params")}
    proof["capture_identity_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return proof


@pytest.mark.parametrize("lane", ["ingest", "legacy"])
@pytest.mark.parametrize("metric,target,value,body_sha", [
    ("high", "2026-09-27", 32.0, "119cf8ac59d95b69837d91f13412b985e1f515330ecbd7f5c2892de5ea65e023"),
    ("low", "2026-09-27", 27.0, "34f2fef7940c95a2cd81b68116ce03592316bf63ff5417beff0153581998f3bd"),
    ("high", "2026-09-28", 32.0, "3000289d650415dba1053e0e6d785ec529bee4b1ae7b0e84ec9677bac7861408"),
])
def test_hko_actual_native_point_resets_unknown_without_verifying_decimal(tmp_path, lane, metric, target, value, body_sha):
    from src.state.db import get_connection, init_schema, init_schema_forecasts
    conn = get_connection(tmp_path / "private-reset.db")
    init_schema(conn)
    init_schema_forecasts(conn)
    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    writer = _write_settlement_truth
    if lane == "legacy":
        from src.execution.harvester import _write_settlement_truth as writer
    raw = (Path(__file__).resolve().parents[1] / "docs/operations/current/evidence" /
           f"gamma_hko_{target.replace('-', '')}_{metric}.body").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == body_sha
    proof = _native_gamma_witness(raw)
    event = json.loads(base64.b64decode(proof["entity_bytes_b64"]))
    event[0]["closed"] = False
    unresolved = _native_gamma_witness(json.dumps(event).encode())
    slug = event[0]["slug"]
    result = writer(conn, city, target, value, value, event_slug=slug,
                    venue_point_witness=unresolved, temperature_metric=metric)
    assert result["authority"] == "DISPUTED"
    assert result["source_grade"] == "UNKNOWN"
    assert conn.execute("SELECT count(*) FROM settlement_outcomes").fetchone()[0] == 0
    # Existing non-VERIFIED rows may reactivate with the independently proven basis.
    conn.execute("INSERT INTO settlement_outcomes(city,target_date,temperature_metric,market_slug,authority) "
                 "VALUES (?,?,?,?,?)", (city.name, target, metric, slug, "DISPUTED"))
    # Latest decimal deliberately differs; unique32 comes solely from native YES point.
    result = writer(conn, city, target, value, value, event_slug=slug,
                    obs_row=_obs(99.7, "C"), venue_point_witness=proof, temperature_metric=metric)
    assert result["authority"] == "VERIFIED", result
    row = conn.execute("SELECT * FROM settlement_outcomes").fetchone()
    assert row["settlement_value"] == value
    assert row["settlement_source"] == "polymarket_gamma"
    provenance = json.loads(row["provenance_json"])
    assert provenance["source_grade"] == "UNKNOWN"
    assert provenance["claim_basis"] == "venue_unique_integer_point_v1"
    assert row["settled_at"] >= proof["capture_received_at_utc"]
    assert provenance["venue_point_witness"] == proof
    assert provenance["reactivated_by"] == "gamma_point:" + proof["capture_identity_sha256"]
    changes = conn.total_changes
    assert writer(conn, city, target, value, value, event_slug=slug,
                  venue_point_witness=proof, temperature_metric=metric)["status"] == "preserved_existing_fact"
    assert conn.total_changes == changes
    conn.close()


def test_hko_normal_fetch_preserves_raw_witness_and_public_tick_accepts_point(tmp_path, monkeypatch):
    import httpx
    from src.state.db import get_connection, init_schema, init_schema_forecasts
    from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets
    raw = (Path(__file__).resolve().parents[1] /
           "docs/operations/current/evidence/gamma_hko_20260927_high.body").read_bytes()
    calls = []
    def original_transport(url, **kwargs):
        calls.append((url, kwargs))
        assert url == "https://gamma-api.polymarket.com/events"
        return httpx.Response(200, content=raw, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", original_transport)
    conn = get_connection(tmp_path / "private-normal-tick.db")
    init_schema(conn)
    init_schema_forecasts(conn)
    result = write_settlement_truth_for_open_markets(conn)
    assert result["settlements_written"] == 1, result
    row = conn.execute("SELECT * FROM settlement_outcomes").fetchone()
    provenance = json.loads(row["provenance_json"])
    assert row["settlement_value"] == 32.0
    assert row["settlement_source"] == "polymarket_gamma"
    assert provenance["source_grade"] == "UNKNOWN"
    proof = provenance["venue_point_witness"]
    assert base64.b64decode(proof["entity_bytes_b64"]) == raw
    assert proof["entity_sha256"] == "119cf8ac59d95b69837d91f13412b985e1f515330ecbd7f5c2892de5ea65e023"
    assert proof["request_params"] == {str(k): str(v) for k, v in calls[0][1]["params"].items()}
    assert row["settled_at"] >= proof["capture_received_at_utc"]
    assert len(calls) == 1
    conn.close()


@pytest.mark.parametrize("lane", ["ingest", "legacy"])
@pytest.mark.parametrize("bad", ["wrong_bounds", "wrong_condition", "wrong_unit", "wrong_track"])
def test_hko_writers_reject_mismatched_native_claim_before_legacy_write(tmp_path, lane, bad):
    from src.state.db import get_connection, init_schema, init_schema_forecasts
    conn = get_connection(tmp_path / "private-writer-bad.db")
    init_schema(conn)
    init_schema_forecasts(conn)
    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="F" if bad == "wrong_unit" else "C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    writer = _write_settlement_truth
    if lane == "legacy":
        from src.execution.harvester import _write_settlement_truth as writer
    value = 33.0 if bad == "wrong_bounds" else 32.0
    outcomes = ([{"condition_id": "wrong", "yes_token_id": "wrong", "yes_won": True}]
                if bad == "wrong_condition" else None)
    changes = conn.total_changes
    result = writer(conn, city, "2026-09-27", value, value,
        event_slug="highest-temperature-in-hong-kong-on-september-27-2026",
        temperature_metric="low" if bad == "wrong_track" else "high",
        venue_point_witness=_native_gamma_witness(), resolved_market_outcomes=outcomes)
    assert result["authority"] == "DISPUTED", result
    assert conn.execute("SELECT count(*) FROM settlements").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM settlement_outcomes").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market_events").fetchone()[0] == 0
    assert conn.total_changes == changes
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_world_conn() -> sqlite3.Connection:
    """In-memory DB with minimal settlements + settlement_outcomes schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settlements (
            city TEXT, target_date TEXT, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT, pm_bin_lo REAL, pm_bin_hi REAL,
            unit TEXT, settlement_source_type TEXT, temperature_metric TEXT,
            physical_quantity TEXT, observation_field TEXT, data_version TEXT,
            provenance_json TEXT,
            PRIMARY KEY (city, target_date, market_slug)
        );
        CREATE TABLE IF NOT EXISTS settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL, target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS market_events (
            event_id INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL, city TEXT NOT NULL,
            target_date TEXT NOT NULL, temperature_metric TEXT NOT NULL,
            condition_id TEXT, token_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, outcome TEXT,
            created_at TEXT, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


def _make_city_f() -> City:
    return City(
        name="NYC",
        lat=40.78,
        lon=-73.97,
        timezone="America/New_York",
        settlement_unit="F",
        cluster="NYC",
        wu_station="KLGA",
        country_code="US",
        settlement_source_type="wu_icao",
    )

def _make_city_c() -> City:
    return City(
        name="London",
        lat=51.5074,
        lon=-0.1278,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLL",
        country_code="GB",
        settlement_source_type="wu_icao",
    )


def _obs(val: float, unit: str = "F") -> dict:
    return {
        "id": 1,
        "source": "wu_icao_history",
        "high_temp": val,
        "low_temp": None,
        "unit": unit,
        "fetched_at": "2026-05-08T00:00:00Z",
        "station_id": "KLGA",
        "authority": "VERIFIED",
        "observation_field": "high_temp",
        "observed_temp": val,
    }


# ---------------------------------------------------------------------------
# T1: both agree + bin contains → VERIFIED, reason None
# ---------------------------------------------------------------------------

def test_agree_bin_contains_is_verified():
    """Baseline: obs inside bin → VERIFIED, no dispute reason."""
    conn = _make_world_conn()
    city = _make_city_f()
    result = _write_settlement_truth(
        conn, city, "2026-01-10", 44.0, 46.0,
        event_slug="slug-agree",
        obs_row=_obs(45.0, "F"),
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T2: obs within tolerance of bin edge → SOURCE_DISAGREEMENT
# ---------------------------------------------------------------------------

def test_obs_within_tolerance_of_bin_edge_is_source_disagreement():
    """Obs just misses bin edge by ≤1°F → SOURCE_DISAGREEMENT, DISPUTED."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin is [44, 46]. Obs rounds to 43 — 1°F below lo edge. Distance = 1.0 ≤ tol=1.0.
    result = _write_settlement_truth(
        conn, city, "2026-01-11", 44.0, 46.0,
        event_slug="slug-disagree",
        obs_row=_obs(43.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Expected SOURCE_DISAGREEMENT, got {result['reason']!r} — "
        "obs within tolerance of bin edge must not be labelled obs_outside_bin"
    )
    assert result["authority"] == "DISPUTED"


# ---------------------------------------------------------------------------
# T3: obs far outside bin (> tolerance from edge) → obs_outside_bin
# ---------------------------------------------------------------------------

def test_obs_far_outside_bin_is_obs_outside_bin():
    """Obs genuinely far outside bin → 'harvester_live_obs_outside_bin'."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin is [44, 46]. Obs rounds to 40 — 4°F below lo edge. Distance = 4.0 > tol=1.0.
    result = _write_settlement_truth(
        conn, city, "2026-01-12", 44.0, 46.0,
        event_slug="slug-far-outside",
        obs_row=_obs(40.0, "F"),
    )
    assert result["reason"] == "harvester_live_obs_outside_bin", (
        f"Expected obs_outside_bin for obs far from edge, got {result['reason']!r}"
    )
    assert result["authority"] == "DISPUTED"


# ---------------------------------------------------------------------------
# T4: both bins None → no_bin_info (precedence regression guard)
# ---------------------------------------------------------------------------

def test_null_bin_still_emits_no_bin_info_not_disagreement():
    """null-bin rows → 'harvester_live_no_bin_info' (not SOURCE_DISAGREEMENT)."""
    conn = _make_world_conn()
    city = _make_city_f()
    result = _write_settlement_truth(
        conn, city, "2026-01-13", None, None,
        event_slug="slug-null-bin",
        obs_row=_obs(45.0, "F"),
    )
    assert result["reason"] == "harvester_live_no_bin_info", (
        f"null-bin must remain no_bin_info, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T5: open-shoulder hi=None, obs within tolerance of lo → SOURCE_DISAGREEMENT
# ---------------------------------------------------------------------------

def test_open_shoulder_lo_only_within_tolerance_is_disagreement():
    """Open-shoulder bin (lo=44, hi=None), obs=43 just below → SOURCE_DISAGREEMENT."""
    conn = _make_world_conn()
    city = _make_city_f()
    # lo=44°F open-shoulder. Obs=43°F rounds to 43. Distance to lo edge = 1.0 ≤ tol.
    result = _write_settlement_truth(
        conn, city, "2026-01-14", 44.0, None,
        event_slug="slug-open-shoulder-disagree",
        obs_row=_obs(43.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Expected SOURCE_DISAGREEMENT, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T6: obs exactly at tolerance boundary → SOURCE_DISAGREEMENT (inclusive)
# ---------------------------------------------------------------------------

def test_obs_exactly_at_tolerance_boundary_is_disagreement():
    """Obs exactly 1°F from bin edge → SOURCE_DISAGREEMENT (boundary inclusive)."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin [50, 52]. Obs=49 → distance=1.0 exactly. Tolerance=1.0 → ≤ → disagreement.
    result = _write_settlement_truth(
        conn, city, "2026-01-15", 50.0, 52.0,
        event_slug="slug-boundary",
        obs_row=_obs(49.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Obs at exact tolerance boundary should be SOURCE_DISAGREEMENT, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T7: obs one unit beyond tolerance → obs_outside_bin
# ---------------------------------------------------------------------------

def test_obs_beyond_tolerance_is_obs_outside_bin():
    """Obs 1°F beyond tolerance from bin edge → obs_outside_bin."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin [50, 52]. Obs=48.4 rounds to 48 → distance=2.0 > tol=1.0 → obs_outside_bin.
    result = _write_settlement_truth(
        conn, city, "2026-01-16", 50.0, 52.0,
        event_slug="slug-beyond-tol",
        obs_row=_obs(48.4, "F"),
    )
    assert result["reason"] == "harvester_live_obs_outside_bin", (
        f"Obs beyond tolerance should be obs_outside_bin, got {result['reason']!r}"
    )


@pytest.mark.parametrize("writer_lane", ["ingest", "legacy"])
@pytest.mark.parametrize("metric,target", [("high", "2026-09-27"),
                                          ("low", "2026-09-27"),
                                          ("high", "2026-09-28")])
def test_hko_later_decimal_revision_matching_winner_is_not_source_verified(writer_lane, metric, target):
    """A captured later source value can fit the paid bin without proving first publication."""
    conn = _make_world_conn()
    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    obs = dict(_obs(32.7, "C"), source="hko_daily_api", station_id="HKO",
               fetched_at="2026-09-29T00:00:00Z",
               observation_local_time="2026-09-27T15:00:00+08:00",
               high_provenance_metadata={"response_sha256": "a" * 64,
                                         "first_publication": True})
    obs[metric + "_temp"] = 32.7
    writer = _write_settlement_truth
    if writer_lane == "legacy":
        from src.execution.harvester import _write_settlement_truth as writer
    result = writer(conn, city, target, 32.0, 32.0,
                    event_slug="hko-sep27-high", obs_row=obs, temperature_metric=metric)
    assert result["authority"] == "DISPUTED", result
    assert result["source_grade"] == "UNKNOWN"
    assert conn.execute("SELECT count(*) FROM settlements").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM settlement_outcomes").fetchone()[0] == 0
    before = conn.total_changes
    assert writer(conn, city, target, 32.0, 32.0,
                  event_slug="hko-sep27-high", obs_row=obs,
                  temperature_metric=metric)["changed"] is False
    assert conn.total_changes == before


@pytest.mark.parametrize("writer_lane", ["ingest", "legacy"])
def test_hko_unknown_call_preserves_historical_verified_without_recertifying(writer_lane):
    conn = _make_world_conn()
    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    conn.execute("INSERT INTO settlements(city,target_date,market_slug,temperature_metric,"
                 "settlement_value,authority,provenance_json) VALUES (?,?,?,?,?,?,?)",
                 (city.name, "2026-09-27", "hko-sep27-high", "high", 32.0, "VERIFIED", "{}"))
    before = list(conn.execute("SELECT * FROM settlements"))[0]
    changes = conn.total_changes
    writer = _write_settlement_truth
    if writer_lane == "legacy":
        from src.execution.harvester import _write_settlement_truth as writer
    result = writer(conn, city, "2026-09-27", 32.0, 32.0,
                    event_slug="hko-sep27-high", obs_row=dict(_obs(32.7, "C"), source="hko_daily_api"))
    assert result["status"] == "preserved_existing_fact"
    assert result["source_grade"] == "UNKNOWN"
    assert result["authority"] == "DISPUTED"
    assert tuple(conn.execute("SELECT * FROM settlements").fetchone()) == tuple(before)
    assert conn.total_changes == changes


def test_hko_old_matching_verified_noop_cannot_certify_publication_or_emit_outcomes():
    import json
    from src.ingest.harvester_truth_writer import (
        _SETTLEMENT_TRUTH_REVISION, _metric_identity_for, _stable_settlement_truth_matches,
    )
    conn = _make_world_conn()
    conn.execute("ALTER TABLE settlement_outcomes ADD COLUMN settlement_unit TEXT")
    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    metric = _metric_identity_for("high")
    conn.execute("INSERT INTO settlements(city,target_date,market_slug,winning_bin,settlement_value,"
                 "settlement_source,settled_at,authority,pm_bin_lo,pm_bin_hi,unit,settlement_source_type,"
                 "temperature_metric,physical_quantity,observation_field,data_version,provenance_json) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (city.name, "2026-09-27", "hko-high", "32°C", 32.0, city.settlement_source,
                  "2026-09-29T00:00:00Z", "VERIFIED", 32.0, 32.0, "C", "HKO", "high",
                  metric.physical_quantity, metric.observation_field, "hko_daily_api",
                  json.dumps({"truth_revision": _SETTLEMENT_TRUTH_REVISION})))
    conn.execute("INSERT INTO settlement_outcomes(city,target_date,temperature_metric,market_slug,"
                 "winning_bin,settlement_value,settlement_source,settled_at,authority,settlement_unit) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (city.name, "2026-09-27", "high", "hko-high", "32°C", 32.0,
                  city.settlement_source, "2026-09-29T00:00:00Z", "VERIFIED", "C"))
    assert _stable_settlement_truth_matches(conn, city=city, target_date="2026-09-27",
             metric_identity=metric, event_slug="hko-high", winning_bin="32°C", settlement_value=32.0,
             settled_at="2026-09-29T00:00:00Z", authority="VERIFIED", pm_bin_lo=32.0, pm_bin_hi=32.0,
             db_source_type="HKO", data_version="hko_daily_api")
    changes = conn.total_changes
    result = _write_settlement_truth(conn, city, "2026-09-27", 32.0, 32.0,
             event_slug="hko-high", obs_row=dict(_obs(32.7, "C"), source="hko_daily_api",
                  fetched_at="2026-09-29T00:00:00Z"),
             resolved_market_outcomes=[{"condition_id": "cond", "yes_token_id": "yes", "yes_won": True}])
    assert result["source_grade"] == "UNKNOWN"
    assert result["authority"] == "DISPUTED"
    assert result["status"] == "preserved_existing_fact"
    assert conn.total_changes == changes
    assert conn.execute("SELECT count(*) FROM market_events").fetchone()[0] == 0
