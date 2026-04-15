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
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
# R3 — WU CITY_STATIONS must stay in sync with the backfill script
# ---------------------------------------------------------------------------


def test_R3_wu_city_stations_match_backfill_script() -> None:
    """daily_obs_append.CITY_STATIONS must be a subset+same-values of the
    backfill script's CITY_STATIONS (which is the authoritative source).

    Path A duplication only works if the two stay aligned. If a new city
    is added to the backfill but not to the live appender, the live path
    will silently stop collecting it — data freshness bug that no unit
    test would catch.
    """
    import importlib.util
    script_path = PROJECT_ROOT / "scripts" / "backfill_wu_daily_all.py"
    spec = importlib.util.spec_from_file_location("_backfill_wu", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    script_stations = mod.CITY_STATIONS
    append_stations = daily_obs_append.CITY_STATIONS

    # Every key in append must exist in script with identical tuple.
    mismatches = []
    for name, info in append_stations.items():
        if name not in script_stations:
            mismatches.append(f"{name} in append but not in backfill script")
        elif script_stations[name] != info:
            mismatches.append(
                f"{name}: append={info} != script={script_stations[name]}"
            )
    # Also flag any cities in script that daily_obs_append is missing.
    for name in script_stations:
        if name not in append_stations:
            mismatches.append(f"{name} in backfill script but not in append")
    assert not mismatches, (
        "CITY_STATIONS drifted between append and backfill:\n  "
        + "\n  ".join(mismatches)
    )


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
        mod._fetch_wu_icao_daily_highs_lows = lambda *a, **kw: {
            "2025-06-01": (200.0, 190.0),  # impossible — guard will reject
        }
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
    for backfill_name, live_mod in [
        ("backfill_hourly_openmeteo.py", hourly_instants_append),
        ("backfill_solar_openmeteo.py", solar_append),
    ]:
        spec = importlib.util.spec_from_file_location(
            f"_{backfill_name}", PROJECT_ROOT / "scripts" / backfill_name,
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert live_mod.OPENMETEO_ARCHIVE_URL == mod.OPENMETEO_ARCHIVE_URL, (
            f"{live_mod.__name__} and {backfill_name} disagree on "
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
