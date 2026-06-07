#!/usr/bin/env python3
"""Build a durable replacement soft-anchor fine-tune artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_finetune_artifact import (  # noqa: E402
    build_replacement_forecast_finetune_artifact,
    write_replacement_forecast_finetune_artifact,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build replacement fine-tune artifact from JSON rows")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--generated-at")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    if args.output_json is None and not args.stdout:
        parser.error("provide --output-json or --stdout")
    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input JSON must decode to an object")
        artifact = build_replacement_forecast_finetune_artifact(
            payload,
            generated_at=args.generated_at,
            source_path=args.input_json,
        )
        if args.output_json is not None:
            write_replacement_forecast_finetune_artifact(artifact, args.output_json)
        if args.stdout:
            print(json.dumps(artifact.as_dict(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0 if artifact.ready_for_refit else 1


if __name__ == "__main__":
    raise SystemExit(main())
