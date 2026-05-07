# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Lifecycle: created=2026-05-07; last_reviewed=2026-05-07; last_reused=2026-05-07
# Authority basis: LOW/HIGH alignment recovery GO-precheck; derived report only.
# Purpose: Lock read-only OpenData/TIGGE equivalence diagnostic semantics.
# Reuse: Re-check scripts/diagnose_opendata_tigge_equivalence.py before using output as a GO gate.
"""Tests for scripts.diagnose_opendata_tigge_equivalence."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.diagnose_opendata_tigge_equivalence import (
    _compare_raw_grib_metadata_rows,
    build_report,
)


def _write_record(
    root: Path,
    *,
    subdir: str,
    city_slug: str,
    issue_dir: str,
    target_date: str,
    lead_day: int,
    param_id: int,
    short_name: str,
    step_type: str,
    physical_quantity: str,
    selected_steps: list[str] | None,
    members: list[float | None],
    training_allowed: bool = True,
) -> None:
    path = (
        root
        / subdir
        / city_slug
        / issue_dir
        / f"{subdir}_target_{target_date}_lead_{lead_day}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_version": subdir,
        "physical_quantity": physical_quantity,
        "paramId": param_id,
        "short_name": short_name,
        "step_type": step_type,
        "aggregation_window_hours": 6,
        "issue_time_utc": "2026-05-04T00:00:00+00:00",
        "target_date_local": target_date,
        "lead_day": lead_day,
        "unit": "C",
        "training_allowed": training_allowed,
        "members": [
            {"member": index, "value_native_unit": value}
            for index, value in enumerate(members)
        ],
    }
    if selected_steps is not None:
        payload["selected_step_ranges"] = selected_steps
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_report_distinguishes_same_physical_quantity_from_current_object_mismatch(tmp_path: Path) -> None:
    _write_record(
        tmp_path,
        subdir="open_ens_mx2t6_localday_max",
        city_slug="kuala-lumpur",
        issue_dir="20260504",
        target_date="2026-05-10",
        lead_day=6,
        param_id=121,
        short_name="mx2t6",
        step_type="max",
        physical_quantity="mx2t6_local_calendar_day_max",
        selected_steps=["144-150", "150-156"],
        members=[30.0, 31.0, 32.0],
    )
    _write_record(
        tmp_path,
        subdir="tigge_ecmwf_ens_mx2t6_localday_max",
        city_slug="kuala-lumpur",
        issue_dir="20260504",
        target_date="2026-05-10",
        lead_day=6,
        param_id=121,
        short_name="mx2t6",
        step_type="max",
        physical_quantity="mx2t6_local_calendar_day_max",
        selected_steps=["138-144", "144-150", "150-156"],
        members=[30.0, 30.5, 32.0],
    )

    report = build_report(
        cache_root=tmp_path,
        generated_at="2026-05-07T00:00:00+00:00",
    )

    high = report["tracks"]["high"]
    assert high["common_file_keys"] == 1
    assert high["physical_signature_mismatch_common"] == 0
    assert high["selected_step_mismatch_common"] == 1
    assert high["member_value_mismatch_common"] == 1
    assert report["verdict"]["physical_quantity_semantics"] == (
        "same_ecmwf_6h_extrema_when_param_step_member_match"
    )
    assert report["verdict"]["calibration_sharing_go"] is False


def test_report_blocks_when_low_has_no_same_key_comparator(tmp_path: Path) -> None:
    _write_record(
        tmp_path,
        subdir="open_ens_mn2t6_localday_min",
        city_slug="kuala-lumpur",
        issue_dir="20260504",
        target_date="2026-05-10",
        lead_day=6,
        param_id=122,
        short_name="mn2t6",
        step_type="min",
        physical_quantity="mn2t6_local_calendar_day_min",
        selected_steps=["144-150"],
        members=[24.0, 25.0],
    )

    report = build_report(
        cache_root=tmp_path,
        generated_at="2026-05-07T00:00:00+00:00",
    )

    low = report["tracks"]["low"]
    assert low["common_file_keys"] == 0
    assert low["low_or_metric_comparator_gap"] is True
    assert "low_same_key_comparator_gap" in report["verdict"]["why_not_go"]
    assert "paired_tigge_fetch_at_open_data_grid_or_native_model_grid" in report["verdict"]["when_can_be_go"]


def test_report_exposes_horizon_gap_and_bulk_recovery_plan(tmp_path: Path) -> None:
    _write_record(
        tmp_path,
        subdir="open_ens_mx2t6_localday_max",
        city_slug="austin",
        issue_dir="20260504",
        target_date="2026-05-14",
        lead_day=10,
        param_id=121,
        short_name="mx2t6",
        step_type="max",
        physical_quantity="mx2t6_local_calendar_day_max",
        selected_steps=["240-246"],
        members=[30.0],
    )
    _write_record(
        tmp_path,
        subdir="tigge_ecmwf_ens_mx2t6_localday_max",
        city_slug="austin",
        issue_dir="20260504",
        target_date="2026-05-11",
        lead_day=7,
        param_id=121,
        short_name="mx2t6",
        step_type="max",
        physical_quantity="mx2t6_local_calendar_day_max",
        selected_steps=["168-174"],
        members=[30.0],
    )

    report = build_report(
        cache_root=tmp_path,
        generated_at="2026-05-07T00:00:00+00:00",
    )

    high = report["tracks"]["high"]
    assert high["open_leads_missing_in_tigge_cache"] == [10]
    assert "high_tigge_horizon_gap" in report["verdict"]["why_not_go"]
    bulk = report["paired_fetch_plan"]["bulk_recovery"]
    assert any("lead_day 10" in item for item in bulk)


def test_raw_grib_metadata_proves_physical_quantity_separate_from_acquisition_policy() -> None:
    open_row = {
        "centre": "ecmf",
        "marsClass": "od",
        "stream": "enfo",
        "type": "pf",
        "dataDate": 20260504,
        "dataTime": 0,
        "paramId": 122,
        "shortName": "mn2t6",
        "stepType": "min",
        "startStep": 144,
        "endStep": 150,
        "stepRange": "144-150",
        "number": 17,
        "Ni": 1440,
        "Nj": 721,
        "gridType": "regular_ll",
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "packingType": "grid_ccsds",
        "bitsPerValue": 12,
    }
    tigge_row = {
        **open_row,
        "marsClass": "ti",
        "Ni": 181,
        "Nj": 141,
        "iDirectionIncrementInDegrees": 0.5,
        "jDirectionIncrementInDegrees": 0.5,
        "packingType": "grid_simple",
        "bitsPerValue": 16,
    }

    comparison = _compare_raw_grib_metadata_rows(
        open_rows=[open_row],
        tigge_rows=[tigge_row],
        expected={"param_id": 122, "short_name": "mn2t6", "step_type": "min"},
    )

    assert comparison["same_issue_member_step_pairs"] == 1
    assert comparison["source_family_6h_quantity_match"] is True
    assert comparison["all_common_pairs_name_same_physical_quantity"] is True
    assert comparison["acquisition_difference_counts"]["archive_class"] == 1
    assert comparison["acquisition_difference_counts"]["grid_resolution"] == 1
    assert comparison["acquisition_difference_counts"]["packing"] == 1
