#!/usr/bin/env python3
"""Build a durable replacement forecast product-specific refit handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_refit_handoff import (  # noqa: E402
    build_replacement_forecast_refit_handoff,
    write_replacement_forecast_refit_handoff,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build replacement forecast refit handoff JSON")
    parser.add_argument("--fine-tune-artifact-json", type=Path, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--metric", choices=("high", "low"), required=True)
    parser.add_argument("--data-version")
    parser.add_argument("--generated-at")
    parser.add_argument("--live-promotion-requested", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    if args.output_json is None and not args.stdout:
        parser.error("provide --output-json or --stdout")
    try:
        fine_tune_artifact = json.loads(args.fine_tune_artifact_json.read_text(encoding="utf-8"))
        if not isinstance(fine_tune_artifact, dict):
            raise ValueError("fine-tune artifact JSON must decode to an object")
        handoff = build_replacement_forecast_refit_handoff(
            fine_tune_artifact=fine_tune_artifact,
            city=args.city,
            season=args.season,
            metric=args.metric,
            data_version=args.data_version,
            generated_at=args.generated_at,
            live_promotion_requested=bool(args.live_promotion_requested),
        )
        if args.output_json is not None:
            write_replacement_forecast_refit_handoff(handoff, args.output_json)
        if args.stdout:
            print(json.dumps(handoff.as_dict(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0 if handoff.ready_for_product_refit else 1


if __name__ == "__main__":
    raise SystemExit(main())
