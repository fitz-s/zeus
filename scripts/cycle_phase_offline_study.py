#!/usr/bin/env python3
# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~05:00Z (cycle policy + 06/18Z deep offline
#   investigation), docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md §OPERATOR
#   DIRECTIVES + K4.0b(d). PURE OFFLINE study: live DBs READ-ONLY, no daemon/flag/src-live edits.
#   Writes ONLY to a scratch DB (state/cycle_phase_study.db). NEVER live zeus-forecasts.db.
"""Offline 06Z/18Z cycle-phase qualification study.

Backfills + re-materializes replacement-forecast posteriors for SETTLED past targets across
all four model-cycle phases (00/06/12/18Z) into a SCRATCH database, using the SAME fusion /
materialization code path the live pipeline uses (imported, pointed at the scratch conn), then
settlement-grades each phase on identical family-days.

Stages (sub-commands):
  hydrate     - copy the live read-only substrate (raw_model_forecasts, raw_forecast_artifacts,
                source_run_coverage, source_run, market_events, settlement_outcomes) into the
                scratch DB, preserving primary-key IDs (the materializer identity gates check
                artifact_id + raw_model_forecast natural keys against these rows).
  materialize - for each (settled target x phase x lead-1 cycle x city x metric) with all inputs
                present, build the materialize request from the on-disk AIFS GRIB + OM9 anchor
                payload and call materialize_replacement_forecast_live(scratch_conn, request).
  grade       - join scratch posteriors to VERIFIED settlement truth and emit per-phase metrics.

WHY SCRATCH-FAITHFUL: materialize_replacement_forecast_live(conn, request) consumes raw_model_
forecasts + settlement history from the PASSED conn for bayes_precision_fusion; the scratch DB carries those
rows verbatim, and the flags read from config are the live flags, so a scratch posterior is
byte-faithful to what live would have produced from the same cycle. EB-bias + sigma-floor lookups
open the live world DB read-only (flag-gated; EB bias is OFF in current config).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIVE_FORECASTS_DB = ROOT / "state" / "zeus-forecasts.db"
LIVE_TRADES_DB = ROOT / "state" / "zeus_trades.db"
SCRATCH_DB = ROOT / "state" / "cycle_phase_study.db"

# Tables copied verbatim from the live forecasts DB (read-only) into the scratch DB. IDs are
# preserved so the materializer's artifact-identity + fusion natural-key lookups resolve.
_COPY_TABLES = (
    "raw_model_forecasts",
    "raw_forecast_artifacts",
    "source_run_coverage",
    "source_run",
    "market_events",
    "settlement_outcomes",
)

_PHASES = (0, 6, 12, 18)
_SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"

# True per-cycle publication lag (hours). AIFS-ENS open data publishes ~+8h after the cycle
# (consolidated_systemic_overhaul K4.0b: "AIFS-ENS publishes ~+8h"); OM9 single-runs is similar.
# This is the SHARED faithful availability used for the pre-day decision model so every phase is
# evaluated under identical publication-lag assumptions (the only phase difference is cycle hour).
_PHASE_PUBLICATION_LAG_HOURS = 8.0


def _vlog(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# STAGE: hydrate
# ---------------------------------------------------------------------------
def hydrate(scratch_db: Path, *, live_db: Path, force: bool) -> dict[str, object]:
    if scratch_db.exists() and not force:
        raise SystemExit(f"{scratch_db} exists; pass --force to rebuild")
    if scratch_db.exists():
        scratch_db.unlink()
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(scratch_db))
    counts: dict[str, int] = {}
    try:
        conn.execute(f"ATTACH DATABASE 'file:{live_db}?mode=ro' AS live")
        for table in _COPY_TABLES:
            # Copy the EXACT schema (CREATE TABLE + its UNIQUE/PK constraints + indices) from live,
            # then bulk-INSERT the rows. CREATE ... AS SELECT would drop the UNIQUE constraints, and
            # write_manifest_to_db's INSERT ... ON CONFLICT requires the raw_forecast_artifacts
            # natural-key UNIQUE index to exist (the backfill stage writes new artifact rows).
            create_sql = conn.execute(
                "SELECT sql FROM live.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            conn.execute(create_sql)
            conn.execute(f"INSERT INTO {table} SELECT * FROM live.{table}")
            # Recreate the table's indices (including UNIQUE ones the ON CONFLICT target needs).
            for (idx_sql,) in conn.execute(
                "SELECT sql FROM live.sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            ).fetchall():
                try:
                    conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass  # auto-created/duplicate index names — skip
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = int(n)
            _vlog(f"  copied {table}: {n} rows")
        conn.commit()
        conn.execute("DETACH DATABASE live")
        # Helpful indices for the materializer's natural-key reads (live has them; the copy does not).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rmf_nat ON raw_model_forecasts(city, metric, target_date, source_cycle_time, endpoint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rfa_artifact ON raw_forecast_artifacts(artifact_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_me_nat ON market_events(city, target_date, temperature_metric)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_so_nat ON settlement_outcomes(city, target_date, temperature_metric, authority)"
        )
        # Initialise the replacement-forecast write schema (posteriors / anchors / readiness)
        # on the scratch DB so materialization has a place to write.
        from src.state.db import _create_readiness_state  # noqa: PLC0415
        from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: PLC0415

        ensure_replacement_forecast_live_schema(conn)
        _create_readiness_state(conn)
        conn.commit()
    finally:
        conn.close()
    return {"status": "HYDRATED", "scratch_db": str(scratch_db), "row_counts": counts}


# ---------------------------------------------------------------------------
# STAGE: materialize
# ---------------------------------------------------------------------------
def _local_day_window(tz_name: str, target_date: str) -> tuple[datetime, datetime]:
    d = date.fromisoformat(target_date)
    zone = ZoneInfo(tz_name)
    start = datetime(d.year, d.month, d.day, tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _aifs_steps_for_target(cycle: datetime, tz_name: str, target_date: str) -> tuple[int, ...]:
    start_utc, end_utc = _local_day_window(tz_name, target_date)
    steps = []
    for step in range(0, 241, 6):
        valid = cycle + timedelta(hours=step)
        if start_utc <= valid < end_utc:
            steps.append(step)
    return tuple(steps)


def _find_aifs_artifact(
    conn: sqlite3.Connection, *, cycle_iso: str, metric: str, target_date: str
) -> dict | None:
    """Return the best on-disk AIFS GRIB artifact row covering (cycle, metric, target_date)."""
    rows = conn.execute(
        """
        SELECT artifact_id, artifact_path, sha256, source_cycle_time, source_available_at,
               product_id, artifact_metadata_json
        FROM raw_forecast_artifacts
        WHERE source_id='ecmwf_aifs_ens' AND source_cycle_time = ?
        ORDER BY artifact_id
        """,
        (cycle_iso,),
    ).fetchall()
    best = None
    for r in rows:
        md = json.loads(r[6] or "{}")
        if str(md.get("metric") or "") != metric:
            continue
        tdates = md.get("target_dates") or ([md.get("target_date")] if md.get("target_date") else [])
        if target_date not in tdates:
            continue
        if not Path(str(r[1])).is_file():
            continue
        # Prefer the widest-coverage file (most cities) so per-city extraction works.
        ncities = len(md.get("cities") or [])
        cand = {
            "artifact_id": int(r[0]),
            "artifact_path": str(r[1]),
            "sha256": str(r[2]),
            "source_cycle_time": str(r[3]),
            "source_available_at": str(r[4]),
            "product_id": str(r[5]),
            "ncities": ncities,
            "source_run_id": md.get("source_run_id"),
            "cities": md.get("cities") or [],
        }
        if best is None or ncities > best["ncities"]:
            best = cand
    return best


def _find_om9_artifact(
    conn: sqlite3.Connection, *, cycle_iso: str, metric: str, target_date: str, city: str
) -> dict | None:
    rows = conn.execute(
        """
        SELECT artifact_id, artifact_path, sha256, source_cycle_time, source_available_at,
               product_id, artifact_metadata_json
        FROM raw_forecast_artifacts
        WHERE source_id='openmeteo_ecmwf_ifs_9km' AND source_cycle_time = ?
        ORDER BY artifact_id
        """,
        (cycle_iso,),
    ).fetchall()
    for r in rows:
        md = json.loads(r[6] or "{}")
        if str(md.get("metric") or "") != metric:
            continue
        if str(md.get("city") or "") != city:
            continue
        tdates = md.get("target_dates") or ([md.get("target_date")] if md.get("target_date") else [])
        if target_date not in tdates:
            continue
        payload_path = md.get("openmeteo_payload_json")
        precision_path = md.get("precision_metadata_json")
        if not payload_path or not Path(str(payload_path)).is_file():
            continue
        if not precision_path or not Path(str(precision_path)).is_file():
            continue
        return {
            "artifact_id": int(r[0]),
            "artifact_path": str(r[1]),
            "sha256": str(r[2]),
            "source_cycle_time": str(r[3]),
            "source_available_at": str(r[4]),
            "product_id": str(r[5]),
            "source_run_id": md.get("source_run_id"),
            "openmeteo_payload_json": str(payload_path),
            "precision_metadata_json": str(precision_path),
        }
    return None


def _baseline_coverage(conn: sqlite3.Connection, *, city: str, target_date: str, metric: str):
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        latest_baseline_coverage_for_replacement_seed,
    )

    conn.row_factory = sqlite3.Row
    return latest_baseline_coverage_for_replacement_seed(
        conn, city=city, target_date=target_date, temperature_metric=metric
    )


def _market_bins(conn: sqlite3.Connection, *, city: str, target_date: str, metric: str, settlement_unit: str, rounding_rule: str):
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        market_bins_for_replacement_seed,
        _market_bins_to_celsius,
    )
    from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin  # noqa: PLC0415

    conn.row_factory = sqlite3.Row
    raw_bins = market_bins_for_replacement_seed(conn, city=city, target_date=target_date, temperature_metric=metric)
    if not raw_bins:
        return None
    celsius = _market_bins_to_celsius(raw_bins, settlement_unit=settlement_unit, rounding_rule=rounding_rule)
    return tuple(
        AifsTemperatureBin(
            bin_id=str(b["bin_id"]),
            lower_c=None if b["lower_c"] is None else float(b["lower_c"]),
            upper_c=None if b["upper_c"] is None else float(b["upper_c"]),
            center_c=None if b["center_c"] is None else float(b["center_c"]),
            display_unit=str(b["display_unit"]),
            settlement_unit=str(b["settlement_unit"]),
            rounding_rule=str(b["rounding_rule"]),
        )
        for b in celsius
    )


def _materialize_one(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    cycle: datetime,
    aifs: dict,
    om9: dict,
) -> dict:
    from src.config import cities_by_name  # noqa: PLC0415
    from src.data.ecmwf_aifs_grib_samples import extract_aifs_2t_point_samples_from_grib  # noqa: PLC0415
    from src.data.ecmwf_aifs_sampled_2t_localday import extract_aifs_sampled_2t_localday  # noqa: PLC0415
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor  # noqa: PLC0415
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import (  # noqa: PLC0415
        OpenMeteoIfs9PrecisionMetadata,
        evaluate_openmeteo_ecmwf_ifs9_precision_guard,
    )
    from src.data.replacement_forecast_materializer import (  # noqa: PLC0415
        ReplacementForecastMaterializeRequest,
        materialize_replacement_forecast_live,
    )
    from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
        expected_replacement_dependency_identity_by_role,
    )

    city_cfg = cities_by_name[city]
    settlement_unit = str(getattr(city_cfg, "settlement_unit", "C"))
    rounding_rule = "oracle_truncate" if str(getattr(city_cfg, "settlement_source_type", "") or "") == "hko" else "wmo_half_up"
    settlement_step_c = 1.0 if settlement_unit.upper() == "C" else 5.0 / 9.0

    bins = _market_bins(conn, city=city, target_date=target_date, metric=metric, settlement_unit=settlement_unit, rounding_rule=rounding_rule)
    if not bins:
        return {"status": "SKIP", "reason": "NO_MARKET_BINS"}
    baseline = _baseline_coverage(conn, city=city, target_date=target_date, metric=metric)
    if baseline is None:
        return {"status": "SKIP", "reason": "NO_BASELINE_COVERAGE"}

    # AIFS samples from the on-disk GRIB (point extraction at city coords).
    extraction = extract_aifs_2t_point_samples_from_grib(
        aifs["artifact_path"],
        latitude=float(city_cfg.lat),
        longitude=float(city_cfg.lon),
        source_cycle_time=cycle,
    )
    aifs_local = extract_aifs_sampled_2t_localday(
        list(extraction.samples),
        city_timezone=city_cfg.timezone,
        target_local_date=date.fromisoformat(target_date),
        source_cycle_time=cycle,
    )
    aifs_local = replace(
        aifs_local,
        artifact_id=aifs["artifact_id"],
        identity_decision_valid=True,
        identity_reason_codes=tuple(extraction.identity_reason_codes),
        identity_decision_hash=extraction.identity_decision_hash,
        member_ids_hash=__import__("hashlib").sha256(json.dumps(tuple(extraction.member_ids), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        step_hours_hash=__import__("hashlib").sha256(json.dumps(tuple(extraction.step_hours), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        raw_sha256=extraction.raw_sha256,
    )

    om_payload = json.loads(Path(om9["openmeteo_payload_json"]).read_text())
    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        om_payload,
        city_timezone=city_cfg.timezone,
        target_local_date=date.fromisoformat(target_date),
        source_cycle_time=cycle,
    )
    precision_payload = json.loads(Path(om9["precision_metadata_json"]).read_text())
    precision_guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**precision_payload)
    )

    # ----- FAITHFUL DECISION-TIME MODEL (pre-day, lead-1) -----
    # The recorded source_available_at values are LATE-CAPTURE timestamps (when Zeus downloaded
    # the backfill), not true model publication. For a faithful lead-1 decision we use the TRUE
    # per-leg publication lag (AIFS-ENS ~+8h, K4.0b; OM9 single-runs ~+8h) and pin computed_at to
    # JUST BEFORE the target local-day window opens — the "evening before" decision regime.
    #
    # A phase whose data only publishes AFTER the window opens is HONESTLY an intraday/DAY0
    # decision: its dependency availability lands after a pre-window computed_at, so the
    # materializer's DEPENDENCY_AFTER_COMPUTED_AT / DAY0 gate blocks it. That block is the
    # signal, not a bug — later phases (12/18Z) structurally lose pre-day usability for many
    # cities (the publication-timing penalty is itself a phase-quality result, reported in grade).
    target_window_start_utc, _ = _local_day_window(city_cfg.timezone, target_date)
    pub_avail = cycle + timedelta(hours=_PHASE_PUBLICATION_LAG_HOURS)
    # Pre-day decision instant: one minute before the local day opens (never in-window).
    computed_at = target_window_start_utc - timedelta(minutes=1)
    # Faithful dependency availability = the cycle's true publication. If a dep published after
    # the decision instant, the gate will (correctly) block this cell as not-pre-day-decidable.
    dep_avail_iso = pub_avail.isoformat()
    # The AIFS artifact-identity gate requires request.aifs_source_available_at to EXACTLY match
    # the scratch raw_forecast_artifacts.source_available_at row. The hydrated row carries the
    # late-capture timestamp; re-stamp it to the faithful publication time on the SCRATCH copy so
    # the identity check and the faithful decision model agree (scratch-only; never the live DB).
    conn.execute(
        "UPDATE raw_forecast_artifacts SET source_available_at=? WHERE artifact_id=?",
        (dep_avail_iso, aifs["artifact_id"]),
    )
    expected = expected_replacement_dependency_identity_by_role(metric)

    request = ReplacementForecastMaterializeRequest(
        city=city,
        city_id=str(baseline.get("city_id") or city),
        city_timezone=city_cfg.timezone,
        target_date=date.fromisoformat(target_date),
        temperature_metric=metric,
        baseline_source_run_id=str(baseline["source_run_id"]),
        baseline_data_version=expected["baseline_b0"].data_version,
        baseline_source_available_at=dep_avail_iso,
        aifs_extraction=aifs_local,
        aifs_source_run_id=str(aifs["source_run_id"] or f"aifs:{aifs['source_cycle_time']}"),
        aifs_source_available_at=dep_avail_iso,
        openmeteo_anchor=anchor,
        openmeteo_source_run_id=str(om9["source_run_id"] or f"om9:{om9['source_cycle_time']}"),
        openmeteo_source_available_at=dep_avail_iso,
        bins=bins,
        source_cycle_time=cycle,
        computed_at=computed_at,
        expires_at=computed_at + timedelta(hours=3),
        anchor_artifact_id=om9["artifact_id"],
        aifs_artifact_id=aifs["artifact_id"],
        openmeteo_precision_guard=precision_guard,
        anchor_weight=0.80,
        anchor_sigma_c=3.00,
        settlement_step_c=settlement_step_c,
    )
    result = materialize_replacement_forecast_live(conn, request)
    return {
        "status": result.status,
        "reason_codes": list(result.reason_codes),
        "posterior_id": result.posterior_id,
    }


def materialize(
    scratch_db: Path,
    *,
    targets: list[str],
    cities: list[str] | None,
    metrics: list[str],
    limit_per_cell: int | None,
) -> dict[str, object]:
    from src.config import cities_by_name  # noqa: PLC0415

    conn = sqlite3.connect(str(scratch_db))
    conn.row_factory = sqlite3.Row
    summary: dict[str, dict[str, int]] = {}
    reason_hist: dict[str, dict[str, int]] = {}
    try:
        for target_date in targets:
            for phase in _PHASES:
                cycle = datetime.fromisoformat(target_date).replace(tzinfo=UTC) - timedelta(days=1)
                cycle = cycle.replace(hour=phase, minute=0, second=0, microsecond=0)
                cycle_iso = cycle.isoformat()
                key = f"{target_date}|{phase:02d}Z"
                summary.setdefault(key, {"attempt": 0, "READY": 0, "BLOCKED": 0, "SKIP": 0, "ERROR": 0})
                reason_hist.setdefault(key, {})
                # settled cities for this target/metric
                for metric in metrics:
                    settled = [
                        r[0]
                        for r in conn.execute(
                            "SELECT city FROM settlement_outcomes WHERE authority='VERIFIED' AND target_date=? AND temperature_metric=?",
                            (target_date, metric),
                        ).fetchall()
                    ]
                    if cities:
                        settled = [c for c in settled if c in cities]
                    done = 0
                    for city in settled:
                        if city not in cities_by_name:
                            continue
                        if limit_per_cell and done >= limit_per_cell:
                            break
                        aifs = _find_aifs_artifact(conn, cycle_iso=cycle_iso, metric=metric, target_date=target_date)
                        if aifs is None or city not in (aifs.get("cities") or []):
                            summary[key]["SKIP"] += 1
                            reason_hist[key]["NO_AIFS_FOR_CITY"] = reason_hist[key].get("NO_AIFS_FOR_CITY", 0) + 1
                            continue
                        om9 = _find_om9_artifact(conn, cycle_iso=cycle_iso, metric=metric, target_date=target_date, city=city)
                        if om9 is None:
                            summary[key]["SKIP"] += 1
                            reason_hist[key]["NO_OM9_FOR_CITY"] = reason_hist[key].get("NO_OM9_FOR_CITY", 0) + 1
                            continue
                        summary[key]["attempt"] += 1
                        try:
                            conn.execute("BEGIN")
                            res = _materialize_one(
                                conn, city=city, target_date=target_date, metric=metric,
                                cycle=cycle, aifs=aifs, om9=om9,
                            )
                            if res["status"] == "READY":
                                conn.commit()
                                summary[key]["READY"] += 1
                                done += 1
                            else:
                                conn.rollback()
                                bucket = res["status"] if res["status"] in ("BLOCKED", "SKIP") else "SKIP"
                                summary[key][bucket] += 1
                                for rc in (res.get("reason_codes") or [res.get("reason", "UNKNOWN")]):
                                    reason_hist[key][rc] = reason_hist[key].get(rc, 0) + 1
                        except Exception as exc:  # noqa: BLE001
                            conn.rollback()
                            summary[key]["ERROR"] += 1
                            ename = f"ERR:{type(exc).__name__}:{str(exc)[:80]}"
                            reason_hist[key][ename] = reason_hist[key].get(ename, 0) + 1
                _vlog(f"  {key} {metrics}: {summary[key]}")
    finally:
        conn.close()
    return {"status": "MATERIALIZED", "summary": summary, "reason_hist": reason_hist}


# ---------------------------------------------------------------------------
# STAGE: backfill  (download missing AIFS GRIB + OM9 anchors from mirrors)
# ---------------------------------------------------------------------------
_SCRATCH_RAW_DIR = ROOT / "state" / "cycle_phase_study_raw"


def _safe(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def backfill(
    scratch_db: Path,
    *,
    target_date: str,
    phases: list[int],
    metrics: list[str],
    cities: list[str] | None,
) -> dict[str, object]:
    """Download missing AIFS GRIB + OM9 anchors for (D-1 cycle x phase) covering `target_date`.

    Writes artifacts under state/cycle_phase_study_raw/ and registers raw_forecast_artifacts
    rows in the SCRATCH DB. Reuses the live download building blocks (AIFS mirror failover +
    run-pinned OM9 single-runs) so provenance matches the live capture path. Every artifact row
    carries the faithful source_cycle_time + source_available_at (cycle + measured publication lag)
    and the same product_id / data_version identities the materializer expects.
    """
    from src.config import cities_by_name  # noqa: PLC0415
    from src.data.ecmwf_aifs_ens_request import build_aifs_ens_open_data_request, retrieve_aifs_ens_open_data_request  # noqa: PLC0415
    from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION as AIFS_HIGH, LOW_DATA_VERSION as AIFS_LOW  # noqa: PLC0415
    from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
        HIGH_DATA_VERSION as OM_HIGH,
        LOW_DATA_VERSION as OM_LOW,
        build_anchor_request,
        build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
        fetch_openmeteo_ecmwf_ifs9_anchor_payload,
    )
    from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest_to_db  # noqa: PLC0415

    # Reuse the live precision-metadata builder so the precision JSON is identical to production.
    sys.path.insert(0, str(ROOT / "scripts"))
    from download_replacement_forecast_current_targets import _precision_metadata  # noqa: PLC0415

    aifs_dv = {"high": AIFS_HIGH, "low": AIFS_LOW}
    om_dv = {"high": OM_HIGH, "low": OM_LOW}
    conn = sqlite3.connect(str(scratch_db))
    conn.row_factory = sqlite3.Row
    out: dict[str, object] = {"status": "BACKFILLED", "target_date": target_date, "downloads": []}
    try:
        for phase in phases:
            cycle = datetime.fromisoformat(target_date).replace(tzinfo=UTC) - timedelta(days=1)
            cycle = cycle.replace(hour=phase, minute=0, second=0, microsecond=0)
            pub_avail = (cycle + timedelta(hours=_PHASE_PUBLICATION_LAG_HOURS)).isoformat()
            raw_dir = _SCRATCH_RAW_DIR / cycle.strftime("%Y%m%dT%H%M%SZ")
            raw_dir.mkdir(parents=True, exist_ok=True)
            for metric in metrics:
                settled = [
                    r[0]
                    for r in conn.execute(
                        "SELECT city FROM settlement_outcomes WHERE authority='VERIFIED' AND target_date=? AND temperature_metric=?",
                        (target_date, metric),
                    ).fetchall()
                ]
                if cities:
                    settled = [c for c in settled if c in cities]
                settled = [c for c in settled if c in cities_by_name]
                if not settled:
                    continue
                # AIFS already on disk for (cycle, metric, target)?
                have_aifs = _find_aifs_artifact(conn, cycle_iso=cycle.isoformat(), metric=metric, target_date=target_date)
                if have_aifs is None:
                    # Union of 6-hour steps across the settled cities' local-day windows.
                    steps: set[int] = set()
                    for c in settled:
                        steps |= set(_aifs_steps_for_target(cycle, cities_by_name[c].timezone, target_date))
                    steps = tuple(sorted(steps))
                    if not steps:
                        continue
                    slug = f"{steps[0]}_{steps[-1]}_{len(steps)}steps"
                    aifs_path = raw_dir / f"aifs_ens_{cycle.strftime('%Y%m%d_%Hz')}_{metric}_2t_steps_{slug}.grib2"
                    if not aifs_path.exists() or aifs_path.stat().st_size < 50_000_000:
                        req = build_aifs_ens_open_data_request(
                            forecast_date=cycle.date(), cycle_hour=cycle.hour, target_path=aifs_path, steps=steps
                        )
                        _vlog(f"  downloading AIFS {cycle.isoformat()} {metric} steps={slug} ...")
                        retrieve_aifs_ens_open_data_request(req)
                    manifest = RawForecastArtifactManifest.from_file(
                        aifs_path,
                        source_id="ecmwf_aifs_ens",
                        product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
                        data_version=aifs_dv[metric],
                        source_cycle_time=cycle.isoformat(),
                        source_available_at=pub_avail,
                        captured_at=pub_avail,
                        request_url="ecmwf-opendata://aifs-ens/2t/cycle-phase-study",
                        request_params={"date": cycle.strftime("%Y%m%d"), "time": cycle.hour, "steps": list(steps)},
                        product_metadata={
                            "artifact_class": "aifs_sampled_2t_grib_cycle_phase_study",
                            "cities": settled,
                            "target_dates": [target_date],
                            "metric": metric,
                            "source_run_id": f"aifs-study-{metric}-{cycle.strftime('%Y%m%dT%H%M%SZ')}",
                        },
                    )
                    conn.execute("BEGIN")
                    aid = write_manifest_to_db(conn, manifest, verify_artifact=True)
                    conn.commit()
                    out["downloads"].append({"kind": "aifs", "cycle": cycle.isoformat(), "metric": metric, "artifact_id": aid, "steps": slug})

                # OM9 anchors per city (skip those already on disk).
                for city in settled:
                    have_om = _find_om9_artifact(conn, cycle_iso=cycle.isoformat(), metric=metric, target_date=target_date, city=city)
                    if have_om is not None:
                        continue
                    cfg = cities_by_name[city]
                    payload_path = raw_dir / f"openmeteo_{_safe(city)}_{target_date}_{metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
                    precision_path = raw_dir / f"openmeteo_precision_{_safe(city)}_{target_date}_{metric}.json"
                    req = build_anchor_request(
                        latitude=float(cfg.lat), longitude=float(cfg.lon), run=cycle, timezone_name=cfg.timezone, forecast_hours=120
                    )
                    if not payload_path.exists():
                        payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(req)
                        payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
                    if not precision_path.exists():
                        precision_path.write_text(
                            json.dumps(_precision_metadata(city, target_date, anchor_sigma_c=3.0), indent=2, sort_keys=True, default=str) + "\n"
                        )
                    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
                        payload_path,
                        request=req,
                        metric=metric,
                        source_available_at=pub_avail,
                        captured_at=pub_avail,
                        product_metadata={
                            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_cycle_phase_study",
                            "city": city,
                            "cities": [city],
                            "target_date": target_date,
                            "target_dates": [target_date],
                            "metric": metric,
                            "source_run_id": f"om9-study-{_safe(city)}-{metric}-{cycle.strftime('%Y%m%dT%H%M%SZ')}",
                            "openmeteo_payload_json": str(payload_path),
                            "precision_metadata_json": str(precision_path),
                        },
                    )
                    conn.execute("BEGIN")
                    aid = write_manifest_to_db(conn, manifest, verify_artifact=True)
                    conn.commit()
                    out["downloads"].append({"kind": "om9", "cycle": cycle.isoformat(), "metric": metric, "city": city, "artifact_id": aid})
            _vlog(f"  backfill phase {phase:02d}Z done ({sum(1 for d in out['downloads'] if d['cycle']==cycle.isoformat())} artifacts)")
    finally:
        conn.close()
    out["download_count"] = len(out["downloads"])
    return out


# ---------------------------------------------------------------------------
# STAGE: grade
# ---------------------------------------------------------------------------
import math  # noqa: E402

_TS = 0.03  # taker edge threshold (operator: buy_no clears at ts=0.03)


def _settled_bin_qkey(market_bins: list[dict], settlement_value: float) -> str | None:
    """Map a settled numeric value to the q-key (range_label) of the bin that contains it."""
    for b in market_bins:
        lo = b["range_low"]
        hi = b["range_high"]
        if lo is not None and settlement_value < float(lo):
            continue
        if hi is not None and settlement_value > float(hi):
            continue
        return str(b["range_label"])
    return None


def _phase_of(cyc_iso: str) -> int:
    return datetime.fromisoformat(cyc_iso).astimezone(UTC).hour


def _mad_sigma(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    med = sorted(values)[len(values) // 2]
    mad = sorted(abs(v - med) for v in values)[len(values) // 2]
    return 1.4826 * mad


def grade(scratch_db: Path, *, trades_db: Path) -> dict[str, object]:
    """Settlement-grade scratch posteriors by cycle phase on identical family-days.

    Metrics per phase: (a) certified-bounds coverage (settled bin's q in [q_lcb,q_ucb]),
    (b) LogLoss of q on settled bin, (c) modal-bin hit rate, (d) simulated after-cost buy_no
    win-rate where the certified edge cleared ts=0.03 (fee = 0.05*p*(1-p)*shares), (e) fused-center
    residual (settled - mu) mean + MAD-sigma. Paired: a (city,target,metric) cell contributes to a
    phase only if it materialized for that phase; the paired set per metric is the cells present in
    ALL graded phases (reported separately as the paired cohort).
    """
    conn = sqlite3.connect(str(scratch_db))
    conn.row_factory = sqlite3.Row
    trade_conn = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True)
    trade_conn.row_factory = sqlite3.Row
    per_phase: dict[int, dict] = {p: {"cells": []} for p in _PHASES}
    try:
        rows = conn.execute(
            """
            SELECT p.posterior_id, p.city, p.target_date, p.temperature_metric, p.source_cycle_time,
                   p.q_json, p.q_lcb_json, p.q_ucb_json, p.provenance_json
            FROM forecast_posteriors p
            WHERE p.source_id = ?
            ORDER BY p.city, p.target_date, p.temperature_metric, p.source_cycle_time
            """,
            (_SOURCE_ID,),
        ).fetchall()
        for r in rows:
            phase = _phase_of(str(r["source_cycle_time"]))
            if phase not in per_phase:
                continue
            settle = conn.execute(
                "SELECT settlement_value, winning_bin FROM settlement_outcomes WHERE city=? AND target_date=? AND temperature_metric=? AND authority='VERIFIED' LIMIT 1",
                (r["city"], r["target_date"], r["temperature_metric"]),
            ).fetchone()
            if settle is None or settle["settlement_value"] is None:
                continue
            settle_val = float(settle["settlement_value"])
            mkt = conn.execute(
                "SELECT range_label, range_low, range_high FROM market_events WHERE city=? AND target_date=? AND temperature_metric=? AND token_id IS NOT NULL ORDER BY COALESCE(range_low,-999),COALESCE(range_high,999)",
                (r["city"], r["target_date"], r["temperature_metric"]),
            ).fetchall()
            mkt_bins = [dict(m) for m in mkt]
            settled_qkey = _settled_bin_qkey(mkt_bins, settle_val)
            if settled_qkey is None:
                continue
            q = json.loads(r["q_json"] or "{}")
            if settled_qkey not in q:
                continue
            prov = json.loads(r["provenance_json"] or "{}")
            q_settled = float(q[settled_qkey])
            modal_key = max(q, key=lambda k: q[k]) if q else None
            # The fused center mu (bayes_precision_fusion.anchor_value_c) is in CELSIUS; settlement_value is in
            # the city's settlement unit (F for US cities). Convert settle_val to C so the residual
            # (settled - mu) is a true degC error, not a unit-mismatch artifact.
            from src.config import cities_by_name as _cbn  # noqa: PLC0415
            _su = str(getattr(_cbn.get(r["city"]), "settlement_unit", "C") or "C").upper()
            settle_val_c = (settle_val - 32.0) * 5.0 / 9.0 if _su == "F" else settle_val
            cell = {
                "city": r["city"],
                "target_date": r["target_date"],
                "metric": r["temperature_metric"],
                "settle_val": settle_val,
                "settle_val_c": settle_val_c,
                "settled_qkey": settled_qkey,
                "q_settled": q_settled,
                "modal_hit": int(modal_key == settled_qkey),
                "logloss": -math.log(max(1e-12, min(1.0, q_settled))),
                "q_mode": prov.get("replacement_q_mode"),
                "q_shape": prov.get("q_shape"),
                "mu": (prov.get("bayes_precision_fusion") or {}).get("anchor_value_c"),
            }
            # (a) bounds coverage. NOTE: the per-cell "settled bin's q within [lcb,ucb]" check is
            # VACUOUS-BY-CONSTRUCTION (the materializer clips q_lcb <= q_point <= q_ucb per bin),
            # so we also accumulate the MEANINGFUL aggregate bound-honesty rows: over all
            # (cell, bin) pairs, an honest certified band must straddle realized frequency:
            # mean(q_lcb) <= mean(y) <= mean(q_ucb) on any pre-registered subset.
            lcb = json.loads(r["q_lcb_json"] or "null")
            ucb = json.loads(r["q_ucb_json"] or "null")
            cell["bin_rows"] = []
            if isinstance(lcb, dict) and isinstance(ucb, dict) and settled_qkey in lcb and settled_qkey in ucb:
                cell["has_bounds"] = 1
                cell["covered"] = int(float(lcb[settled_qkey]) <= q_settled <= float(ucb[settled_qkey]))
                cell["q_lcb_settled"] = float(lcb[settled_qkey])
                for bk, qv in q.items():
                    if bk in lcb and bk in ucb:
                        cell["bin_rows"].append(
                            (1.0 if bk == settled_qkey else 0.0, float(qv), float(lcb[bk]), float(ucb[bk]))
                        )
            else:
                cell["has_bounds"] = 0
                cell["covered"] = None
                cell["q_lcb_settled"] = None
            # (d) buy_no win-rate: for each NON-settled bin, the certified no-edge = no_lcb - ask_no.
            # no_lcb = 1 - q_ucb(bin) (complement of the upper bound on YES). We need the executable
            # NO ask; approximate from YES top ask: ask_no = 1 - yes_bid ~ but we only have YES ask in
            # snapshots, so we use ask_no_proxy = 1 - (1 - yes_ask) = yes_ask is wrong. Instead grade
            # buy_no on the per-bin realized outcome: a buy_no on bin B wins iff settled != B. The edge
            # gate uses no_lcb vs the YES ask of bin B: buying NO(B) at price (1 - yes_ask_B) wins
            # (1/(1-yes_ask_B) - 1) per dollar if settled != B. We require certified no_lcb - (1-yes_ask) >= ts.
            cell["buy_no_trades"] = 0
            cell["buy_no_wins"] = 0
            cell["buy_no_pnl"] = 0.0
            if isinstance(ucb, dict):
                for b in mkt_bins:
                    bk = str(b["range_label"])
                    if bk == settled_qkey:
                        pass  # buying NO on the settled bin LOSES — still allowed if it cleared the gate
                    if bk not in ucb:
                        continue
                    q_ucb_b = float(ucb[bk])
                    no_lcb = 1.0 - q_ucb_b  # certified lower bound on P(not bin)
                    snap = _yes_ask_snapshot(trade_conn, conn, city=r["city"], target_date=r["target_date"], metric=r["temperature_metric"], range_label=bk)
                    if snap is None:
                        continue
                    yes_ask = snap
                    no_ask = 1.0 - yes_ask
                    if no_ask <= 0.0 or no_ask >= 1.0:
                        continue
                    edge = no_lcb - no_ask
                    if edge < _TS:
                        continue
                    # DIRECTION LAW: buy_no only when bin != forecast modal (the favorite-longshot harvest).
                    if bk == modal_key:
                        continue
                    cell["buy_no_trades"] += 1
                    shares = 1.0
                    fee = 0.05 * no_ask * (1.0 - no_ask) * shares
                    if settle_val_not_in_bin(b, settle_val):
                        cell["buy_no_wins"] += 1
                        cell["buy_no_pnl"] += shares * (1.0 / no_ask - 1.0) - fee
                    else:
                        cell["buy_no_pnl"] += -shares - fee
            per_phase[phase]["cells"].append(cell)
    finally:
        conn.close()
        trade_conn.close()
    return _summarize_phases(per_phase)


def settle_val_not_in_bin(bin_row: dict, settle_val: float) -> bool:
    lo = bin_row["range_low"]
    hi = bin_row["range_high"]
    inside = (lo is None or settle_val >= float(lo)) and (hi is None or settle_val <= float(hi))
    return not inside


def _yes_ask_snapshot(trade_conn, fcst_conn, *, city, target_date, metric, range_label) -> float | None:
    """Best executable YES ask for (city,target,metric,bin) at the pre-day decision time.

    Resolves the market_slug + condition_id from the forecast DB market_events, then reads the
    latest executable_market_snapshots YES ask before the target local day. Returns a float in
    (0,1) or None if no executable ask exists.
    """
    me = fcst_conn.execute(
        "SELECT market_slug, condition_id FROM market_events WHERE city=? AND target_date=? AND temperature_metric=? AND range_label=? AND condition_id IS NOT NULL LIMIT 1",
        (city, target_date, metric, range_label),
    ).fetchone()
    if me is None:
        return None
    # Use end-of-prior-day as decision cutoff (pre-day decision regime; coarse but consistent).
    cutoff = f"{target_date}T00:00:00+00:00"
    rows = trade_conn.execute(
        """
        SELECT orderbook_top_ask FROM executable_market_snapshots
        WHERE event_slug=? AND condition_id=? AND outcome_label='YES' AND captured_at <= ?
        ORDER BY captured_at DESC LIMIT 20
        """,
        (me["market_slug"], me["condition_id"], cutoff),
    ).fetchall()
    for row in rows:
        v = row["orderbook_top_ask"]
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 < f < 1.0:
            return f
    return None


def _summarize_phases(per_phase: dict[int, dict]) -> dict[str, object]:
    out: dict[str, object] = {"phases": {}}
    # paired family-day cells: keys present in every phase that has any cells.
    keysets = {}
    for p, d in per_phase.items():
        keysets[p] = {(c["city"], c["target_date"], c["metric"]) for c in d["cells"]}
    nonempty = [p for p in _PHASES if keysets[p]]
    paired = set.intersection(*[keysets[p] for p in nonempty]) if nonempty else set()
    out["paired_cell_keys_n"] = len(paired)
    out["paired_phases"] = nonempty
    for p in _PHASES:
        cells = per_phase[p]["cells"]
        paired_cells = [c for c in cells if (c["city"], c["target_date"], c["metric"]) in paired]
        fused_cells = [c for c in cells if c["has_bounds"]]
        # Aggregate bound-honesty over all (cell, bin) rows of fused cells: mean realized outcome
        # vs the mean certified band. An honest band straddles mean(y).
        bin_rows = [row for c in fused_cells for row in c.get("bin_rows", [])]
        if bin_rows:
            mean_y = sum(r[0] for r in bin_rows) / len(bin_rows)
            mean_q = sum(r[1] for r in bin_rows) / len(bin_rows)
            mean_lcb = sum(r[2] for r in bin_rows) / len(bin_rows)
            mean_ucb = sum(r[3] for r in bin_rows) / len(bin_rows)
            bound_honesty = {
                "n_bin_rows": len(bin_rows),
                "mean_y": mean_y,
                "mean_q": mean_q,
                "mean_lcb": mean_lcb,
                "mean_ucb": mean_ucb,
                "band_straddles_reality": bool(mean_lcb <= mean_y <= mean_ucb),
            }
        else:
            bound_honesty = {"n_bin_rows": 0}
        out["phases"][f"{p:02d}Z"] = {
            "n_all": len(cells),
            "n_paired": len(paired_cells),
            "n_fused": len(fused_cells),
            **_metrics_for(cells, "all"),
            **{f"paired_{k}": v for k, v in _metrics_for(paired_cells, "paired").items()},
            **{f"fused_{k}": v for k, v in _metrics_for(fused_cells, "fused").items()},
            "bound_honesty": bound_honesty,
        }
    # Pairwise FUSED-vs-FUSED comparison on common cells (the substrate-fair phase comparison:
    # the all-phase strict pairing compares single-anchor 00/06Z q against fused 12/18Z q, which
    # confounds cycle phase with capture substrate; fused-common pairs remove that confound).
    fused_keys = {
        p: {(c["city"], c["target_date"], c["metric"]): c for c in per_phase[p]["cells"] if c["has_bounds"]}
        for p in _PHASES
    }
    pairwise: dict[str, object] = {}
    for i, a in enumerate(_PHASES):
        for b in _PHASES[i + 1:]:
            common = sorted(set(fused_keys[a]) & set(fused_keys[b]))
            if not common:
                pairwise[f"{a:02d}Z_vs_{b:02d}Z"] = {"n": 0}
                continue
            ca = [fused_keys[a][k] for k in common]
            cb = [fused_keys[b][k] for k in common]
            pairwise[f"{a:02d}Z_vs_{b:02d}Z"] = {
                "n": len(common),
                f"logloss_{a:02d}Z": sum(c["logloss"] for c in ca) / len(ca),
                f"logloss_{b:02d}Z": sum(c["logloss"] for c in cb) / len(cb),
                "logloss_delta_b_minus_a": (sum(c["logloss"] for c in cb) - sum(c["logloss"] for c in ca)) / len(ca),
                f"modal_{a:02d}Z": sum(c["modal_hit"] for c in ca) / len(ca),
                f"modal_{b:02d}Z": sum(c["modal_hit"] for c in cb) / len(cb),
                "logloss_win_b": sum(1 for x, y in zip(ca, cb) if y["logloss"] < x["logloss"]),
                "logloss_win_a": sum(1 for x, y in zip(ca, cb) if x["logloss"] < y["logloss"]),
            }
    out["pairwise_fused"] = pairwise
    return out


def _metrics_for(cells: list[dict], _tag: str) -> dict:
    if not cells:
        return {"coverage": None, "logloss": None, "modal_hit_rate": None,
                "buy_no_trades": 0, "buy_no_winrate": None, "buy_no_pnl": 0.0,
                "resid_mean": None, "resid_mad_sigma": None, "n_bounds": 0}
    bounded = [c for c in cells if c["has_bounds"]]
    cov = [c["covered"] for c in bounded if c["covered"] is not None]
    resids = [c["settle_val_c"] - c["mu"] for c in cells if c.get("mu") is not None and c.get("settle_val_c") is not None]
    tno = sum(c["buy_no_trades"] for c in cells)
    wno = sum(c["buy_no_wins"] for c in cells)
    pnl = sum(c["buy_no_pnl"] for c in cells)
    return {
        "coverage": (sum(cov) / len(cov)) if cov else None,
        "n_bounds": len(bounded),
        "logloss": sum(c["logloss"] for c in cells) / len(cells),
        "modal_hit_rate": sum(c["modal_hit"] for c in cells) / len(cells),
        "buy_no_trades": tno,
        "buy_no_winrate": (wno / tno) if tno else None,
        "buy_no_pnl": pnl,
        "resid_mean": (sum(resids) / len(resids)) if resids else None,
        "resid_mad_sigma": _mad_sigma(resids) if len(resids) >= 2 else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_h = sub.add_parser("hydrate")
    p_h.add_argument("--force", action="store_true")
    p_h.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_b = sub.add_parser("backfill")
    p_b.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_b.add_argument("--target-date", required=True)
    p_b.add_argument("--phases", nargs="+", type=int, default=[0, 6, 12])
    p_b.add_argument("--metrics", nargs="+", default=["high", "low"])
    p_b.add_argument("--cities", nargs="+", default=None)
    p_m = sub.add_parser("materialize")
    p_m.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_m.add_argument("--targets", nargs="+", default=["2026-06-07", "2026-06-08", "2026-06-09"])
    p_m.add_argument("--cities", nargs="+", default=None)
    p_m.add_argument("--metrics", nargs="+", default=["high", "low"])
    p_m.add_argument("--limit-per-cell", type=int, default=None)
    p_g = sub.add_parser("grade")
    p_g.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_g.add_argument("--trades-db", type=Path, default=LIVE_TRADES_DB)
    p_g.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.cmd == "hydrate":
        out = hydrate(args.scratch_db, live_db=LIVE_FORECASTS_DB, force=args.force)
    elif args.cmd == "backfill":
        out = backfill(
            args.scratch_db,
            target_date=args.target_date,
            phases=args.phases,
            metrics=args.metrics,
            cities=args.cities,
        )
    elif args.cmd == "materialize":
        out = materialize(
            args.scratch_db,
            targets=args.targets,
            cities=args.cities,
            metrics=args.metrics,
            limit_per_cell=args.limit_per_cell,
        )
    elif args.cmd == "grade":
        out = grade(args.scratch_db, trades_db=args.trades_db)
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(out, sort_keys=True, indent=2, default=str) + "\n")
    else:
        parser.error("unknown command")
    print(json.dumps(out, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
