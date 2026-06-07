#!/usr/bin/env python3
"""Render replacement forecast go-live readiness artifacts from JSON input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_go_live_report import (  # noqa: E402
    build_replacement_forecast_go_live_readiness_from_payload,
    replacement_forecast_before_after_rows_from_csv,
    replacement_forecast_go_live_payload_template,
    replacement_forecast_go_live_report_to_jsonable,
    replacement_forecast_payload_with_current_live_switch_inventory,
    write_replacement_forecast_go_live_artifacts,
)


def _payload_declared_before_after_csv(payload: dict[str, object], input_path: Path) -> Path | None:
    capital_replay = payload.get("capital_replay")
    if not isinstance(capital_replay, dict):
        return None
    raw_path = capital_replay.get("before_after_rows_csv")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    candidate_bases = (input_path.parent, *input_path.parent.parents, Path.cwd(), ROOT)
    for base in candidate_bases:
        resolved = base / candidate
        if resolved.exists():
            return resolved
    return input_path.parent / candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render replacement forecast go-live readiness report")
    parser.add_argument("--input-json", type=Path, default=None, help="Explicit readiness payload JSON")
    parser.add_argument("--before-after-rows-csv", type=Path, default=None, help="Optional CSV rows overriding payload.before_after_rows")
    parser.add_argument("--live-state-root", type=Path, default=None, help="Optional repo root used to replace payload live_switch fields from real files and SQLite table inventory")
    parser.add_argument("--report-md", type=Path, default=None, help="Markdown artifact output path")
    parser.add_argument("--report-json", type=Path, default=None, help="JSON artifact output path")
    parser.add_argument("--stdout", action="store_true", help="Print JSON summary to stdout")
    parser.add_argument("--print-template", action="store_true", help="Print an explicit input JSON template")
    args = parser.parse_args(argv)
    if args.print_template:
        print(json.dumps(replacement_forecast_go_live_payload_template(), sort_keys=True, indent=2))
        return 0
    if args.input_json is None:
        parser.error("--input-json is required unless --print-template is set")
    if args.report_md is None and args.report_json is None and not args.stdout:
        parser.error("provide --report-md, --report-json, or --stdout")
    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        before_after_rows_csv = args.before_after_rows_csv
        if before_after_rows_csv is None:
            before_after_rows_csv = _payload_declared_before_after_csv(payload, args.input_json)
        if before_after_rows_csv is not None:
            rows = replacement_forecast_before_after_rows_from_csv(before_after_rows_csv)
            payload["before_after_rows"] = [
                {
                    "official_date": row.official_date,
                    "city": row.city,
                    "temperature_metric": row.temperature_metric,
                    "guardrail_bucket": row.guardrail_bucket,
                    "baseline_brier": row.baseline_brier,
                    "replacement_brier": row.replacement_brier,
                    "baseline_log_loss": row.baseline_log_loss,
                    "replacement_log_loss": row.replacement_log_loss,
                    "baseline_after_cost_pnl": row.baseline_after_cost_pnl,
                    "replacement_after_cost_pnl": row.replacement_after_cost_pnl,
                    "truth_authority": row.truth_authority,
                    "replay_status": row.replay_status,
                }
                for row in rows
            ]
        if args.live_state_root is not None:
            payload = replacement_forecast_payload_with_current_live_switch_inventory(
                payload,
                args.live_state_root,
            )
        report = build_replacement_forecast_go_live_readiness_from_payload(payload)
    except Exception as exc:
        error = {
            "status": "INVALID_PAYLOAD",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
        print(json.dumps(error, sort_keys=True), file=sys.stderr)
        return 2
    written = write_replacement_forecast_go_live_artifacts(
        report,
        markdown_path=args.report_md,
        json_path=args.report_json,
    ) if args.report_md is not None or args.report_json is not None else {}
    summary = replacement_forecast_go_live_report_to_jsonable(report)
    summary["written_artifacts"] = written
    if args.stdout:
        print(json.dumps(summary, sort_keys=True))
    report_ok_statuses = {"SIMPLE_SWITCH_READY", "FINE_TUNE_READY", "LIVE_PROMOTION_READY"}
    if report.status in report_ok_statuses or report.switch_decision_status == "LIVE_AUTHORITY":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
