#!/usr/bin/env python3
"""Audit replacement forecast raw artifact materialization readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_materialization_preflight import (  # noqa: E402
    build_replacement_forecast_materialization_preflight,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replacement forecast materialization preflight")
    parser.add_argument("--forecast-db", type=Path, required=True)
    parser.add_argument("--raw-manifest-dir", type=Path, required=True)
    parser.add_argument("--scratch-seed-dir", type=Path, required=True)
    parser.add_argument("--computed-at", default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--receipt-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_replacement_forecast_materialization_preflight(
            forecast_db=args.forecast_db,
            raw_manifest_dir=args.raw_manifest_dir,
            scratch_seed_dir=args.scratch_seed_dir,
            computed_at=args.computed_at,
            limit=args.limit,
        )
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    payload = report.as_dict()
    if args.receipt_json is not None:
        args.receipt_json.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{report.status}: {','.join(report.reason_codes)}")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
