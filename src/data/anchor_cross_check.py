# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ("最正确的方法…不再遇到数据短缺") —
#   K4.0b(f) anchor transport ladder. Meta-stamped artifacts are admitted on the
#   provider's DECLARED run identity; this module is the belt-and-suspenders that
#   RE-VERIFIES that declaration against the run-pinned single-runs API once the same
#   run appears there (Fitz #4: data carries authority; declared authority gets audited).
#   2026-06-11 (rung-3 bucket transport): same antibody extended to
#   ``run_authority=bucket_partial_run_unverified`` artifacts — once single-runs serves
#   the run, the stored bucket-read hourly series is compared against the run-pinned API
#   series for the same city; a VERIFIED receipt is the precondition for trusting the
#   bucket transport for that regime (raw grid reads may differ from API point requests
#   that apply elevation/lapse-rate downscaling — the cross-check MEASURES it).
"""Retroactive cross-check of declared-authority anchor artifacts against single-runs.

Two transport regimes are audited here, both keyed by cycle:

  * ``provider_meta_declared`` (rung 2, meta-stamped standard API): once single-runs
    serves the declared run, fetch one representative city run-pinned and compare its
    hourly series against the stored meta-stamped payload. Identical ⇒ meta declaration
    truthful (VERIFIED); divergence ⇒ ERROR + MISMATCH receipt.
  * ``bucket_partial_run_unverified`` (rung 3, S3 data_spatial partial-run read): once
    single-runs serves the run, compare the stored bucket-read series against the
    run-pinned API series for the same city. VERIFIED ⇒ the bucket transport's grid
    read matched the API for that city; MISMATCH (>tolerance) ⇒ likely an elevation /
    lapse-rate downscaling delta that must be whitelisted or corrected before the
    bucket transport is trusted there.

Both regimes reuse the same ``compare_hourly_series`` comparator. Receipts:
state/anchor_cross_check.json (append-mergeable map keyed by cycle, with a per-regime
sub-key). The raw artifact manifests stay immutable; the receipt file + logs are the
audit surface.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("zeus.anchor_cross_check")

UTC = timezone.utc
RECEIPT_PATH = Path("state/anchor_cross_check.json")
TEMP_TOLERANCE_C = 0.05
# Bucket transport tolerance = one API quantum (0.1C). The single-runs API serves 0.1C-
# rounded temps; the bucket carries 0.01C. A clean city's raw delta is exactly 0.05C (the
# half-quantum API-rounding gap); real coastal/terrain downscaling bias is ≥0.25C (measured
# 2026-06-11 — docs/evidence/anchor_channels/2026-06-11_bucket_vs_api_grid_validation.md).
# 0.1C cleanly separates the two with a wide margin.
BUCKET_VS_API_TOLERANCE_C = 0.1


def _load_receipts(path: Path = RECEIPT_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_receipts(receipts: Mapping[str, Any], path: Path = RECEIPT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(receipts), indent=1, sort_keys=True))


def compare_hourly_series(
    stored_payload: Mapping[str, Any],
    pinned_payload: Mapping[str, Any],
    *,
    tolerance_c: float = TEMP_TOLERANCE_C,
) -> dict[str, Any]:
    """Compare hourly temperature_2m series on intersecting timestamps (pure logic).

    The raw max-abs delta is reported. The caller chooses ``tolerance_c``: the meta-stamped
    path (both series from the same API at 0.1C) uses the strict 0.05C default; the bucket
    path passes ``BUCKET_VS_API_TOLERANCE_C`` (0.1C = one API quantum) because the API serves
    0.1C-rounded temps while the bucket carries 0.01C — a bucket value of 10.95 vs an API
    value of 10.9 (or 11.0) is the API's rounding, NOT a disagreement, and lands at exactly
    0.05C. Real coastal/terrain downscaling bias is ≥0.25C (measured 2026-06-11), so a 0.1C
    tolerance cleanly separates API-rounding noise from genuine bias (Fitz #4)."""
    stored = stored_payload.get("hourly") or {}
    pinned = pinned_payload.get("hourly") or {}
    stored_map = dict(zip(stored.get("time") or (), stored.get("temperature_2m") or ()))
    pinned_map = dict(zip(pinned.get("time") or (), pinned.get("temperature_2m") or ()))
    common = sorted(set(stored_map) & set(pinned_map))
    if not common:
        return {"verdict": "NO_OVERLAP", "compared": 0, "max_abs_delta_c": None}
    deltas = [
        abs(float(stored_map[t]) - float(pinned_map[t]))
        for t in common
        if stored_map[t] is not None and pinned_map[t] is not None
    ]
    max_delta = max(deltas) if deltas else None
    # tolerance comparison with a tiny epsilon so an exact-boundary delta (e.g. 0.05000001
    # from float repr of a true 0.05C API-rounding gap) is admitted, not spuriously rejected.
    verdict = (
        "VERIFIED"
        if max_delta is not None and max_delta <= tolerance_c + 1e-6
        else "MISMATCH"
    )
    return {"verdict": verdict, "compared": len(deltas), "max_abs_delta_c": max_delta}


def run_anchor_cross_check_cycle(forecast_db: Path) -> dict[str, Any]:
    """One pass: verify every pending meta-stamped cycle whose run single-runs now serves.

    Bounded: one single-runs fetch per pending cycle per pass. Fail-soft per cycle."""
    import sqlite3

    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        build_anchor_request,
        fetch_openmeteo_ecmwf_ifs9_anchor_payload,
    )
    from src.state.db import _connect

    receipts = _load_receipts()
    report: dict[str, Any] = {"checked": [], "pending": [], "errors": []}
    try:
        conn = _connect(Path(forecast_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_cycle_time, request_params_json, artifact_metadata_json
            FROM raw_forecast_artifacts
            WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
              AND artifact_metadata_json LIKE '%provider_meta_declared%'
            ORDER BY source_cycle_time DESC
            """
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"journal read: {exc}")
        return report
    by_cycle: dict[str, sqlite3.Row] = {}
    for row in rows:
        by_cycle.setdefault(str(row["source_cycle_time"]), row)
    for cycle_iso, row in by_cycle.items():
        if receipts.get(cycle_iso, {}).get("verdict") == "VERIFIED":
            continue
        try:
            meta = json.loads(row["artifact_metadata_json"] or "{}")
            payload_path = Path(str(meta.get("openmeteo_payload_json") or ""))
            if not payload_path.exists():
                report["errors"].append(f"{cycle_iso}: stored payload missing")
                continue
            stored_payload = json.loads(payload_path.read_text())
            params = json.loads(row["request_params_json"] or "{}")
            cycle = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
            request = build_anchor_request(
                latitude=float(params["latitude"]),
                longitude=float(params["longitude"]),
                run=cycle,
                timezone_name=str(params.get("timezone") or "UTC"),
                forecast_hours=int(params.get("forecast_hours") or 120),
            )
            try:
                pinned_payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request)
            except Exception:
                # single-runs does not serve this run yet — stay pending.
                report["pending"].append(cycle_iso)
                continue
            result = compare_hourly_series(stored_payload, pinned_payload)
            result["checked_at"] = datetime.now(UTC).isoformat()
            receipts[cycle_iso] = result
            report["checked"].append({"cycle": cycle_iso, **result})
            if result["verdict"] == "MISMATCH":
                logger.error(
                    "ANCHOR META-STAMP MISMATCH cycle=%s max_abs_delta_c=%s — "
                    "meta-declared run did not match the run-pinned series; lineage "
                    "requires operator review (receipts: %s)",
                    cycle_iso,
                    result["max_abs_delta_c"],
                    RECEIPT_PATH,
                )
        except Exception as exc:  # noqa: BLE001 — per-cycle fail-soft
            report["errors"].append(f"{cycle_iso}: {type(exc).__name__}: {str(exc)[:140]}")
    _write_receipts(receipts)
    return report


def run_bucket_anchor_cross_check_cycle(forecast_db: Path) -> dict[str, Any]:
    """One pass: verify every pending bucket-transport cycle whose run single-runs now serves.

    For each ``run_authority=bucket_partial_run_unverified`` cycle, once the run-pinned
    single-runs API serves that run, compare the stored bucket-read hourly series against
    the API series for the SAME city. VERIFIED ⇒ the bucket grid read matched the API
    point request (no material elevation-downscaling delta for that city); MISMATCH ⇒
    ERROR + receipt (the city must be whitelisted / corrected before the bucket transport
    is trusted there).

    Bounded: one single-runs fetch per pending cycle per pass. Fail-soft per cycle. The
    receipt is stored under a ``bucket`` sub-key so it never collides with the meta-stamped
    receipt for the same cycle."""
    import sqlite3

    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        build_anchor_request,
        fetch_openmeteo_ecmwf_ifs9_anchor_payload,
    )
    from src.state.db import _connect

    receipts = _load_receipts()
    report: dict[str, Any] = {"checked": [], "pending": [], "errors": []}
    try:
        conn = _connect(Path(forecast_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_cycle_time, request_params_json, artifact_metadata_json
            FROM raw_forecast_artifacts
            WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
              AND artifact_metadata_json LIKE '%bucket_partial_run_unverified%'
            ORDER BY source_cycle_time DESC
            """
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"journal read: {exc}")
        return report
    # Key by (cycle, city): one cycle has many cities with DIFFERENT verdicts (coastal/
    # terrain cities mismatch while inland cities verify), so a single per-cycle receipt
    # cannot represent per-city whitelisting. The whitelist resolver reads city from each
    # ``<cycle>::bucket::<city>`` receipt.
    by_cycle_city: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        try:
            _meta = json.loads(row["artifact_metadata_json"] or "{}")
        except Exception:  # noqa: BLE001
            _meta = {}
        city = str(_meta.get("city") or "")
        by_cycle_city.setdefault((str(row["source_cycle_time"]), city), row)
    for (cycle_iso, city), row in by_cycle_city.items():
        receipt_key = f"{cycle_iso}::bucket::{city}" if city else f"{cycle_iso}::bucket"
        if receipts.get(receipt_key, {}).get("verdict") == "VERIFIED":
            continue
        try:
            meta = json.loads(row["artifact_metadata_json"] or "{}")
            payload_path = Path(str(meta.get("openmeteo_payload_json") or ""))
            if not payload_path.exists():
                report["errors"].append(f"{cycle_iso}: stored bucket payload missing")
                continue
            stored_payload = json.loads(payload_path.read_text())
            params = json.loads(row["request_params_json"] or "{}")
            cycle = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
            request = build_anchor_request(
                latitude=float(params["latitude"]),
                longitude=float(params["longitude"]),
                run=cycle,
                timezone_name=str(params.get("timezone") or "UTC"),
                forecast_hours=int(params.get("forecast_hours") or 120),
            )
            try:
                pinned_payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request)
            except Exception:
                report["pending"].append(cycle_iso)
                continue
            # BUCKET_VS_API_TOLERANCE_C (0.1C = one API quantum): the API serves 0.1C-rounded
            # temps while the bucket carries 0.01C — a clean city's delta is exactly 0.05C
            # (API rounding), real bias is ≥0.25C, so 0.1C separates them cleanly (Fitz #4).
            result = compare_hourly_series(
                stored_payload, pinned_payload, tolerance_c=BUCKET_VS_API_TOLERANCE_C
            )
            result["checked_at"] = datetime.now(UTC).isoformat()
            result["transport"] = "bucket_partial_run"
            result["city"] = meta.get("city")
            receipts[receipt_key] = result
            report["checked"].append({"cycle": cycle_iso, **result})
            if result["verdict"] == "MISMATCH":
                logger.error(
                    "ANCHOR BUCKET-TRANSPORT MISMATCH cycle=%s city=%s max_abs_delta_c=%s — "
                    "bucket grid read diverged from the run-pinned API series beyond %.3fC "
                    "(likely elevation/lapse-rate downscaling); city must be whitelisted or "
                    "corrected before the bucket transport is trusted (receipts: %s)",
                    cycle_iso,
                    meta.get("city"),
                    result["max_abs_delta_c"],
                    TEMP_TOLERANCE_C,
                    RECEIPT_PATH,
                )
        except Exception as exc:  # noqa: BLE001 — per-cycle fail-soft
            report["errors"].append(f"{cycle_iso}: {type(exc).__name__}: {str(exc)[:140]}")
    _write_receipts(receipts)
    return report
