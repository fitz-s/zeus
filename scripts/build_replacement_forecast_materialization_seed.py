#!/usr/bin/env python3
"""Build replacement forecast materialization seed from market/source context."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: E402
    build_replacement_forecast_materialization_seed,
    latest_baseline_coverage_for_replacement_seed,
    load_manifest_with_path,
    market_bins_for_replacement_seed,
    write_seed,
)
from src.state.db import _connect  # noqa: E402


UTC = timezone.utc


def _dt(value: str | None, *, field_name: str) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build replacement materialization seed JSON from DB context")
    parser.add_argument("--forecast-db", type=Path, required=True, help="Forecast DB containing market_events/source_run_coverage")
    parser.add_argument("--city", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--temperature-metric", choices=("high", "low"), required=True)
    parser.add_argument("--aifs-manifest-json", type=Path, required=True)
    parser.add_argument("--openmeteo-manifest-json", type=Path, required=True)
    parser.add_argument("--aifs-samples-json", type=Path, required=True)
    parser.add_argument("--openmeteo-payload-json", type=Path, required=True)
    parser.add_argument("--precision-metadata-json", type=Path, required=True)
    parser.add_argument("--computed-at", help="Decision/queue time; default now UTC")
    parser.add_argument("--expires-at")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        conn = _connect(args.forecast_db, write_class="live")
        conn.row_factory = sqlite3.Row
        try:
            coverage = latest_baseline_coverage_for_replacement_seed(
                conn,
                city=args.city,
                target_date=args.target_date,
                temperature_metric=args.temperature_metric,
            )
            bins = market_bins_for_replacement_seed(
                conn,
                city=args.city,
                target_date=args.target_date,
                temperature_metric=args.temperature_metric,
            )
        finally:
            conn.close()
        if coverage is None:
            result = {"status": "BLOCKED", "reason_codes": ["BASELINE_COVERAGE_NOT_FOUND"], "seed": {}}
        elif not bins:
            result = {"status": "BLOCKED", "reason_codes": ["MARKET_BIN_FAMILY_NOT_FOUND"], "seed": {}}
        else:
            seed_result = build_replacement_forecast_materialization_seed(
                city=args.city,
                target_date=args.target_date,
                temperature_metric=args.temperature_metric,
                market_bins=bins,
                baseline_coverage=coverage,
                aifs_manifest=load_manifest_with_path(args.aifs_manifest_json),
                openmeteo_manifest=load_manifest_with_path(args.openmeteo_manifest_json),
                aifs_samples_json=args.aifs_samples_json,
                openmeteo_payload_json=args.openmeteo_payload_json,
                precision_metadata_json=args.precision_metadata_json,
                computed_at=_dt(args.computed_at, field_name="computed_at"),
                expires_at=None if args.expires_at is None else _dt(args.expires_at, field_name="expires_at"),
                base_dir=(args.output_json.parent if args.output_json is not None else Path.cwd()),
            )
            result = seed_result.as_dict()
            if seed_result.ok and args.output_json is not None and seed_result.seed is not None:
                write_seed(args.output_json, seed_result.seed)
        if args.stdout or args.output_json is None:
            print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
