# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect product-specific replacement refit handoff artifacts.
# Reuse: Run before using replacement fine-tune output to drive EMOS/data refit.
# Authority basis: Replacement refit must be product-keyed, non-live, and inspectable.
"""Replacement forecast refit handoff tests."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.data.replacement_forecast_finetune_artifact import (
    build_replacement_forecast_finetune_artifact,
    parameter_key,
)
from src.data.replacement_forecast_refit_handoff import (
    HANDOFF_SCHEMA_VERSION,
    build_replacement_forecast_refit_handoff,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import SoftAnchorParameter


REPO_ROOT = Path(__file__).resolve().parents[1]
PARAM_SELECTED = SoftAnchorParameter(anchor_weight=0.80, anchor_sigma_c=3.00)
PARAM_OTHER = SoftAnchorParameter(anchor_weight=0.60, anchor_sigma_c=4.00)


def _fine_tune_payload(*, days: int = 5, rows_per_day: int = 50) -> dict[str, object]:
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


def _artifact(*, days: int = 5, rows_per_day: int = 50) -> dict[str, object]:
    return build_replacement_forecast_finetune_artifact(
        _fine_tune_payload(days=days, rows_per_day=rows_per_day),
        generated_at="2026-06-06T08:00:00+00:00",
    ).as_dict()


def test_refit_handoff_builds_product_keyed_non_live_artifact() -> None:
    handoff = build_replacement_forecast_refit_handoff(
        fine_tune_artifact=_artifact(),
        city="Shanghai",
        season="JJA",
        metric="high",
        generated_at="2026-06-06T09:00:00+00:00",
    )

    payload = handoff.as_dict()
    assert payload["schema_version"] == HANDOFF_SCHEMA_VERSION
    assert payload["status"] == "REFIT_HANDOFF_READY"
    assert payload["ready_for_product_refit"] is True
    assert payload["live_promotion_allowed"] is False
    assert payload["baseline_calibration_reused"] is False
    assert payload["training_scope"] == "replacement_product_specific_only"
    assert payload["selected_parameter"] == parameter_key(PARAM_SELECTED)
    assert payload["official_days"] == 5
    assert payload["official_rows"] == 250
    assert payload["min_guardrail_bucket_rows"] == 250
    assert payload["emos_cell_key"] == (
        "Shanghai|JJA|high|derived_posterior|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1"
    )
    assert payload["refit_decision"]["status"] == "PRODUCT_SPECIFIC_REFIT_READY"
    assert payload["refit_decision"]["live_promotion_allowed"] is False


def test_refit_handoff_blocks_unready_finetune_and_live_promotion_request() -> None:
    small = build_replacement_forecast_refit_handoff(
        fine_tune_artifact=_artifact(days=1, rows_per_day=10),
        city="Shanghai",
        season="JJA",
        metric="high",
    )

    assert small.status == "REFIT_HANDOFF_BLOCKED"
    assert "REPLACEMENT_REFIT_HANDOFF_FINE_TUNE_ARTIFACT_NOT_READY" in small.reason_codes
    assert "REPLACEMENT_REFIT_INSUFFICIENT_OFFICIAL_DAYS" in small.reason_codes

    live_requested = build_replacement_forecast_refit_handoff(
        fine_tune_artifact=_artifact(),
        city="Shanghai",
        season="JJA",
        metric="high",
        live_promotion_requested=True,
    )

    assert live_requested.status == "REFIT_HANDOFF_BLOCKED"
    assert "REPLACEMENT_REFIT_HANDOFF_LIVE_PROMOTION_NOT_ALLOWED" in live_requested.reason_codes
    assert live_requested.live_promotion_allowed is False


def test_refit_handoff_rejects_short_alias() -> None:
    with pytest.raises(ValueError, match="full replacement identity"):
        build_replacement_forecast_refit_handoff(
            fine_tune_artifact=_artifact(),
            city="Shanghai",
            season="JJA",
            metric="high",
            product_id="short_" + "h" + "3_alias",
        )


def test_refit_handoff_cli_writes_artifact_and_blocks_live_promotion(tmp_path: Path) -> None:
    artifact_path = tmp_path / "fine_tune.json"
    handoff_path = tmp_path / "handoff.json"
    artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")

    ready = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_refit_handoff.py",
            "--fine-tune-artifact-json",
            str(artifact_path),
            "--city",
            "Shanghai",
            "--season",
            "JJA",
            "--metric",
            "high",
            "--generated-at",
            "2026-06-06T09:00:00+00:00",
            "--output-json",
            str(handoff_path),
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(ready.stdout)["status"] == "REFIT_HANDOFF_READY"
    assert json.loads(handoff_path.read_text(encoding="utf-8"))["ready_for_product_refit"] is True

    blocked = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_refit_handoff.py",
            "--fine-tune-artifact-json",
            str(artifact_path),
            "--city",
            "Shanghai",
            "--season",
            "JJA",
            "--metric",
            "high",
            "--live-promotion-requested",
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["status"] == "REFIT_HANDOFF_BLOCKED"
