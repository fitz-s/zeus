# Created: 2026-04-13
# Last reused/audited: 2026-04-25
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Protect K2 live-ingestion and backfill relationship contracts.
# Reuse: Keep tests fixture-backed; inspect source-routing assumptions before extending.
# Authority basis: K2 live-ingestion packet; P1 daily observation writer provenance packet.
"""K2 live-ingestion packet — relationship tests.

These tests enforce cross-module invariants that the K2 packet depends on.
They are relationship tests, not function tests: each asserts a property
that must hold at the seam between two modules, not a local behavior of
one function. The test names document the invariant.

Why relationship tests, not unit tests:
The K2 appenders (daily_obs_append, hourly_instants_append, solar_append,
forecasts_append) orchestrate a physical data-table write + a coverage
ledger upsert + a guard validation, all via different modules. A unit
test of any one of them cannot catch the failure mode where the three
get out of sync — e.g. the physical table has a row but data_coverage
doesn't, or the guard import path diverges from the one the backfill
script uses. Relationship tests assert the cross-module contract at
exactly the place it can break.

Run:
    pytest tests/test_k2_live_ingestion_relationships.py -v
"""
from __future__ import annotations

import ast
import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.data import (
    daily_obs_append,
    forecasts_append,
    hourly_instants_append,
    solar_append,
)
from src.data import ingestion_guard
from src.data.hole_scanner import DataTable as ScannerDataTable
from src.data.hole_scanner import HoleScanner, SOURCES_BY_TABLE
from src.state.data_coverage import (
    CoverageReason,
    CoverageStatus,
    DataTable,
    count_by_status,
    find_pending_fills,
    record_failed,
    record_legitimate_gap,
    record_written,
)
from src.state.db import init_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# R1 — Guard surface: Layer 3 is gone, callers must not reference it.
# ---------------------------------------------------------------------------


def test_R1_no_module_references_deleted_layer_3() -> None:
    """Any caller referencing `check_seasonal_plausibility` or
    `SeasonalPlausibilityViolation` would crash at import time.

    Grep-based structural test: if either name appears in any production
    source file, K2 is broken even if tests pass — the deletion was
    incomplete. Layer 3 was deleted 2026-04-13 as task #60; this test
    guards against regression via import-time failure OR stale comments
    that might fool future readers into thinking Layer 3 still exists.
    """
    forbidden = ("check_seasonal_plausibility", "SeasonalPlausibilityViolation")
    # Only check executable code, not docstrings / comments.
    modules = [
        ingestion_guard,
        daily_obs_append,
        hourly_instants_append,
        solar_append,
        forecasts_append,
    ]
    for mod in modules:
        source = inspect.getsource(mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                pytest.fail(
                    f"{mod.__name__} contains AST Name reference to "
                    f"deleted symbol {node.id!r} at line {node.lineno}"
                )
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                pytest.fail(
                    f"{mod.__name__} contains AST Attribute reference "
                    f"to deleted symbol {node.attr!r} at line {node.lineno}"
                )


# ---------------------------------------------------------------------------
# R2 — SOURCES_BY_TABLE registry must match what each appender writes
# ---------------------------------------------------------------------------


def test_R2_daily_obs_sources_match_registry() -> None:
    """daily_obs_append constants must match hole_scanner's registry.

    If the appender writes source='wu_icao_history_v2' while the scanner
    expects 'wu_icao_history', the hole scanner will treat every live row
    as an untracked hole and the scanner loop will fight the appender
    forever. Relationship test pins the contract.
    """
    expected = SOURCES_BY_TABLE[ScannerDataTable.OBSERVATIONS]
    assert daily_obs_append.WU_SOURCE in expected, (
        f"daily_obs_append.WU_SOURCE={daily_obs_append.WU_SOURCE!r} not in "
        f"hole_scanner's OBSERVATIONS sources {expected!r}"
    )
    assert daily_obs_append.HKO_SOURCE in expected, (
        f"daily_obs_append.HKO_SOURCE={daily_obs_append.HKO_SOURCE!r} not in "
        f"hole_scanner's OBSERVATIONS sources {expected!r}"
    )


def test_R2_hourly_instants_source_match_registry() -> None:
    expected = SOURCES_BY_TABLE[ScannerDataTable.OBSERVATION_INSTANTS]
    assert hourly_instants_append.SOURCE in expected


def test_R2_solar_source_match_registry() -> None:
    expected = SOURCES_BY_TABLE[ScannerDataTable.SOLAR_DAILY]
    assert solar_append.SOURCE in expected


def test_R2_forecasts_sources_match_registry() -> None:
    expected = set(SOURCES_BY_TABLE[ScannerDataTable.FORECASTS])
    actual = set(forecasts_append.MODEL_SOURCE_MAP.values())
    assert actual == expected, (
        f"forecasts_append.MODEL_SOURCE_MAP values {actual!r} != "
        f"hole_scanner's FORECASTS sources {expected!r}"
    )


# ---------------------------------------------------------------------------
# R3 — daily_obs_append reads WU station config from cities.json (R-G)
# ---------------------------------------------------------------------------


def test_R3_daily_obs_append_has_no_city_stations() -> None:
    """Phase 3 R-G: daily_obs_append must NOT declare a local CITY_STATIONS map.

    Station config (ICAO, country code, unit) is now read exclusively from
    cities.json via src.config.cities_by_name. The parallel map is deleted.
    """
    assert not hasattr(daily_obs_append, "CITY_STATIONS"), (
        "daily_obs_append still exports CITY_STATIONS — Phase 3 R-G requires "
        "this map to be deleted; station config must come from cities.json."
    )


def test_R3_daily_obs_append_wu_cities_sourced_from_cities_json() -> None:
    """daily_tick iterates cities_by_name filtered by settlement_source_type=='wu_icao'.

    Verify that all wu_icao cities in cities.json have a non-empty wu_station
    and country_code — the two fields append_wu_city reads in place of CITY_STATIONS.
    """
    from src.config import cities_by_name
    wu_cities = [c for c in cities_by_name.values() if c.settlement_source_type == "wu_icao"]
    assert wu_cities, "No wu_icao cities found in cities.json"
    missing = []
    for c in wu_cities:
        if not c.wu_station:
            missing.append(f"{c.name}: wu_station is empty")
        if not c.country_code:
            missing.append(f"{c.name}: country_code is empty")
    assert not missing, "wu_icao cities missing required station fields:\n  " + "\n  ".join(missing)


# ---------------------------------------------------------------------------
# R4 — Coverage ledger two-table atomicity via savepoint
# ---------------------------------------------------------------------------


def test_R4_write_atom_with_coverage_rolls_back_on_failure() -> None:
    """If _write_atom_with_coverage raises mid-way, the savepoint must
    rollback the observations INSERT so data_coverage doesn't diverge.

    This directly tests the S1-2 reviewer finding: without per-row
    savepoint isolation, a failure between the INSERT and record_written
    would leave an orphan observation row the scanner couldn't reach.
    """
    conn = _memdb()

    # Set up a valid high atom but force record_written to fail by
    # corrupting the data_coverage table schema temporarily.
    from src.data.daily_obs_append import _build_atom_pair, _write_atom_with_coverage
    target_d = date(2025, 1, 15)
    atom_high, atom_low = _build_atom_pair(
        city_name="NYC",
        target_d=target_d,
        high_val=50.0,
        low_val=30.0,
        raw_unit="F",
        target_unit="F",
        station_id="KLGA:US",
        source="wu_icao_history",
        rebuild_run_id="test",
        data_source_version="wu_icao_v1_2026",
        api_endpoint="test://",
        provenance={},
    )

    # Drop data_coverage so the second statement fails but the first
    # (observations INSERT) would have already run without rollback.
    conn.execute("DROP TABLE data_coverage")

    with pytest.raises(Exception):
        _write_atom_with_coverage(
            conn, atom_high, atom_low, data_source="wu_icao_history",
        )

    # Critical assertion: observations must have zero rows for this
    # (city, target_date, source). The savepoint ROLLBACK TO must have
    # undone the INSERT.
    remaining = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE city=? AND target_date=?",
        ("NYC", target_d.isoformat()),
    ).fetchone()[0]
    assert remaining == 0, (
        "_write_atom_with_coverage leaked an observation row after "
        "savepoint rollback — atomicity contract broken"
    )


# ---------------------------------------------------------------------------
# R5 — Guard rejection terminal state is LEGITIMATE_GAP (S2 fix)
# ---------------------------------------------------------------------------


def test_R5_guard_rejection_uses_legitimate_gap_not_failed() -> None:
    """A deterministic IngestionGuard rejection must land as LEGITIMATE_GAP
    in data_coverage, not FAILED with a long embargo.

    The reviewer's S2-2 finding: FAILED is for transient errors that the
    scanner retries after retry_after expires. A guard rejection is
    deterministic and retrying it does not change the outcome. Using
    FAILED with a 365d embargo was the wrong state model — after 365
    days the scanner would retry and cycle forever.
    """
    conn = _memdb()

    # Inject a row that Layer 1 will reject (160°F exceeds Earth record).
    from src.data.daily_obs_append import append_wu_city, WU_SOURCE

    # Can't actually call the real WU API in a unit test. Instead,
    # simulate by monkeypatching the fetch helper to return a bad value.
    import src.data.daily_obs_append as mod
    original = mod._fetch_wu_icao_daily_highs_lows
    try:
        mod._fetch_wu_icao_daily_highs_lows = lambda *a, **kw: mod.WuDailyFetchResult(
            payload={
                "2025-06-01": (200.0, 190.0),  # impossible — guard will reject
            }
        )
        stats = append_wu_city(
            "Houston",
            [date(2025, 6, 1)],
            conn,
            rebuild_run_id="test",
        )
    finally:
        mod._fetch_wu_icao_daily_highs_lows = original

    # Row should be pinned as LEGITIMATE_GAP with reason GUARD_REJECTED
    # (S2-2 fix). It must NOT be in FAILED.
    gap_row = conn.execute(
        """
        SELECT status, reason FROM data_coverage
        WHERE data_table='observations' AND city='Houston'
          AND target_date='2025-06-01'
        """
    ).fetchone()
    assert gap_row is not None, (
        "append_wu_city did not record a coverage row for the guard rejection"
    )
    assert gap_row["status"] == CoverageStatus.LEGITIMATE_GAP.value, (
        f"guard rejection landed as {gap_row['status']}, expected LEGITIMATE_GAP"
    )
    assert gap_row["reason"] == CoverageReason.GUARD_REJECTED
    assert stats["guard_rejected"] == 1


@pytest.mark.parametrize(
    ("failure_reason", "auth_failed"),
    [
        (CoverageReason.AUTH_ERROR, True),
        (CoverageReason.HTTP_429, False),
        (CoverageReason.HTTP_5XX, False),
        (CoverageReason.PARSE_ERROR, False),
    ],
)
def test_R5_wu_fetch_failure_reason_reaches_coverage(
    failure_reason: str,
    auth_failed: bool,
) -> None:
    """WU fetch failures must not collapse into a generic empty payload.

    Relationship: the fetch layer classifies upstream failures, and
    append_wu_city must preserve that reason in data_coverage so operators can
    distinguish auth, rate-limit, server, and parse failures.
    """
    conn = _memdb()
    import src.data.daily_obs_append as mod

    original = mod._fetch_wu_icao_daily_highs_lows
    try:
        mod._fetch_wu_icao_daily_highs_lows = lambda *a, **kw: mod.WuDailyFetchResult(
            payload={},
            failure_reason=failure_reason,
            retryable=not auth_failed,
            auth_failed=auth_failed,
            error="synthetic failure",
        )
        stats = mod.append_wu_city(
            "NYC",
            [date(2026, 4, 10)],
            conn,
            rebuild_run_id="test",
        )
    finally:
        mod._fetch_wu_icao_daily_highs_lows = original

    row = conn.execute(
        """
        SELECT status, reason FROM data_coverage
        WHERE data_table='observations' AND city='NYC'
          AND data_source='wu_icao_history' AND target_date='2026-04-10'
        """
    ).fetchone()
    assert row["status"] == CoverageStatus.FAILED.value
    assert row["reason"] == failure_reason
    assert stats["fetch_errors"] == 1
    assert stats["missing_from_api"] == 0


def test_R5_empty_wu_payload_is_not_network_error() -> None:
    """A usable empty WU response is SOURCE_NOT_PUBLISHED_YET, not transport failure."""
    conn = _memdb()
    import src.data.daily_obs_append as mod

    original = mod._fetch_wu_icao_daily_highs_lows
    try:
        mod._fetch_wu_icao_daily_highs_lows = lambda *a, **kw: mod.WuDailyFetchResult(
            payload={}
        )
        stats = mod.append_wu_city(
            "NYC",
            [date(2026, 4, 10)],
            conn,
            rebuild_run_id="test",
        )
    finally:
        mod._fetch_wu_icao_daily_highs_lows = original

    row = conn.execute(
        """
        SELECT status, reason FROM data_coverage
        WHERE data_table='observations' AND city='NYC'
          AND data_source='wu_icao_history' AND target_date='2026-04-10'
        """
    ).fetchone()
    assert row["status"] == CoverageStatus.FAILED.value
    assert row["reason"] == CoverageReason.SOURCE_NOT_PUBLISHED_YET
    assert stats["fetch_errors"] == 0
    assert stats["missing_from_api"] == 1


def test_R5_wu_observation_upsert_preserves_row_identity() -> None:
    """Duplicate live WU writes update the observation row without delete+insert."""
    conn = _memdb()
    from src.data.daily_obs_append import _build_atom_pair, _write_atom_with_coverage

    target_d = date(2025, 1, 15)
    first_high, first_low = _build_atom_pair(
        city_name="NYC",
        target_d=target_d,
        high_val=50.0,
        low_val=30.0,
        raw_unit="F",
        target_unit="F",
        station_id="KLGA:US",
        source="wu_icao_history",
        rebuild_run_id="first",
        data_source_version="wu_icao_v1_2026",
        api_endpoint="test://",
        provenance={"run": "first"},
    )
    _write_atom_with_coverage(conn, first_high, first_low, data_source="wu_icao_history")
    conn.commit()

    row1 = conn.execute(
        "SELECT id, high_temp, low_temp, rebuild_run_id FROM observations "
        "WHERE city='NYC' AND target_date='2025-01-15' AND source='wu_icao_history'"
    ).fetchone()

    second_high, second_low = _build_atom_pair(
        city_name="NYC",
        target_d=target_d,
        high_val=52.0,
        low_val=31.0,
        raw_unit="F",
        target_unit="F",
        station_id="KLGA:US",
        source="wu_icao_history",
        rebuild_run_id="second",
        data_source_version="wu_icao_v1_2026",
        api_endpoint="test://",
        provenance={"run": "second"},
    )
    _write_atom_with_coverage(conn, second_high, second_low, data_source="wu_icao_history")
    conn.commit()

    row2 = conn.execute(
        "SELECT id, high_temp, low_temp, rebuild_run_id FROM observations "
        "WHERE city='NYC' AND target_date='2025-01-15' AND source='wu_icao_history'"
    ).fetchone()
    coverage = conn.execute(
        "SELECT status FROM data_coverage WHERE city='NYC' "
        "AND data_source='wu_icao_history' AND target_date='2025-01-15'"
    ).fetchone()

    assert row2["id"] == row1["id"]
    assert row2["high_temp"] == 52.0
    assert row2["low_temp"] == 31.0
    assert row2["rebuild_run_id"] == "second"
    assert coverage["status"] == CoverageStatus.WRITTEN.value


# ---------------------------------------------------------------------------
# R6 — MISSING → WRITTEN transition works end-to-end
# ---------------------------------------------------------------------------


def test_R6_scanner_missing_flipped_to_written_by_append() -> None:
    """hole_scanner creates MISSING rows; when the appender runs on those
    exact (city, source, target_date) tuples, they flip to WRITTEN.

    This is the full immune-system loop: scanner detects hole → scanner
    writes MISSING → live append fills the hole → data_coverage flips to
    WRITTEN → scanner's next pass excludes this row from pending fills.
    Any break in that chain means holes accumulate indefinitely.
    """
    conn = _memdb()

    # Pretend we have no WU data yet; scanner should find every expected
    # row as MISSING or LEGITIMATE_GAP.
    scanner = HoleScanner(conn, today=date(2026, 4, 13))
    result = scanner.scan(ScannerDataTable.OBSERVATIONS)
    assert result.recorded_missing > 0 or result.pinned_legitimate_gap > 0

    # Count pending fills before and after we simulate one appender write.
    before = len(find_pending_fills(conn, data_table=DataTable.OBSERVATIONS, max_rows=100_000))

    # Simulate an appender writing one row successfully.
    record_written(
        conn,
        data_table=DataTable.OBSERVATIONS,
        city="NYC",
        data_source="wu_icao_history",
        target_date="2026-04-10",
    )

    after = len(find_pending_fills(conn, data_table=DataTable.OBSERVATIONS, max_rows=100_000))
    assert after == before - 1, (
        f"Expected pending fills to drop by exactly 1 after record_written, "
        f"got {before} → {after}"
    )


# ---------------------------------------------------------------------------
# R7 — FAILED with retry_after embargo is honored by find_pending_fills
# ---------------------------------------------------------------------------


def test_R7_failed_row_under_retry_embargo_hidden_from_pending() -> None:
    """A FAILED row whose retry_after is in the future must NOT appear
    in find_pending_fills — otherwise the scanner would hammer a
    rate-limited upstream during its embargo window.
    """
    conn = _memdb()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    record_failed(
        conn, data_table=DataTable.OBSERVATIONS, city="NYC",
        data_source="wu_icao_history", target_date=date(2026, 4, 10),
        reason=CoverageReason.HTTP_429, retry_after=future,
    )
    record_failed(
        conn, data_table=DataTable.OBSERVATIONS, city="Chicago",
        data_source="wu_icao_history", target_date=date(2026, 4, 10),
        reason=CoverageReason.HTTP_429, retry_after=past,
    )
    conn.commit()

    pending = find_pending_fills(conn, data_table=DataTable.OBSERVATIONS)
    pending_cities = {r["city"] for r in pending}
    assert "NYC" not in pending_cities, "future-embargo FAILED should be hidden"
    assert "Chicago" in pending_cities, "past-embargo FAILED should be visible"


# ---------------------------------------------------------------------------
# R8 — daily_obs_append.data_source_version matches backfill script
# ---------------------------------------------------------------------------


def test_R8_data_source_version_matches_backfill_wu() -> None:
    """Both live and backfill WU writes must use 'wu_icao_v1_2026' so
    calibration queries that group by data_source_version do not fragment
    live-ingested rows from backfilled rows. S2-1 reviewer finding.
    """
    source = inspect.getsource(daily_obs_append)
    # The constant must appear literally in daily_obs_append.
    assert '"wu_icao_v1_2026"' in source, (
        "daily_obs_append does not write data_source_version='wu_icao_v1_2026'"
    )

    script_path = PROJECT_ROOT / "scripts" / "backfill_wu_daily_all.py"
    script_source = script_path.read_text()
    assert '"wu_icao_v1_2026"' in script_source, (
        "backfill_wu_daily_all does not use data_source_version='wu_icao_v1_2026'"
    )


def test_R8_data_source_version_matches_backfill_hko() -> None:
    source = inspect.getsource(daily_obs_append)
    assert '"hko_opendata_v1_2026"' in source

    script_path = PROJECT_ROOT / "scripts" / "backfill_hko_daily.py"
    script_source = script_path.read_text()
    assert '"hko_opendata_v1_2026"' in script_source


def _load_script_module(script_name: str, module_name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        module_name, PROJECT_ROOT / "scripts" / script_name,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _assert_provenance_identity(provenance: dict) -> None:
    required = {
        "payload_hash",
        "source_url",
        "parser_version",
        "source",
        "station_id",
        "target_date",
    }
    missing = required - set(provenance)
    assert not missing, f"missing provenance keys: {sorted(missing)}"
    for key in required:
        assert provenance[key], f"empty provenance key: {key}"
    assert str(provenance["payload_hash"]).startswith("sha256:")


def _legacy_observations_conn() -> sqlite3.Connection:
    return _memdb()


def test_R8_wu_backfill_writes_non_empty_provenance_identity() -> None:
    mod = _load_script_module("backfill_wu_daily_all.py", "_backfill_wu_provenance")
    source_url = mod._build_wu_source_url(
        icao="KORD",
        cc="US",
        unit_code="e",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 3),
    )
    provenance = mod._build_wu_daily_provenance(
        icao="KORD",
        cc="US",
        unit="F",
        unit_code="e",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 3),
        target_date=date(2026, 4, 2),
        payload_hash="sha256:" + "a" * 64,
        source_url=source_url,
    )

    _assert_provenance_identity(provenance)
    assert provenance["source"] == "wu_icao_history"
    assert provenance["station_id"] == "KORD"
    assert "apiKey=" not in provenance["source_url"]
    assert provenance["api_key_redacted"] is True

    script_source = (PROJECT_ROOT / "scripts" / "backfill_wu_daily_all.py").read_text()
    assert "provenance_metadata={}" not in script_source
    assert "write_daily_observation_with_revision" in script_source


def test_R8_wu_backfill_persists_provenance_identity(monkeypatch) -> None:
    mod = _load_script_module("backfill_wu_daily_all.py", "_backfill_wu_writer")
    conn = _legacy_observations_conn()

    def fake_fetch(icao, cc, start_date, end_date, unit, timezone_name):
        unit_code = "m" if unit == "C" else "e"
        source_url = mod._build_wu_source_url(
            icao=icao,
            cc=cc,
            unit_code=unit_code,
            start_date=start_date,
            end_date=end_date,
        )
        target_date = start_date
        return {
            target_date.isoformat(): (
                75.0,
                62.0,
                mod._build_wu_daily_provenance(
                    icao=icao,
                    cc=cc,
                    unit=unit,
                    unit_code=unit_code,
                    start_date=start_date,
                    end_date=end_date,
                    target_date=target_date,
                    payload_hash="sha256:" + "d" * 64,
                    source_url=source_url,
                ),
            )
        }

    monkeypatch.setattr(mod, "_fetch_wu_icao_daily_highs_lows", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    stats = mod.backfill_city(
        "Chicago",
        days_back=1,
        conn=conn,
        chunk_days=1,
        sleep_seconds=0,
    )
    assert stats["collected"] == 1

    row = conn.execute(
        """
        SELECT authority, source, station_id,
               high_provenance_metadata, low_provenance_metadata
        FROM observations
        WHERE city = 'Chicago' AND source = 'wu_icao_history'
        """
    ).fetchone()
    assert row is not None
    assert row["authority"] == "VERIFIED"
    high_provenance = json.loads(row["high_provenance_metadata"])
    low_provenance = json.loads(row["low_provenance_metadata"])
    _assert_provenance_identity(high_provenance)
    _assert_provenance_identity(low_provenance)
    assert high_provenance["station_id"] == row["station_id"] == "KORD"
    assert low_provenance["station_id"] == row["station_id"] == "KORD"
    assert "apiKey=" not in high_provenance["source_url"]
    assert high_provenance["api_key_redacted"] is True


def test_R8_hko_backfill_writes_non_empty_provenance_identity() -> None:
    mod = _load_script_module("backfill_hko_daily.py", "_backfill_hko_provenance")
    high_identity = mod._build_hko_payload_identity(
        year=2026,
        month=4,
        data_type="CLMMAXT",
        payload_hash="sha256:" + "b" * 64,
    )
    low_identity = mod._build_hko_payload_identity(
        year=2026,
        month=4,
        data_type="CLMMINT",
        payload_hash="sha256:" + "c" * 64,
    )
    provenance = mod._build_hko_daily_provenance(
        target_date=date(2026, 4, 2),
        high_identity=high_identity,
        low_identity=low_identity,
    )

    _assert_provenance_identity(provenance)
    assert provenance["source"] == "hko_daily_api"
    assert provenance["station_id"] == "HKO"
    assert (
        provenance["component_payload_hashes"]["CLMMAXT"]
        == high_identity["payload_hash"]
    )
    assert (
        provenance["component_payload_hashes"]["CLMMINT"]
        == low_identity["payload_hash"]
    )

    script_source = (PROJECT_ROOT / "scripts" / "backfill_hko_daily.py").read_text()
    assert (
        'provenance_metadata={"station": HKO_STATION, '
        '"dataType": ["CLMMAXT", "CLMMINT"]}'
        not in script_source
    )
    assert "write_daily_observation_with_revision" in script_source


def test_R8_hko_backfill_persists_provenance_identity(monkeypatch) -> None:
    mod = _load_script_module("backfill_hko_daily.py", "_backfill_hko_writer")
    conn = _legacy_observations_conn()

    def fake_fetch(year, month, data_type):
        value = 27.0 if data_type == "CLMMAXT" else 22.0
        payload_hash = "sha256:" + ("e" if data_type == "CLMMAXT" else "f") * 64
        identity = mod._build_hko_payload_identity(
            year=year,
            month=month,
            data_type=data_type,
            payload_hash=payload_hash,
        )
        return {(year, month, 2): (value, "C", identity)}, None

    monkeypatch.setattr(mod, "_fetch_hko_month_with_retry", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    stats = mod.run_backfill(
        date(2026, 4, 2),
        date(2026, 4, 2),
        conn=conn,
        rebuild_run_id="test_hko_provenance",
        sleep_seconds=0,
    )
    assert stats["inserted"] == 1

    row = conn.execute(
        """
        SELECT authority, source, station_id,
               high_provenance_metadata, low_provenance_metadata
        FROM observations
        WHERE city = 'Hong Kong' AND source = 'hko_daily_api'
        """
    ).fetchone()
    assert row is not None
    assert row["authority"] == "VERIFIED"
    high_provenance = json.loads(row["high_provenance_metadata"])
    low_provenance = json.loads(row["low_provenance_metadata"])
    _assert_provenance_identity(high_provenance)
    _assert_provenance_identity(low_provenance)
    assert high_provenance["station_id"] == row["station_id"] == "HKO"
    assert low_provenance["station_id"] == row["station_id"] == "HKO"
    assert high_provenance["component_payload_hashes"]["CLMMAXT"].startswith("sha256:")
    assert low_provenance["component_payload_hashes"]["CLMMINT"].startswith("sha256:")


# ---------------------------------------------------------------------------
# R9 — State-machine upsert rules: no demotions of terminal states
# ---------------------------------------------------------------------------


def test_R9_written_cannot_be_downgraded_to_failed() -> None:
    """Reviewer S1b: record_failed must not overwrite a WRITTEN row.

    Transient network error during a retry of a row that was already
    written on a prior tick would have flipped WRITTEN → FAILED without
    the state-machine upsert WHERE clause. Test asserts the WHERE
    clause actually prevents the demotion.
    """
    conn = _memdb()
    record_written(
        conn, data_table=DataTable.OBSERVATIONS, city="NYC",
        data_source="wu_icao_history", target_date="2026-04-10",
    )
    record_failed(
        conn, data_table=DataTable.OBSERVATIONS, city="NYC",
        data_source="wu_icao_history", target_date="2026-04-10",
        reason=CoverageReason.HTTP_429,
        retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    conn.commit()
    row = conn.execute(
        "SELECT status FROM data_coverage WHERE data_table='observations' "
        "AND city='NYC' AND target_date='2026-04-10'"
    ).fetchone()
    assert row["status"] == CoverageStatus.WRITTEN.value, (
        f"WRITTEN was demoted to {row['status']!r} by a subsequent record_failed"
    )


def test_R9_written_cannot_be_downgraded_to_missing() -> None:
    """A scanner re-scan must not demote a WRITTEN row to MISSING."""
    conn = _memdb()
    record_written(
        conn, data_table=DataTable.OBSERVATIONS, city="NYC",
        data_source="wu_icao_history", target_date="2026-04-10",
    )
    from src.state.data_coverage import record_missing
    record_missing(
        conn, data_table=DataTable.OBSERVATIONS, city="NYC",
        data_source="wu_icao_history", target_date="2026-04-10",
    )
    conn.commit()
    row = conn.execute(
        "SELECT status FROM data_coverage WHERE data_table='observations' "
        "AND city='NYC' AND target_date='2026-04-10'"
    ).fetchone()
    assert row["status"] == CoverageStatus.WRITTEN.value


def test_R9_legitimate_gap_can_overwrite_written() -> None:
    """LEGITIMATE_GAP is the authoritative upstream-invalidation signal
    and MUST be able to overwrite WRITTEN. This is the HKO C→# retroactive
    flip case: we wrote a row when HKO said "C", then HKO says "actually #".
    """
    conn = _memdb()
    record_written(
        conn, data_table=DataTable.OBSERVATIONS, city="Hong Kong",
        data_source="hko_daily_api", target_date="2026-04-10",
    )
    record_legitimate_gap(
        conn, data_table=DataTable.OBSERVATIONS, city="Hong Kong",
        data_source="hko_daily_api", target_date="2026-04-10",
        reason=CoverageReason.HKO_INCOMPLETE_FLAG,
    )
    conn.commit()
    row = conn.execute(
        "SELECT status, reason FROM data_coverage WHERE data_table='observations' "
        "AND city='Hong Kong' AND target_date='2026-04-10'"
    ).fetchone()
    assert row["status"] == CoverageStatus.LEGITIMATE_GAP.value
    assert row["reason"] == CoverageReason.HKO_INCOMPLETE_FLAG


def test_R9_legitimate_gap_can_upgrade_failed() -> None:
    """Reviewer S2-2: a deterministic guard rejection on a previously
    network-failed row should land as LEGITIMATE_GAP, not stay FAILED.
    """
    conn = _memdb()
    record_failed(
        conn, data_table=DataTable.OBSERVATIONS, city="Houston",
        data_source="wu_icao_history", target_date="2024-05-17",
        reason=CoverageReason.HTTP_429,
        retry_after=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    record_legitimate_gap(
        conn, data_table=DataTable.OBSERVATIONS, city="Houston",
        data_source="wu_icao_history", target_date="2024-05-17",
        reason=CoverageReason.GUARD_REJECTED,
    )
    conn.commit()
    row = conn.execute(
        "SELECT status FROM data_coverage WHERE city='Houston' AND target_date='2024-05-17'"
    ).fetchone()
    assert row["status"] == CoverageStatus.LEGITIMATE_GAP.value


def test_R9_legitimate_gap_terminal_no_failed_overwrite() -> None:
    """LEGITIMATE_GAP is terminal: once HKO/UKMO/scanner says "this row
    will never exist", a later FAILED must not un-say it.
    """
    conn = _memdb()
    record_legitimate_gap(
        conn, data_table=DataTable.OBSERVATIONS, city="Hong Kong",
        data_source="hko_daily_api", target_date="2026-04-11",
        reason=CoverageReason.HKO_INCOMPLETE_FLAG,
    )
    record_failed(
        conn, data_table=DataTable.OBSERVATIONS, city="Hong Kong",
        data_source="hko_daily_api", target_date="2026-04-11",
        reason=CoverageReason.HTTP_429,
        retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    conn.commit()
    row = conn.execute(
        "SELECT status FROM data_coverage WHERE city='Hong Kong' AND target_date='2026-04-11'"
    ).fetchone()
    assert row["status"] == CoverageStatus.LEGITIMATE_GAP.value


# ---------------------------------------------------------------------------
# R10 — Scanner self-seeds from physical tables (critic S2#2 fix)
# ---------------------------------------------------------------------------


def test_R10_scanner_self_seeds_from_physical_observations() -> None:
    """Reviewer/critic S2#2: a backfill script that inserts into
    observations without writing data_coverage must be self-healed by
    the scanner's first scan. The scanner queries the physical table
    and upserts WRITTEN rows for any (city, source, target_date) not
    yet tracked, before computing the expected/covered diff.
    """
    conn = _memdb()
    # Simulate a backfill-script write: INSERT into observations with
    # NO corresponding data_coverage row. This is exactly what the
    # currently-running backfill (PID 62478) does in production.
    conn.execute(
        """
        INSERT INTO observations (
            city, target_date, source, high_temp, low_temp, unit, authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("NYC", "2025-07-15", "wu_icao_history", 85.0, 70.0, "F", "VERIFIED"),
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM data_coverage "
        "WHERE city='NYC' AND target_date='2025-07-15'"
    ).fetchone()[0] == 0, "precondition: coverage must be empty"

    # Run a scan — the self-seed step should promote the physical row
    # to WRITTEN in data_coverage.
    scanner = HoleScanner(conn, today=date(2026, 4, 13))
    scanner.scan(ScannerDataTable.OBSERVATIONS)

    row = conn.execute(
        "SELECT status FROM data_coverage "
        "WHERE city='NYC' AND data_source='wu_icao_history' "
        "AND target_date='2025-07-15'"
    ).fetchone()
    assert row is not None, "scanner failed to self-seed the physical row"
    assert row["status"] == CoverageStatus.WRITTEN.value


# ---------------------------------------------------------------------------
# R11 — URL / MODEL drift tests between live and backfill
# ---------------------------------------------------------------------------


def test_R11_wu_url_matches_backfill() -> None:
    """WU ICAO URL must match between live and backfill so live/backfill
    rows come from the same API version. Endpoint drift = silent offset.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_backfill_wu", PROJECT_ROOT / "scripts" / "backfill_wu_daily_all.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert daily_obs_append.WU_ICAO_HISTORY_URL == mod.WU_ICAO_HISTORY_URL


def test_R11_hko_url_matches_backfill() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_backfill_hko", PROJECT_ROOT / "scripts" / "backfill_hko_daily.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert daily_obs_append.HKO_API_URL == mod.HKO_API_URL


def test_R11_openmeteo_archive_url_matches_backfill() -> None:
    import importlib.util
    from src.data.openmeteo_client import ARCHIVE_URL
    for backfill_name in [
        "backfill_hourly_openmeteo.py",
        "backfill_solar_openmeteo.py",
    ]:
        spec = importlib.util.spec_from_file_location(
            f"_{backfill_name}", PROJECT_ROOT / "scripts" / backfill_name,
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert ARCHIVE_URL == mod.OPENMETEO_ARCHIVE_URL, (
            f"openmeteo_client.ARCHIVE_URL and {backfill_name} disagree on "
            f"OPENMETEO_ARCHIVE_URL"
        )


def test_R11_forecasts_model_source_map_matches_backfill() -> None:
    """The per-model source-string map must be identical between
    live forecasts_append and backfill_openmeteo_previous_runs. A drift
    here would fragment per-model calibration buckets silently.

    Uses text-scan rather than importlib because the backfill script
    defines a frozen dataclass whose ClassVar resolution triggers a
    Python 3.14 dataclasses internal lookup that fails for modules
    imported via spec_from_file_location unless the module is
    registered in sys.modules first.
    """
    script_source = (
        PROJECT_ROOT / "scripts" / "backfill_openmeteo_previous_runs.py"
    ).read_text()
    # Expect every key/value pair from forecasts_append.MODEL_SOURCE_MAP
    # to be literally present in the backfill source. Text scan is
    # sufficient because both files have the same dict literal format.
    for model_key, source_value in forecasts_append.MODEL_SOURCE_MAP.items():
        expected_literal = f'"{model_key}": "{source_value}"'
        assert expected_literal in script_source, (
            f"backfill_openmeteo_previous_runs.py missing "
            f"MODEL_SOURCE_MAP[{model_key!r}] = {source_value!r}"
        )


# ---------------------------------------------------------------------------
# R12 — K2 scheduler smoke test
# ---------------------------------------------------------------------------


def test_R12_main_py_defines_all_k2_functions() -> None:
    """src/main.py must define all 6 _k2_* functions that the scheduler
    registers. A typo in a function name would silently drop a lane.
    """
    main_py = PROJECT_ROOT / "src" / "main.py"
    source = main_py.read_text()
    tree = ast.parse(source)
    funcs = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_k2_")
    }
    expected = {
        "_k2_daily_obs_tick",
        "_k2_hourly_instants_tick",
        "_k2_solar_daily_tick",
        "_k2_forecasts_daily_tick",
        "_k2_startup_catch_up",
        "_k2_hole_scanner_tick",
    }
    assert expected <= funcs, f"missing K2 functions: {expected - funcs}"


def test_R12_main_py_references_k2_job_ids() -> None:
    """src/main.py must register a scheduler job with each of the expected
    K2 job IDs. Verified via source-text scan rather than importing main
    (which would open DB connections and run startup checks).
    """
    main_py = (PROJECT_ROOT / "src" / "main.py").read_text()
    for job_id in (
        "k2_daily_obs",
        "k2_hourly_instants",
        "k2_solar_daily",
        "k2_forecasts_daily",
        "k2_startup_catch_up",
        "k2_hole_scanner",
    ):
        assert f'id="{job_id}"' in main_py, (
            f'main.py missing scheduler job id="{job_id}"'
        )


# ── R13 — guard→appender→coverage atomicity ──────────────────────────────


def _make_atom(
    city: str = "Chicago",
    target_d: date = date(2026, 4, 10),
    value_type: str = "high",
    value: float = 75.0,
) -> "ObservationAtom":
    """Build a minimal valid ObservationAtom for testing."""
    from src.types.observation_atom import ObservationAtom

    fetch_utc = datetime(target_d.year, target_d.month, target_d.day, 22, 0, 0, tzinfo=timezone.utc)
    local_time = datetime(target_d.year, target_d.month, target_d.day, 17, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
    return ObservationAtom(
        city=city,
        target_date=target_d,
        value_type=value_type,
        value=value,
        target_unit="F",
        raw_value=value,
        raw_unit="F",
        source="wu_icao_history",
        station_id="KORD",
        api_endpoint="https://api.weather.com/test",
        fetch_utc=fetch_utc,
        local_time=local_time,
        collection_window_start_utc=datetime(target_d.year, target_d.month, target_d.day, 6, 0, 0, tzinfo=timezone.utc),
        collection_window_end_utc=datetime(target_d.year, target_d.month, target_d.day, 22, 0, 0, tzinfo=timezone.utc),
        timezone="America/Chicago",
        utc_offset_minutes=-300,
        dst_active=True,
        is_ambiguous_local_hour=False,
        is_missing_local_hour=False,
        hemisphere="N",
        season="MAM",
        month=4,
        rebuild_run_id="test_r13",
        data_source_version="test_v1",
        authority="VERIFIED",
        validation_pass=True,
    )


def test_R13_write_atom_coverage_atomicity_happy_path() -> None:
    """When _write_atom_with_coverage succeeds, both a physical observations
    row AND a WRITTEN coverage ledger row must exist.  Neither may land
    without the other — the savepoint contract of S1.
    """
    from src.data.daily_obs_append import _write_atom_with_coverage

    conn = _memdb()
    target_d = date(2026, 4, 10)
    atom_high = _make_atom(value_type="high", value=75.0, target_d=target_d)
    atom_low = _make_atom(value_type="low", value=55.0, target_d=target_d)

    _write_atom_with_coverage(conn, atom_high, atom_low, data_source="wu_icao_history")
    conn.commit()

    # Physical row landed
    obs_rows = conn.execute(
        "SELECT * FROM observations WHERE city = ? AND target_date = ?",
        ("Chicago", target_d.isoformat()),
    ).fetchall()
    assert len(obs_rows) == 1, f"expected 1 obs row, got {len(obs_rows)}"

    # Coverage row landed with WRITTEN status
    cov_rows = conn.execute(
        "SELECT status FROM data_coverage WHERE city = ? AND target_date = ? AND data_table = ?",
        ("Chicago", target_d.isoformat(), DataTable.OBSERVATIONS.value),
    ).fetchall()
    assert len(cov_rows) == 1, f"expected 1 coverage row, got {len(cov_rows)}"
    assert cov_rows[0]["status"] == CoverageStatus.WRITTEN.value, (
        f"expected WRITTEN, got {cov_rows[0]['status']}"
    )


def test_R13_guard_rejection_records_legitimate_gap_no_obs_row() -> None:
    """When the guard rejects a datum and record_legitimate_gap is called,
    the coverage row must be LEGITIMATE_GAP and the observations table
    must have zero rows for that (city, date).  This is the second arm of
    the guard→appender→coverage contract.
    """
    conn = _memdb()
    target_d = date(2026, 4, 10)

    # Simulate guard rejection — record the gap directly (the real flow
    # catches IngestionRejected and calls this).
    record_legitimate_gap(
        conn,
        data_table=DataTable.OBSERVATIONS,
        city="Chicago",
        data_source="wu_icao_history",
        target_date=target_d,
        reason=CoverageReason.GUARD_REJECTED,
    )
    conn.commit()

    # No physical row
    obs_count = conn.execute(
        "SELECT count(*) FROM observations WHERE city = ? AND target_date = ?",
        ("Chicago", target_d.isoformat()),
    ).fetchone()[0]
    assert obs_count == 0, f"guard-rejected path should not write obs, got {obs_count}"

    # Coverage row exists as LEGITIMATE_GAP
    cov_rows = conn.execute(
        "SELECT status, reason FROM data_coverage WHERE city = ? AND target_date = ? AND data_table = ?",
        ("Chicago", target_d.isoformat(), DataTable.OBSERVATIONS.value),
    ).fetchall()
    assert len(cov_rows) == 1, f"expected 1 coverage row, got {len(cov_rows)}"
    assert cov_rows[0]["status"] == CoverageStatus.LEGITIMATE_GAP.value


def test_R13_obs_count_equals_written_count() -> None:
    """Cross-table invariant: every observations row must have a WRITTEN
    coverage partner and vice-versa.  This is the no-orphan guarantee.
    """
    from src.data.daily_obs_append import _write_atom_with_coverage

    conn = _memdb()
    dates = [date(2026, 4, d) for d in (10, 11, 12)]

    for d in dates:
        atom_high = _make_atom(value_type="high", value=75.0, target_d=d)
        atom_low = _make_atom(value_type="low", value=55.0, target_d=d)
        _write_atom_with_coverage(conn, atom_high, atom_low, data_source="wu_icao_history")

    # Also add a guard rejection (should NOT inflate obs count)
    record_legitimate_gap(
        conn,
        data_table=DataTable.OBSERVATIONS,
        city="Chicago",
        data_source="wu_icao_history",
        target_date=date(2026, 4, 13),
        reason=CoverageReason.GUARD_REJECTED,
    )
    conn.commit()

    obs_count = conn.execute("SELECT count(*) FROM observations WHERE city = 'Chicago'").fetchone()[0]
    written_count = conn.execute(
        "SELECT count(*) FROM data_coverage WHERE city = 'Chicago' AND data_table = ? AND status = ?",
        (DataTable.OBSERVATIONS.value, CoverageStatus.WRITTEN.value),
    ).fetchone()[0]

    assert obs_count == written_count == len(dates), (
        f"obs_count={obs_count}, written_count={written_count}, expected={len(dates)}"
    )
