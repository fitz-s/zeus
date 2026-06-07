#!/usr/bin/env python3
"""Plan replacement forecast current-fact patches from explicit evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_current_fact_patch import (  # noqa: E402
    read_replacement_forecast_current_fact_patch_plan,
)


def _write_patch_files(plan) -> None:
    if not plan.ready or plan.source_patch is None or plan.data_patch is None:
        raise RuntimeError("current-fact patch is not ready")
    source_path = Path(plan.source_fact_path)
    data_path = Path(plan.data_fact_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(plan.source_patch, encoding="utf-8")
    data_path.write_text(plan.data_patch, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan replacement current-fact patches")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence-json", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="Write current source/data fact files; default is read-only")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = read_replacement_forecast_current_fact_patch_plan(
            args.root,
            evidence_json=args.evidence_json,
        )
        if args.write:
            _write_patch_files(plan)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    payload = plan.as_dict()
    payload["written"] = bool(args.write and plan.ready)
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{plan.status}: {','.join(plan.reason_codes)}")
    return 0 if plan.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
