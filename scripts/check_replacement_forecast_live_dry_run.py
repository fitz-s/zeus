#!/usr/bin/env python3
"""Check replacement forecast simple-switch readiness against current files/DBs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_live_dry_run import (  # noqa: E402
    ReplacementForecastLiveDryRunInput,
    build_replacement_forecast_live_dry_run_report,
)
from src.data.replacement_forecast_runtime_policy import (  # noqa: E402
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    TRADE_AUTHORITY_FLAG,
)


def _load_root_feature_flags(root: Path) -> Mapping[str, Any]:
    settings_path = root / "config" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{settings_path} must decode to a JSON object")
    flags = payload.get("feature_flags")
    if not isinstance(flags, Mapping):
        raise ValueError(f"{settings_path} must contain feature_flags object")
    return flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replacement forecast live dry-run gate")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stdout", action="store_true", help="Print JSON report")
    parser.add_argument("--assume-live-authority-flags", action="store_true", help="Preview live-authority feature flags without editing config")
    parser.add_argument("--assume-current-facts", action="store_true", help="Preview current source/data fact status without editing docs")
    parser.add_argument("--assume-shadow-schema", action="store_true", help="Preview replacement shadow schema after targeted initializer")
    parser.add_argument("--assume-refit-handoff", action="store_true", help="Preview a ready refit handoff file without editing live root")
    args = parser.parse_args(argv)
    try:
        flags = dict(_load_root_feature_flags(args.root))
        if args.assume_live_authority_flags:
            flags.update(
                {
                    TRADE_AUTHORITY_FLAG: True,
                    KELLY_INCREASE_FLAG: True,
                    DIRECTION_FLIP_FLAG: True,
                }
            )
        report = build_replacement_forecast_live_dry_run_report(
            ReplacementForecastLiveDryRunInput(
                root=args.root,
                runtime_flags=flags,
                source_fact_status_override="CURRENT_FOR_LIVE" if args.assume_current_facts else None,
                data_fact_status_override="CURRENT_FOR_LIVE" if args.assume_current_facts else None,
                assume_replacement_shadow_schema_initialized=bool(args.assume_shadow_schema),
                assume_refit_handoff_available=bool(args.assume_refit_handoff),
            )
        )
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(report.as_dict(), sort_keys=True))
    else:
        print(f"{report.status}: {','.join(report.reason_codes)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
