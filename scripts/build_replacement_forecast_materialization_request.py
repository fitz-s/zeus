#!/usr/bin/env python3
"""Build a validated replacement forecast materialization request JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_materialization_request_builder import (  # noqa: E402
    build_replacement_forecast_materialization_request,
)


def _load_json(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--input-json must decode to an object")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build replacement forecast materialization request JSON")
    parser.add_argument("--input-json", type=Path, required=True, help="Seed JSON with raw artifact paths, bins, and source-run identity")
    parser.add_argument("--output-json", type=Path, help="Where to write the materialization request JSON")
    parser.add_argument("--queue-dir", type=Path, help="Write request into this queue directory using the seed filename")
    parser.add_argument("--stdout", action="store_true", help="Print the builder report")
    args = parser.parse_args(argv)
    if args.output_json is not None and args.queue_dir is not None:
        parser.error("--output-json and --queue-dir are mutually exclusive")
    try:
        seed = _load_json(args.input_json)
        result = build_replacement_forecast_materialization_request(seed, base_dir=args.input_json.parent)
        if result.ok and result.request is not None:
            if args.queue_dir is not None:
                _write_json(args.queue_dir / args.input_json.name, dict(result.request))
            elif args.output_json is not None:
                _write_json(args.output_json, dict(result.request))
        if args.stdout or (args.output_json is None and args.queue_dir is None):
            print(json.dumps(result.as_dict(), sort_keys=True))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
