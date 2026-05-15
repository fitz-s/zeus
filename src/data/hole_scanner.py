"""K2 hole scanner: continuous data-coverage reconciliation.

Core of Zeus's live data-ingestion immune system. Periodically (and on
daemon boot) the scanner:

1. Computes the *expected* row set for each of the 4 upstream data tables
   (observations, observation_instants, solar_daily, forecasts). Expected
   rows are a cross product of configured cities, applicable data sources,
   and the target_date range [global_floor, today - publication_lag].

2. Queries the `data_coverage` ledger for *covered* rows (status=WRITTEN or
   LEGITIMATE_GAP).

3. Computes the diff — expected minus covered — these are the holes.

4. Applies the static whitelist from `config/data_availability_exceptions.yaml`
   to pin holes that legitimately cannot be filled:
     - target_date before model retro start → LEGITIMATE_GAP
     - target_date before city onboarded_at → LEGITIMATE_GAP
     - target_date inside publication-lag window → not a hole (skip)

5. Writes the remaining holes as MISSING rows in `data_coverage` so that
   downstream fillers (`src/data/*_append.py`) can act on them via
   `find_pending_fills`.

Design note: scanner is read-only with respect to the data tables themselves.
It never INSERTs into observations / observation_instants / solar_daily /
forecasts. Its only write target is `data_coverage`. Fill is a separate
operation that calls the live-append modules, keeping the detection and
remediation phases cleanly separated.

Not scope of this module:
- Actually fetching the holes — that's `_append.py` modules + a fill driver
- Online verifiers (HKO API cross-check) — stubbed here; implemented by
  `src/data/hole_scanner_verifiers.py` in a later step
- APScheduler integration — `src/main.py` owns job registration
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

from src.config import City, cities as ALL_CITIES, cities_by_name
from src.data.forecast_source_registry import forecast_table_source_ids
from src.state.data_coverage import (
    CoverageReason,
    CoverageStatus,
    DataTable,
    count_by_status,
    coverage_summary,
    find_pending_fills,
    record_legitimate_gap,
    record_missing,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXCEPTIONS_PATH = PROJECT_ROOT / "config" / "data_availability_exceptions.yaml"


# ---------------------------------------------------------------------------
# Source registry — which sources exist per data_table, how they key
# ---------------------------------------------------------------------------
# Each data_table has a set of canonical data_source strings. This registry
# is the single source of truth the scanner uses to build expected sets and
# that live appenders must match when writing coverage rows.

SOURCES_BY_TABLE: dict[DataTable, tuple[str, ...]] = {
    DataTable.OBSERVATIONS: ("wu_icao_history", "hko_daily_api"),
    DataTable.OBSERVATION_INSTANTS: ("openmeteo_archive_hourly",),
    DataTable.SOLAR_DAILY: ("openmeteo_archive_solar",),
    DataTable.FORECASTS: forecast_table_source_ids(),
}


def _source_applies_to_city(data_source: str, city: City) -> bool:
    """Return True if this data_source is the one Zeus uses for this city.

    For observations, the split is derived from ``city.settlement_source_type``:
    WU-sourced cities use ``wu_icao_history``; HKO-sourced cities use
    ``hko_daily_api``.  For every other table, all cities share the same source.
    """
    if data_source == "wu_icao_history":
        return city.settlement_source_type != "hko"
    if data_source == "hko_daily_api":
        return city.settlement_source_type == "hko"
    return True


# ---------------------------------------------------------------------------
# Exceptions config loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExceptionsConfig:
    """Parsed view of `config/data_availability_exceptions.yaml`.

    Frozen dataclass so callers can safely hold a reference across scans
    without worrying about mutation races during long HkoOnlineVerifier
    calls.
    """

    model_retro_starts: dict[str, date]
    publication_lag_days: dict[str, int]
    global_onboarding_floor: date
    read_cities_json_onboarded_at: bool
    auto_fill_ceiling_days: int
    auto_alert_floor_days: int
    max_holes_per_city_per_scan: int
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = DEFAULT_EXCEPTIONS_PATH) -> "ExceptionsConfig":
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(
            model_retro_starts={
                k: date.fromisoformat(v)
                for k, v in (raw.get("model_retro_starts") or {}).items()
            },
            publication_lag_days=dict(raw.get("publication_lag_days") or {}),
            global_onboarding_floor=date.fromisoformat(
                (raw.get("onboarding") or {}).get("global_floor", "2024-01-01")
            ),
            read_cities_json_onboarded_at=bool(
                (raw.get("onboarding") or {}).get("read_cities_json", True)
            ),
            auto_fill_ceiling_days=int(
                (raw.get("fill_policy") or {}).get("auto_fill_ceiling_days", 7)
            ),
            auto_alert_floor_days=int(
                (raw.get("fill_policy") or {}).get("auto_alert_floor_days", 30)
            ),
            max_holes_per_city_per_scan=int(
                (raw.get("fill_policy") or {}).get("max_holes_per_city_per_scan", 90)
            ),
            raw=raw,
        )

    def city_onboard_floor(self, city: City) -> date:
        """Date before which this city has no expected rows.

        Prefers City.onboarded_at if the flag is enabled and present, else
        falls back to the global floor (2024-01-01).
        """
        if self.read_cities_json_onboarded_at:
            onboarded = getattr(city, "onboarded_at", None)
            if onboarded:
                try:
                    if isinstance(onboarded, date):
                        return onboarded
                    return date.fromisoformat(str(onboarded))
                except (ValueError, TypeError):
                    logger.warning(
                        "City %s has unparseable onboarded_at=%r; using global floor",
                        city.name,
                        onboarded,
                    )
        return self.global_onboarding_floor

    def source_upper_bound(self, data_source: str, today: date) -> date:
        """Highest target_date that could legitimately have a row right now.

        = today - publication_lag_days[source]. Rows with target_date beyond
        this are not yet expected upstream, so not holes.
        """
        lag = self.publication_lag_days.get(data_source, 2)
        return today - timedelta(days=lag)

    def source_lower_bound(self, data_source: str) -> date:
        """Lowest target_date the source can supply.

        For NWP models this is the retro window start (e.g. UKMO 2024-08-04).
        For other sources this is the global onboarding floor.
        """
        retro = self.model_retro_starts.get(data_source)
        return retro if retro else self.global_onboarding_floor


# ---------------------------------------------------------------------------
# Expected set builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedRow:
    city: str
    data_source: str
    target_date: str  # ISO


def _iter_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_expected_set(
    data_table: DataTable,
    *,
    today: date,
    config: ExceptionsConfig,
    city_list: Optional[list[City]] = None,
) -> list[ExpectedRow]:
    """Enumerate (city, data_source, target_date) rows that should exist.

    The expected-set window is deliberately *broad*: [global_floor,
    source_upper_bound(source)]. Per-city onboarding floors and per-source
    retro-start dates are NOT applied here — they are applied in the scan
    loop as LEGITIMATE_GAP pins. This gives an operator querying
    data_coverage an explicit row for every pre-retro / pre-onboard date
    with a reason code, instead of silent exclusion that makes "why is
    there no UKMO data for 2024-05-15?" unanswerable.

    Only `publication_lag_days` is honored here (as the upper bound), since
    rows inside the lag window are genuinely not-yet-available upstream and
    should not count as holes even in observability terms.
    """
    cities = city_list if city_list is not None else list(ALL_CITIES)
    sources = SOURCES_BY_TABLE[data_table]
    expected: list[ExpectedRow] = []

    window_start = config.global_onboarding_floor
    for source in sources:
        src_upper = config.source_upper_bound(source, today)
        if src_upper < window_start:
            continue
        for city in cities:
            if not _source_applies_to_city(source, city):
                continue
            for d in _iter_dates(window_start, src_upper):
                expected.append(
                    ExpectedRow(
                        city=city.name,
                        data_source=source,
                        target_date=d.isoformat(),
                    )
                )
    return expected


# ---------------------------------------------------------------------------
# Hole scanner
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """Summary of one scan pass for one data_table."""

    data_table: DataTable
    expected_count: int
    covered_count: int
    hole_count: int
    pinned_legitimate_gap: int
    recorded_missing: int
    pending_fill: int

    def as_dict(self) -> dict:
        return {
            "data_table": self.data_table.value,
            "expected": self.expected_count,
            "covered": self.covered_count,
            "holes": self.hole_count,
            "pinned_legitimate_gap": self.pinned_legitimate_gap,
            "recorded_missing": self.recorded_missing,
            "pending_fill": self.pending_fill,
        }


class HoleScanner:
    """Scans `data_coverage` for missing (city × source × target_date) rows.

    Typical usage:
        scanner = HoleScanner(conn, config=ExceptionsConfig.load())
        result = scanner.scan(DataTable.OBSERVATIONS)
        # or run against all tables:
        results = scanner.scan_all()

    The scanner is non-destructive with respect to existing WRITTEN and
    LEGITIMATE_GAP rows — it only inserts new MISSING rows for holes that
    are not already tracked and not covered by the whitelist.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        forecasts_conn: Optional[sqlite3.Connection] = None,
        config: Optional[ExceptionsConfig] = None,
        today: Optional[date] = None,
    ):
        self.conn = conn
        # Post-K1 split: observations table lives on forecasts.db, not world.db.
        # Pass forecasts_conn so physical-table queries for DataTable.OBSERVATIONS
        # are routed to the correct DB. Falls back to conn (world.db) for all
        # other tables and for backward-compat callers that don't pass it.
        self.forecasts_conn = forecasts_conn if forecasts_conn is not None else conn
        self.config = config if config is not None else ExceptionsConfig.load()
        self.today = today if today is not None else date.today()

    def _get_covered_keys(self, data_table: DataTable) -> set[tuple[str, str, str]]:
        """Return {(city, data_source, target_date)} already in data_coverage
        with status WRITTEN or LEGITIMATE_GAP.

        These rows do not need re-scanning; the scanner's job is to find the
        complement set against the expected universe.
        """
        rows = self.conn.execute(
            """
            SELECT city, data_source, target_date
            FROM data_coverage
            WHERE data_table = ?
              AND status IN ('WRITTEN', 'LEGITIMATE_GAP')
            """,
            (data_table.value,),
        ).fetchall()
        return {(r[0], r[1], r[2]) for r in rows}

    # ------------------------------------------------------------------
    # Self-seeding from physical tables (critic S2#2 fix)
    # ------------------------------------------------------------------
    # Backfill scripts and any other non-K2 writer (legacy collectors,
    # manual SQL, ETL patches) do not write to data_coverage. On first
    # scan after a backfill run, the coverage ledger is empty but the
    # physical tables are full — scanner would flood with MISSING and
    # pending fills would re-fetch everything the system already has.
    # Self-seeding closes that drift by querying the physical table
    # directly and inserting WRITTEN rows for any present data not yet
    # tracked. Runs on every scan call; idempotent via the state-machine
    # upsert (WRITTEN never downgrades).

    _PHYSICAL_TABLE_DISTINCT_SQL: dict[str, str] = {
        "observations": (
            "SELECT DISTINCT city, source AS data_source, target_date "
            "FROM observations WHERE source IS NOT NULL AND target_date IS NOT NULL"
        ),
        # observation_instants is written at per-hour grain but coverage
        # is tracked at per-local_date grain. Derive local_date from the
        # local_timestamp column (dialect: SQLite substr).
        "observation_instants": (
            "SELECT DISTINCT city, source AS data_source, "
            "substr(local_timestamp, 1, 10) AS target_date "
            "FROM observation_instants "
            "WHERE source IS NOT NULL AND local_timestamp IS NOT NULL"
        ),
        # solar_daily has no `source` column — it's always openmeteo_archive_solar.
        "solar_daily": (
            "SELECT DISTINCT city, 'openmeteo_archive_solar' AS data_source, "
            "target_date FROM solar_daily WHERE target_date IS NOT NULL"
        ),
        "forecasts": (
            "SELECT DISTINCT city, source AS data_source, target_date "
            "FROM forecasts WHERE source IS NOT NULL AND target_date IS NOT NULL"
        ),
    }

    def _get_physical_table_keys(
        self, data_table: DataTable
    ) -> set[tuple[str, str, str]]:
        """Query the actual data table for all (city, source, target_date)
        rows physically present, regardless of whether data_coverage has
        caught up with them.

        Post-K1 routing: observations live on forecasts.db (self.forecasts_conn);
        all other tables live on world.db (self.conn).
        """
        sql = self._PHYSICAL_TABLE_DISTINCT_SQL.get(data_table.value)
        if sql is None:
            return set()
        # Route observations to forecasts.db; everything else stays on world.db.
        target_conn = (
            self.forecasts_conn
            if data_table == DataTable.OBSERVATIONS
            else self.conn
        )
        try:
            rows = target_conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            # Table absent from this conn — return empty; scanner treats as no
            # physical data (conservative: no spurious WRITTEN seeds).
            return set()
        return {(r[0], r[1], r[2]) for r in rows}

    def _seed_coverage_from_physical(
        self, data_table: DataTable, covered_keys: set[tuple[str, str, str]],
    ) -> int:
        """For every (city, source, target_date) present in the physical
        table but NOT in data_coverage's WRITTEN/LEGITIMATE_GAP set,
        insert a WRITTEN coverage row.

        Returns the number of rows seeded. Idempotent (state-machine upsert).
        """
        from src.state.data_coverage import bulk_record_written

        physical_keys = self._get_physical_table_keys(data_table)
        # Subtract what's already tracked to avoid redundant upserts.
        to_seed = physical_keys - covered_keys
        if not to_seed:
            return 0
        rows_tuple: list[tuple[str, str, str, str]] = [
            (city, source, target_date, "")
            for (city, source, target_date) in to_seed
        ]
        n = bulk_record_written(
            self.conn, data_table=data_table, rows=rows_tuple,
        )
        self.conn.commit()
        return n

    def _get_missing_or_failed_keys(
        self, data_table: DataTable
    ) -> set[tuple[str, str, str]]:
        """Rows already marked MISSING or FAILED — do not re-insert MISSING
        for these, just track them.
        """
        rows = self.conn.execute(
            """
            SELECT city, data_source, target_date
            FROM data_coverage
            WHERE data_table = ?
              AND status IN ('MISSING', 'FAILED')
            """,
            (data_table.value,),
        ).fetchall()
        return {(r[0], r[1], r[2]) for r in rows}

    def scan(self, data_table: DataTable) -> ScanResult:
        """Run one scan pass for a single data_table.

        Pre-step: self-seed data_coverage from the physical table for any
        rows present but not yet tracked. This closes coverage-ledger
        drift from backfill scripts and other non-K2 writers (critic
        S2#2). On first scan after a large backfill run, this seeds ~N
        rows where N = rows in physical table that had no coverage entry.
        Idempotent via state-machine upsert.

        Then: writes new MISSING rows for undetected holes and LEGITIMATE_GAP
        rows for whitelist-matched exceptions. Commits before returning.
        """
        expected = build_expected_set(
            data_table, today=self.today, config=self.config
        )
        expected_keys = {
            (r.city, r.data_source, r.target_date) for r in expected
        }
        pre_covered = self._get_covered_keys(data_table)
        seeded_from_physical = self._seed_coverage_from_physical(
            data_table, pre_covered,
        )
        # Re-read covered keys after self-seed so the diff reflects
        # any rows just promoted from physical → WRITTEN.
        covered_keys = (
            self._get_covered_keys(data_table)
            if seeded_from_physical > 0
            else pre_covered
        )
        tracked_keys = self._get_missing_or_failed_keys(data_table)

        # Holes = expected - covered - already-tracked
        # Already-tracked (MISSING/FAILED) stay as-is; scanner just counts
        # them as pending. Only brand-new holes get INSERTed.
        untracked_holes = expected_keys - covered_keys - tracked_keys

        recorded_missing = 0
        pinned_legit = 0
        for key in untracked_holes:
            city_name, source, target_str = key
            target = date.fromisoformat(target_str)
            # Re-check whitelist per-row in case config evolves between
            # build_expected_set and here (cheap — all in-memory).
            pin_reason = self._static_whitelist_reason(city_name, source, target)
            if pin_reason is not None:
                record_legitimate_gap(
                    self.conn,
                    data_table=data_table,
                    city=city_name,
                    data_source=source,
                    target_date=target_str,
                    reason=pin_reason,
                )
                pinned_legit += 1
            else:
                record_missing(
                    self.conn,
                    data_table=data_table,
                    city=city_name,
                    data_source=source,
                    target_date=target_str,
                )
                recorded_missing += 1

        self.conn.commit()

        pending = len(find_pending_fills(self.conn, data_table=data_table, max_rows=1_000_000))
        return ScanResult(
            data_table=data_table,
            expected_count=len(expected_keys),
            covered_count=len(covered_keys),
            hole_count=len(untracked_holes) + len(tracked_keys - covered_keys),
            pinned_legitimate_gap=pinned_legit,
            recorded_missing=recorded_missing,
            pending_fill=pending,
        )

    def scan_all(self) -> list[ScanResult]:
        """Run scan for every known data_table in a predictable order."""
        return [self.scan(t) for t in DataTable]

    def _static_whitelist_reason(
        self, city_name: str, data_source: str, target: date
    ) -> Optional[str]:
        """Return a LEGITIMATE_GAP reason code if this row is whitelisted.

        Build_expected_set already filters out most whitelist cases (retro
        windows, city onboarding). This method exists as a second-pass
        defense and to cover edge cases that the expected-set filter may
        not handle in future revisions.
        """
        city = cities_by_name.get(city_name)
        if city is None:
            return None
        # City onboarded_at gate
        city_floor = self.config.city_onboard_floor(city)
        if target < city_floor:
            return CoverageReason.CITY_NOT_YET_ONBOARDED
        # Model retro start gate
        retro_start = self.config.model_retro_starts.get(data_source)
        if retro_start is not None and target < retro_start:
            if "ukmo" in data_source:
                return CoverageReason.UKMO_PRE_START
            return CoverageReason.SOURCE_NOT_PUBLISHED_YET
        return None


# ---------------------------------------------------------------------------
# CLI — `python -m src.data.hole_scanner` for manual ops / smoke tests
# ---------------------------------------------------------------------------


def _cli_report(conn: sqlite3.Connection) -> None:
    print("=== data_coverage summary ===")
    for table in DataTable:
        counts = count_by_status(conn, data_table=table)
        total = sum(counts.values())
        print(
            f"  {table.value:24s} "
            f"total={total:>10,} "
            f"written={counts.get('WRITTEN', 0):>10,} "
            f"legit={counts.get('LEGITIMATE_GAP', 0):>7,} "
            f"failed={counts.get('FAILED', 0):>6,} "
            f"missing={counts.get('MISSING', 0):>7,}"
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="K2 data-coverage hole scanner.",
    )
    parser.add_argument(
        "--scan",
        choices=[t.value for t in DataTable] + ["all"],
        help="Run a scan pass on one table (or 'all').",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print data_coverage status summary and exit (read-only).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.state.db import (
        get_world_connection,
        get_forecasts_connection,
        assert_schema_current,
    )

    # Post-K1 split: observations live on forecasts.db; all other scanner
    # tables (data_coverage, observation_instants, solar_daily, forecasts)
    # live on world.db. Open both connections.
    conn = get_world_connection()
    forecasts_conn = get_forecasts_connection()
    assert_schema_current(conn)

    if args.report:
        _cli_report(conn)
        conn.close()
        forecasts_conn.close()
        return 0

    if args.scan:
        scanner = HoleScanner(conn, forecasts_conn=forecasts_conn)
        if args.scan == "all":
            results = scanner.scan_all()
        else:
            target_table = DataTable(args.scan)
            results = [scanner.scan(target_table)]
        print(json.dumps([r.as_dict() for r in results], indent=2))
        conn.close()
        forecasts_conn.close()
        return 0

    parser.print_help()
    conn.close()
    forecasts_conn.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
