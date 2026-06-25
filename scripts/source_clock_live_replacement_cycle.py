#!/usr/bin/env python3
"""Run the source-clock vNext live replacement chain once.

Pipeline:
  1. Probe Open-Meteo model update metadata.
  2. Download current Open-Meteo anchor inputs.
  3. Download source-clock/BPF extra model inputs.
  4. Enqueue and drain replacement materialization seeds.
  5. Optionally hand off to CycleRunner for the live decision cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_live_materialization_queue import (  # noqa: E402
    process_replacement_forecast_live_materialization_queue,
)
from src.data.replacement_forecast_production import (  # noqa: E402
    _download_bayes_precision_fusion_extra_raw_inputs_if_needed,
    _download_replacement_forecast_current_targets_if_needed,
    _enqueue_cycle_advance_reseeds_if_needed,
    _enqueue_fusion_upgrade_reseeds_if_needed,
    _replacement_forecast_live_materialization_queue_config,
)
from src.data.source_clock_update_probe import probe_openmeteo_source_clock_updates  # noqa: E402
from src.engine.discovery_mode import DiscoveryMode  # noqa: E402


def _jsonable(value):
    if value is None:
        return None
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, dict):
        return value
    return value


def run(args: argparse.Namespace) -> dict[str, object]:
    cfg = _replacement_forecast_live_materialization_queue_config()
    report: dict[str, object] = {
        "source_clock_live_replacement": "vnext_20260625",
    }
    source_clock_report = probe_openmeteo_source_clock_updates(
        endpoint_url=args.model_updates_url,
        use_network=not args.no_network,
    )
    report["source_clock_update_probe"] = source_clock_report.as_dict()

    current_download = _download_replacement_forecast_current_targets_if_needed(cfg)
    report["current_target_download"] = _jsonable(current_download)

    extras_download = _download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg)
    report["extra_model_download"] = _jsonable(extras_download)

    fusion_upgrade = _enqueue_fusion_upgrade_reseeds_if_needed(cfg)
    report["fusion_upgrade_enqueue"] = _jsonable(fusion_upgrade)

    cycle_advance = _enqueue_cycle_advance_reseeds_if_needed(cfg)
    report["cycle_advance_enqueue"] = _jsonable(cycle_advance)

    materialize = process_replacement_forecast_live_materialization_queue(
        request_dir=cfg["request_dir"],
        processed_dir=cfg["processed_dir"],
        failed_dir=cfg["failed_dir"],
        seed_dir=cfg["seed_dir"],
        seed_processed_dir=cfg["seed_processed_dir"],
        seed_failed_dir=cfg["seed_failed_dir"],
        forecast_db=cfg["forecast_db"],
        limit=int(cfg["limit"]),
        seed_limit=int(cfg["seed_limit"]),
    )
    report["materialization_queue"] = materialize.as_dict()

    if args.run_decision:
        from src.engine.cycle_runner import run_cycle  # noqa: PLC0415

        report["decision_cycle"] = run_cycle(DiscoveryMode(args.decision_mode))
    else:
        report["decision_cycle"] = {
            "status": "SKIPPED",
            "reason": "pass --run-decision to hand off to live CycleRunner",
            "decision_mode": args.decision_mode,
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-updates-url", default=None)
    parser.add_argument("--no-network", action="store_true", help="Read cached model updates JSONL instead of fetching.")
    parser.add_argument("--run-decision", action="store_true", help="Run the live CycleRunner decision cycle after materialization.")
    parser.add_argument(
        "--decision-mode",
        default=DiscoveryMode.UPDATE_REACTION.value,
        choices=[mode.value for mode in DiscoveryMode],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "state" / "source_updates" / "source_clock_live_replacement_cycle_report.json",
    )
    args = parser.parse_args()
    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
