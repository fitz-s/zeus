# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ("最正确的方法…不再遇到数据短缺") —
#   K4.0b(f) anchor transport ladder. Meta-stamped artifacts are admitted on the
#   provider's DECLARED run identity; this module is the belt-and-suspenders that
#   RE-VERIFIES that declaration against the run-pinned single-runs API once the same
#   run appears there (Fitz #4: data carries authority; declared authority gets audited).
"""Retroactive cross-check of meta-stamped anchor artifacts against single-runs.

For every cycle that produced ``run_authority=provider_meta_declared`` anchor artifacts,
once the single-runs API serves that run: fetch one representative city run-pinned and
compare its hourly series against the stored meta-stamped payload. Identical series ⇒
the meta declaration was truthful (receipt VERIFIED). Divergence beyond tolerance ⇒
ERROR + receipt MISMATCH — the lineage is flagged for operator review before any future
meta-stamped fetch is trusted for that regime.

Receipts: state/anchor_cross_check.json (append-mergeable map keyed by cycle). The raw
artifact manifests stay immutable; the receipt file + logs are the audit surface.
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
    """Compare hourly temperature_2m series on intersecting timestamps (pure logic)."""
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
    verdict = (
        "VERIFIED"
        if max_delta is not None and max_delta <= tolerance_c
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
