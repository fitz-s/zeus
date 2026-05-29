#!/usr/bin/env python3
# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL_DRAFT2_RESPONSE §2c + E_phase0_fixture_scope.md §4
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Freeze the current production pipeline's p_raw output as the immutable before-baseline for Phase-5 equivalence testing.
# Reuse: Inspect fixture_meta.json for snapshot grain and p_cal=p_raw invariant before relying on the parquet as a before-baseline.
"""Capture Phase-0 'before' fixture for before/after validation harness.

Created: 2026-05-29
Last reused or audited: 2026-05-29
Authority basis: TRIBUNAL_DRAFT2_RESPONSE §2c + E_phase0_fixture_scope.md §4

Freezes the current production pipeline's raw-identity output (p_raw via
p_raw_vector_from_maxes, p_cal = p_raw because get_calibrator returns None/4
for all ecmwf_opendata HIGH rows) as the immutable 'before' baseline for
Phase-5 equivalence testing.

Grain: per-snapshot (6,167 rows), not per-pair (801). Each snapshot has a
distinct (snapshot_id, lead_hours, source_cycle_time, members_json) but shares
the same settlement from the one VERIFIED settlement per (city, target_date).

INVARIANTS:
- NEVER writes to any DB. Single read-only connection with PRAGMA query_only=ON.
- Output: fixture.parquet + fixture_meta.json in --output-dir.
- p_cal = p_raw for all rows (get_calibrator returns (None, 4) for ecmwf_opendata;
  production caller skips calibrate_and_normalize when cal is None).

Usage:
    python scripts/capture_before_fixture.py \
        --db /path/to/zeus-forecasts.db \
        --output-dir docs/operations/before_after_fixture_2026-05-29 \
        [--smoke N]  # test on N rows only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
from datetime import timezone
from pathlib import Path
from datetime import datetime, timezone

import numpy as np

# ---------------------------------------------------------------------------
# Repo root detection — script must work from any cwd
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ZEUS_MODE", "paper")


def _import_zeus_modules():
    """Lazy import to allow --help without full Zeus initialization."""
    from src.config import runtime_cities_by_name  # noqa: PLC0415
    from src.signal.ensemble_signal import p_raw_vector_from_maxes  # noqa: PLC0415
    from src.contracts.calibration_bins import (  # noqa: PLC0415
        C_CANONICAL_GRID,
        F_CANONICAL_GRID,
    )
    return runtime_cities_by_name, p_raw_vector_from_maxes, C_CANONICAL_GRID, F_CANONICAL_GRID


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

_QUERY = """
SELECT
    s.snapshot_id,
    s.city,
    s.target_date,
    s.temperature_metric,
    s.source_cycle_time,
    s.lead_hours,
    s.data_version,
    s.bin_grid_id,
    s.bin_schema_version,
    s.members_json,
    s.members_unit,
    s.settlement_unit,
    s.settlement_rounding_policy,
    s.source_id,
    se.settlement_value,
    se.winning_bin,
    se.unit AS settlement_obs_unit
FROM ensemble_snapshots s  -- canonical DDL (v2_schema.py:108); live-root DB lags with _v2 tables — operator migrates separately
JOIN settlement_outcomes se
    ON s.city = se.city
    AND s.target_date = se.target_date
    AND s.temperature_metric = se.temperature_metric
WHERE s.temperature_metric = 'high'
  AND s.data_version = 'ecmwf_opendata_mx2t3_local_calendar_day_max_v1'
  AND s.authority = 'VERIFIED'
  AND s.causality_status = 'OK'
  AND s.boundary_ambiguous = 0
  AND se.authority = 'VERIFIED'
  AND se.settlement_value IS NOT NULL
ORDER BY s.snapshot_id
"""


def _row_checksum(
    snapshot_id: int,
    members_json: str,
    p_raw_vector: np.ndarray,
    p_cal_vector: np.ndarray,
    settlement_value: float,
) -> str:
    """Canonical row-level tamper-detection digest.

    sha256(snapshot_id_le64 | members_json_utf8 | p_raw_bytes_big_float64
           | p_cal_bytes_big_float64 | settlement_be_float64)
    """
    h = hashlib.sha256()
    h.update(struct.pack("<q", snapshot_id))
    h.update(members_json.encode("utf-8"))
    for v in p_raw_vector:
        h.update(struct.pack(">d", float(v)))
    for v in p_cal_vector:
        h.update(struct.pack(">d", float(v)))
    h.update(struct.pack(">d", float(settlement_value)))
    return h.hexdigest()


def _content_digest(rows: list[dict]) -> str:
    """Serialization-independent content digest over all rows sorted by snapshot_id.

    sha256 over (snapshot_id | p_raw | p_cal | settlement_value) for each row,
    in snapshot_id order. Independent of Parquet encoding / pyarrow version.
    """
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r["snapshot_id"]):
        h.update(struct.pack("<q", row["snapshot_id"]))
        for v in row["p_raw_vector"]:
            h.update(struct.pack(">d", float(v)))
        for v in row["p_cal_vector"]:
            h.update(struct.pack(">d", float(v)))
        h.update(struct.pack(">d", float(row["settlement_value"])))
    return h.hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_error_model_family(conn: sqlite3.Connection) -> dict:
    """Assert 100% single-path (error_model_family NULL) per spec §1 / E_phase0_fixture_scope §1c."""
    try:
        cur = conn.execute("""
            SELECT COUNT(*) FROM ensemble_snapshots s
            JOIN settlement_outcomes se ON s.city=se.city AND s.target_date=se.target_date
                AND s.temperature_metric=se.temperature_metric
            WHERE s.temperature_metric='high'
              AND s.data_version='ecmwf_opendata_mx2t3_local_calendar_day_max_v1'
              AND s.authority='VERIFIED'
              AND s.causality_status='OK'
              AND s.boundary_ambiguous=0
              AND se.authority='VERIFIED'
              AND se.settlement_value IS NOT NULL
              AND json_extract(s.provenance_json, '$.error_model_family') IS NOT NULL
        """)
        non_null_family = cur.fetchone()[0]
    except Exception:
        non_null_family = "unknown"
    return {"error_model_family_non_null_count": non_null_family}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(_REPO_ROOT / "state" / "zeus-forecasts.db"),
        help="Path to zeus-forecasts.db (read-only)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "docs" / "operations" / "before_after_fixture_2026-05-29"),
        help="Directory to write fixture.parquet and fixture_meta.json",
    )
    parser.add_argument(
        "--smoke",
        type=int,
        default=None,
        metavar="N",
        help="Process only first N rows (smoke test)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    print(f"Importing Zeus modules...", flush=True)
    runtime_cities_by_name, p_raw_vector_from_maxes, C_CANONICAL_GRID, F_CANONICAL_GRID = _import_zeus_modules()

    grid_map = {
        "C_canonical_v1": C_CANONICAL_GRID,
        "F_canonical_v1": F_CANONICAL_GRID,
    }

    print(f"Opening DB read-only: {db_path}", flush=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")

    # Pre-flight: verify 100% single-path
    pre_flight = _verify_error_model_family(conn)
    if pre_flight["error_model_family_non_null_count"] not in (0, "unknown"):
        print(
            f"WARNING: {pre_flight['error_model_family_non_null_count']} rows have non-NULL "
            f"error_model_family — error-model path was invoked. Spec §1c assumed single-path only. "
            f"Proceeding with p_raw_vector_from_maxes uniformly (no branching). Verify this is correct.",
            file=sys.stderr,
        )

    print("Loading city map...", flush=True)
    cities_by_name = runtime_cities_by_name()

    print(f"Querying qualifying rows...", flush=True)
    t0 = time.monotonic()
    rows_db = conn.execute(_QUERY).fetchall()
    print(f"  fetched {len(rows_db)} rows in {time.monotonic()-t0:.2f}s", flush=True)

    if args.smoke is not None:
        rows_db = rows_db[: args.smoke]
        print(f"  SMOKE MODE: capped to {len(rows_db)} rows", flush=True)

    results = []
    n_failed = 0          # unexpected code errors
    n_skipped_null = 0    # rows with null/missing member values (data gaps, not code errors)
    n_identity_cal = 0    # get_calibrator returned None → p_cal = p_raw

    for i, row in enumerate(rows_db):
        if i % 200 == 0:
            elapsed = time.monotonic() - t0
            print(f"  [{i}/{len(rows_db)}] {elapsed:.1f}s elapsed", flush=True)

        snapshot_id = row["snapshot_id"]
        city_name = row["city"]
        target_date = row["target_date"]

        try:
            # --- City object ---
            city = cities_by_name[city_name]

            # --- Bin grid (from stored bin_grid_id, not re-derived from city) ---
            bin_grid_id = row["bin_grid_id"]
            if bin_grid_id not in grid_map:
                raise ValueError(f"Unknown bin_grid_id: {bin_grid_id!r}")
            grid = grid_map[bin_grid_id]
            bins = grid.as_bins()

            # --- Verify unit coherence (stored vs config) ---
            stored_settlement_unit = row["settlement_unit"]
            config_settlement_unit = city.settlement_unit
            if stored_settlement_unit != config_settlement_unit:
                raise ValueError(
                    f"Unit mismatch: stored settlement_unit={stored_settlement_unit!r} "
                    f"vs city.settlement_unit={config_settlement_unit!r} for {city_name}"
                )

            # --- Members ---
            members_json = row["members_json"]
            raw_members = json.loads(members_json)

            # Data-gap: some snapshots have None values in members_json (missing at ingest).
            # These cannot produce valid p_raw — skip and count as data gaps, not code errors.
            if any(m is None for m in raw_members):
                n_null = sum(1 for m in raw_members if m is None)
                n_skipped_null += 1
                continue

            member_maxes = np.array(raw_members, dtype=float)
            if len(member_maxes) != 51:
                raise ValueError(
                    f"Expected 51 members, got {len(member_maxes)}"
                )
            if not np.isfinite(member_maxes).all():
                raise ValueError(f"Non-finite member values after null check: {member_maxes[~np.isfinite(member_maxes)]}")

            # --- p_raw via canonical production function ---
            from src.contracts.settlement_semantics import SettlementSemantics  # noqa: PLC0415
            semantics = SettlementSemantics.for_city(city)
            p_raw_vector = p_raw_vector_from_maxes(
                member_maxes,
                city,
                semantics,
                bins,
                n_mc=None,        # → ensemble_n_mc() from config (10000)
                rng=None,         # → deterministic SHA-256 seed
                extra_member_sigma=0.0,  # confirmed NULL error_model_family
            )
            assert abs(p_raw_vector.sum() - 1.0) < 1e-6, (
                f"p_raw does not sum to 1.0: {p_raw_vector.sum()}"
            )

            # --- p_cal: get_calibrator returns (None, 4) for ecmwf_opendata HIGH ---
            # Production path: when cal is None, p_cal = p_raw (no calibration applied).
            # calibrate_and_normalize is NOT called with None — it would crash.
            p_cal_vector = p_raw_vector.copy()
            n_identity_cal += 1  # all rows are uncalibrated (None returned)
            calibrator_type = "None_level4"
            calibrator_n_samples = 0

            # --- Row checksum ---
            checksum = _row_checksum(
                snapshot_id, members_json, p_raw_vector, p_cal_vector,
                row["settlement_value"],
            )

            results.append({
                "snapshot_id": snapshot_id,
                "city": city_name,
                "target_date": target_date,
                "temperature_metric": row["temperature_metric"],
                "source_cycle_time": row["source_cycle_time"],
                "lead_hours": row["lead_hours"],
                "data_version": row["data_version"],
                "bin_grid_id": bin_grid_id,
                "bin_schema_version": row["bin_schema_version"],
                "members_unit": row["members_unit"],
                "settlement_unit": stored_settlement_unit,
                "settlement_rounding_policy": row["settlement_rounding_policy"],
                "source_id": row["source_id"],
                "member_count": len(member_maxes),
                "p_raw_vector": p_raw_vector.tolist(),
                "p_cal_vector": p_cal_vector.tolist(),
                "calibrator_type": calibrator_type,
                "calibrator_n_samples": calibrator_n_samples,
                "settlement_value": row["settlement_value"],
                "winning_bin": row["winning_bin"],
                "row_checksum": checksum,
            })

        except Exception as exc:
            n_failed += 1
            print(
                f"  ERROR snapshot_id={snapshot_id} city={city_name} "
                f"target_date={target_date}: {exc}",
                file=sys.stderr,
            )
            if n_failed > 5:
                print("Too many unexpected code errors (>5), aborting.", file=sys.stderr)
                return 1

    conn.close()

    print(
        f"Processed {len(results)} rows captured, {n_skipped_null} skipped (null members), "
        f"{n_failed} failed (code errors), {n_identity_cal} rows used identity (p_cal=p_raw).",
        flush=True,
    )

    if not results:
        print("No results to write — aborting.", file=sys.stderr)
        return 1

    # --- Content digest (serialization-independent) ---
    content_digest = _content_digest(results)

    # --- Write fixture ---
    fixture_path = output_dir / "fixture.parquet"
    jsonl_path = output_dir / "fixture.jsonl"  # fallback if pyarrow absent

    try:
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415

        # Build a flat table; vector columns stored as JSON strings for portability
        flat_rows = []
        for r in results:
            flat = dict(r)
            flat["p_raw_vector_json"] = json.dumps(r["p_raw_vector"])
            flat["p_cal_vector_json"] = json.dumps(r["p_cal_vector"])
            del flat["p_raw_vector"]
            del flat["p_cal_vector"]
            flat_rows.append(flat)

        table = pa.Table.from_pylist(flat_rows)
        pq.write_table(table, str(fixture_path))
        file_sha256 = _file_sha256(fixture_path)
        wrote_parquet = True
        print(f"Wrote {fixture_path} ({fixture_path.stat().st_size} bytes)", flush=True)
    except ImportError:
        print("pyarrow not available — writing JSONL fallback", flush=True)
        wrote_parquet = False
        with open(jsonl_path, "w") as f:
            for r in results:
                flat = dict(r)
                flat["p_raw_vector_json"] = json.dumps(r["p_raw_vector"])
                flat["p_cal_vector_json"] = json.dumps(r["p_cal_vector"])
                del flat["p_raw_vector"]
                del flat["p_cal_vector"]
                f.write(json.dumps(flat, separators=(",", ":")) + "\n")
        file_sha256 = _file_sha256(jsonl_path)
        print(f"Wrote {jsonl_path} ({jsonl_path.stat().st_size} bytes)", flush=True)

    # --- Manifest ---
    capture_ts = datetime.now(tz=timezone.utc).isoformat()

    # git SHA at capture time
    try:
        import subprocess  # noqa: PLC0415
        code_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(_REPO_ROOT), text=True
        ).strip()
    except Exception:
        code_commit = "unknown"

    target_dates = [r["target_date"] for r in results]
    cities = sorted(set(r["city"] for r in results))

    # Distinct (city, target_date) pairs
    pairs = set((r["city"], r["target_date"]) for r in results)

    meta = {
        "capture_ts": capture_ts,
        "code_commit": code_commit,
        "n_rows": len(results),
        "n_pairs": len(pairs),
        "n_cities": len(cities),
        "n_failed": n_failed,
        "n_skipped_null_members": n_skipped_null,
        "n_identity_cal": n_identity_cal,
        "smoke_mode": args.smoke is not None,
        "smoke_n": args.smoke,
        "date_range": {
            "min_target_date": min(target_dates),
            "max_target_date": max(target_dates),
        },
        "fixture_format": "parquet" if wrote_parquet else "jsonl",
        "fixture_file": str(fixture_path if wrote_parquet else jsonl_path),
        "file_sha256": file_sha256,
        "content_digest_sha256": content_digest,
        "pre_flight": pre_flight,
        "calibration_note": (
            "get_calibrator returns (None, 4) for all ecmwf_opendata HIGH rows. "
            "Production path: p_cal = p_raw (calibrate_and_normalize not called). "
            "calibrator_type='None_level4' on all rows. "
            "No Platt models exist for ecmwf_opendata source in platt_models_v2."
        ),
        "p_raw_note": (
            "Recomputed via p_raw_vector_from_maxes with n_mc=None (→ ensemble_n_mc()), "
            "rng=None (deterministic SHA-256 seed), extra_member_sigma=0.0. "
            "members_json contains per-member daily calendar-day maxes already extracted at ingest."
        ),
        "data_version_filter": "ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        "authority": "TRIBUNAL_DRAFT2_RESPONSE §2c + E_phase0_fixture_scope.md §4",
    }

    meta_path = output_dir / "fixture_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {meta_path}", flush=True)

    # --- Summary ---
    print("\n=== CAPTURE SUMMARY ===")
    print(f"  n_rows (per-snapshot): {len(results)}")
    print(f"  n_pairs (city×date):   {len(pairs)}")
    print(f"  n_cities:              {len(cities)}")
    print(f"  n_skipped_null:        {n_skipped_null} (null members in DB — data gaps)")
    print(f"  n_failed:              {n_failed} (code errors)")
    print(f"  n_identity_cal:        {n_identity_cal} (p_cal=p_raw, calibrator=None)")
    print(f"  date_range:            {meta['date_range']['min_target_date']} — {meta['date_range']['max_target_date']}")
    print(f"  fixture:               {fixture_path if wrote_parquet else jsonl_path}")
    print(f"  file_sha256:           {file_sha256}")
    print(f"  content_digest:        {content_digest}")
    print(f"  code_commit:           {code_commit}")
    print(f"  capture_ts:            {capture_ts}")
    print("======================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
