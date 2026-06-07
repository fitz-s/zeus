#!/usr/bin/env python3
"""Audit replacement forecast runtime wiring without mutating live state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_runtime_wiring_audit import (  # noqa: E402
    build_replacement_forecast_runtime_wiring_audit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replacement forecast runtime wiring audit")
    parser.add_argument("--live-root", type=Path, default=ROOT)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--apply-receipt-json", type=Path, default=None)
    parser.add_argument("--assume-shadow-veto", action="store_true")
    parser.add_argument("--assume-current-facts", action="store_true")
    parser.add_argument("--assume-shadow-schema", action="store_true")
    parser.add_argument("--assume-refit-handoff", action="store_true")
    parser.add_argument("--optional-dependency", action="append", default=["requests"])
    parser.add_argument("--receipt-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    assume_flags = (
        args.assume_shadow_veto,
        args.assume_current_facts,
        args.assume_shadow_schema,
        args.assume_refit_handoff,
    )
    if os.environ.get("ZEUS_REPLACEMENT_FORECAST_PRODUCTION_RELEASE") == "1" and any(assume_flags):
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error_type": "ProductionAssumptionForbidden",
                    "error": "replacement forecast production release forbids --assume-* flags",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        report = build_replacement_forecast_runtime_wiring_audit(
            live_root=args.live_root,
            repo_root=args.repo_root,
            apply_receipt_json=args.apply_receipt_json,
            assume_shadow_veto=bool(args.assume_shadow_veto),
            assume_current_facts=bool(args.assume_current_facts),
            assume_shadow_schema=bool(args.assume_shadow_schema),
            assume_refit_handoff=bool(args.assume_refit_handoff),
            optional_dependencies=tuple(args.optional_dependency or ()),
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
