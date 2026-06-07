# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect durable fine-tune artifact generation for replacement data refit.
# Reuse: Run before consuming soft-anchor fine-tune output in refit or promotion evidence.
# Authority basis: Fine-tune/refit evidence must be inspectable data, not in-memory test-only objects.
"""Replacement forecast fine-tune artifact tests."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from src.data.replacement_forecast_finetune_artifact import (
    build_replacement_forecast_finetune_artifact,
    parameter_key,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import SoftAnchorParameter


REPO_ROOT = Path(__file__).resolve().parents[1]
PARAM_SELECTED = SoftAnchorParameter(anchor_weight=0.80, anchor_sigma_c=3.00)
PARAM_OTHER = SoftAnchorParameter(anchor_weight=0.60, anchor_sigma_c=4.00)


def _payload(*, days: int = 5, rows_per_day: int = 50) -> dict[str, object]:
    rows = []
    start = date(2026, 6, 1)
    for offset in range(days):
        for idx in range(rows_per_day):
            rows.append(
                {
                    "official_date": (start + timedelta(days=offset)).isoformat(),
                    "city": f"City{idx}",
                    "temperature_metric": "high",
                    "bin_id": "warm",
                    "truth_authority": "VERIFIED",
                    "settled_bin_id": "warm",
                    "guardrail_bucket": "standard",
                    "probabilities_by_parameter": {
                        parameter_key(PARAM_SELECTED): {"warm": 0.80, "miss": 0.20},
                        parameter_key(PARAM_OTHER): {"warm": 0.40, "miss": 0.60},
                    },
                }
            )
    return {
        "candidate_grid": [parameter_key(PARAM_SELECTED), parameter_key(PARAM_OTHER)],
        "rows": rows,
    }


def test_finetune_artifact_contains_nested_fold_scores_and_selected_parameter() -> None:
    artifact = build_replacement_forecast_finetune_artifact(
        _payload(),
        generated_at="2026-06-06T08:00:00+00:00",
        source_path="rows.json",
    )

    payload = artifact.as_dict()
    result = payload["result"]
    assert artifact.ready_for_refit is True
    assert payload["status"] == "FINE_TUNE_ARTIFACT_READY"
    assert payload["row_count"] == 250
    assert result["official_days"] == 5
    assert result["official_rows"] == 250
    assert result["selected_parameter"] == parameter_key(PARAM_SELECTED)
    assert result["mean_holdout_brier"] is not None
    assert result["mean_holdout_log_loss"] is not None
    assert len(result["folds"]) == 5
    assert result["guardrail_bucket_coverage"][0]["status"] == "PASS"


def test_finetune_artifact_blocks_small_sample_from_refit_ready() -> None:
    artifact = build_replacement_forecast_finetune_artifact(
        _payload(days=1, rows_per_day=10),
        generated_at="2026-06-06T08:00:00+00:00",
    )

    assert artifact.ready_for_refit is False
    assert artifact.status == "FINE_TUNE_ARTIFACT_SHADOW_ONLY"
    assert "REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_DAYS" in artifact.reason_codes
    assert "REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_ROWS" in artifact.reason_codes


def test_finetune_artifact_cli_writes_json_and_returns_nonzero_for_shadow_only(tmp_path: Path) -> None:
    ready_input = tmp_path / "ready_rows.json"
    ready_output = tmp_path / "ready_artifact.json"
    ready_input.write_text(json.dumps(_payload()), encoding="utf-8")

    ready = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_finetune_artifact.py",
            "--input-json",
            str(ready_input),
            "--output-json",
            str(ready_output),
            "--generated-at",
            "2026-06-06T08:00:00+00:00",
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(ready.stdout)["status"] == "FINE_TUNE_ARTIFACT_READY"
    assert json.loads(ready_output.read_text(encoding="utf-8"))["ready_for_refit"] is True

    small_input = tmp_path / "small_rows.json"
    small_input.write_text(json.dumps(_payload(days=1, rows_per_day=10)), encoding="utf-8")
    small = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_finetune_artifact.py",
            "--input-json",
            str(small_input),
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert small.returncode == 1
    assert json.loads(small.stdout)["status"] == "FINE_TUNE_ARTIFACT_SHADOW_ONLY"
