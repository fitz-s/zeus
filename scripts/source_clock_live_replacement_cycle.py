#!/usr/bin/env python3
"""Run the source-clock vNext live replacement chain once.

Pipeline:
  1. Probe Open-Meteo model update metadata.
  2. Download current Open-Meteo anchor inputs.
  3. Download source-clock/BPF extra model inputs.
  4. Enqueue and drain replacement materialization seeds.

Legacy-pipeline retirement (Phase 2, 2026-07-06): this script no longer hands
off to CycleRunner. The `--run-decision`/`--decision-mode` flags and the
optional step 5 they drove are removed alongside the deleted legacy discovery
pipeline (src.engine.cycle_runtime.execute_discovery_phase); order submission
lives exclusively in the EDLI event-reactor path now.
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
    _download_bayes_precision_fusion_source_clock_raw_inputs_if_needed,
    _download_bayes_precision_fusion_extra_raw_inputs_if_needed,
    _download_replacement_forecast_current_targets_if_needed,
    _enqueue_cycle_advance_reseeds_if_needed,
    _enqueue_fusion_upgrade_reseeds_if_needed,
    _replacement_forecast_live_materialization_queue_config,
)
from src.data.source_clock_update_probe import (  # noqa: E402
    advance_source_clock_cursor,
    probe_openmeteo_source_clock_updates,
    source_clock_scoped_download_cursor_sources,
)


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
        advance_cursor=False,
    )
    report["source_clock_update_probe"] = source_clock_report.as_dict()

    current_download = _download_replacement_forecast_current_targets_if_needed(cfg)
    report["current_target_download"] = _jsonable(current_download)

    scoped_source_clock_download = _download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        cfg,
        source_clock_report=source_clock_report,
    )
    report["source_clock_scoped_extra_model_download"] = _jsonable(scoped_source_clock_download)
    cursor_sources = source_clock_scoped_download_cursor_sources(
        scoped_source_clock_download,
        source_clock_report=source_clock_report,
    )
    report["source_clock_cursor_advanced_sources"] = advance_source_clock_cursor(
        source_clock_report,
        sources=cursor_sources,
    )

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

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-updates-url", default=None)
    parser.add_argument("--no-network", action="store_true", help="Read cached model updates JSONL instead of fetching.")
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
