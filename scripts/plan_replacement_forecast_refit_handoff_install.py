#!/usr/bin/env python3
"""Plan or install a ready replacement forecast refit handoff artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_refit_handoff_install import (  # noqa: E402
    plan_replacement_forecast_refit_handoff_install,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replacement refit handoff install planner")
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--refit-handoff-json", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = plan_replacement_forecast_refit_handoff_install(
            live_root=args.live_root,
            refit_handoff_json=args.refit_handoff_json,
            write=bool(args.write),
        )
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    payload = plan.as_dict()
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{plan.status}: {','.join(plan.reason_codes)}")
    return 0 if plan.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
