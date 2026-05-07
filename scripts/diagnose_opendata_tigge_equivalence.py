#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Lifecycle: created=2026-05-07; last_reviewed=2026-05-07; last_reused=2026-05-07
# Authority basis: LOW/HIGH alignment recovery GO-precheck; derived report only.
# Purpose: Read-only OpenData/TIGGE physical-object equivalence diagnostic.
# Reuse: Re-run after paired 0.25 TIGGE fetch and unified local-day extraction.
"""Diagnose whether ECMWF OpenData and TIGGE serve the same Zeus forecast object.

This script is intentionally read-only.  It does not fetch data, rewrite
extractors, rebuild calibration pairs, fit Platt models, or authorize source
transfer.  It separates three claims that are often conflated:

* physical-quantity semantics: paramId/shortName/stepType name the same ECMWF
  6-hour max/min quantity;
* local extracted object equivalence: current cached JSONs produce the same
  member extrema vector for the same city/issue/target/lead;
* calibration authority: the result is safe to use for live transfer.

Calibration transfer remains blocked unless a paired fetch proves equivalence
through the contract/bin money path.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_cache_root() -> Path:
    candidates = (
        REPO_ROOT.parent / "51 source data" / "raw",
        REPO_ROOT.parent.parent / "workspace-venus" / "51 source data" / "raw",
        Path("/Users/leofitz/.openclaw/workspace-venus/51 source data/raw"),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_CACHE_ROOT = _default_cache_root()

_JSON_NAME_RE = re.compile(r"target_(\d{4}-\d{2}-\d{2})_lead_(\d+)\.json$")
_GRID_SUFFIX_RE = re.compile(r"_grid[0-9p]+x[0-9p]+$")

TRACKS: dict[str, dict[str, Any]] = {
    "high": {
        "temperature_metric": "high",
        "open_subdir": "open_ens_mx2t6_localday_max",
        "tigge_subdir": "tigge_ecmwf_ens_mx2t6_localday_max",
        "param_id": 121,
        "short_name": "mx2t6",
        "step_type": "max",
        "physical_quantity": "mx2t6_local_calendar_day_max",
    },
    "low": {
        "temperature_metric": "low",
        "open_subdir": "open_ens_mn2t6_localday_min",
        "tigge_subdir": "tigge_ecmwf_ens_mn2t6_localday_min",
        "param_id": 122,
        "short_name": "mn2t6",
        "step_type": "min",
        "physical_quantity": "mn2t6_local_calendar_day_min",
    },
}

GO_GATES = (
    "paired_tigge_fetch_at_open_data_grid_or_native_model_grid",
    "same_issue_cycle_param_step_member_grid_metadata",
    "same_local_day_window_attribution_law",
    "same_member_extrema_vector_within_packing_tolerance",
    "same_contract_domain_and_canonical_bin_grid",
    "same_p_raw_vector_and_chosen_edge_bin",
    "cycle_stratified_00z_12z_evidence",
    "low_boundary_status_preserved_or_blocked",
)

NO_GO_PATTERNS = (
    "static_opendata_to_tigge_live_transfer_without_paired_equivalence",
    "mixing_00z_12z_before_cycle_stratified_validation",
    "using_tigge_0p5_archive_to_authorize_opendata_0p25_live_fields",
    "relaxing_low_boundary_law_without_contract_bin_evidence",
)

RAW_GRIB_SAMPLE_STEPS = (144, 150, 156)
RAW_GRIB_ROW_CAP = 240
RAW_GRIB_CANDIDATE_CAP = 32


@dataclass(frozen=True)
class ExtractedRecord:
    path: Path
    city_slug: str
    issue_dir: str
    target_date: str
    lead_day: int

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.city_slug, self.issue_dir, self.target_date, self.lead_day)


@dataclass(frozen=True)
class RawGribCandidate:
    path: Path
    source_family: str


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _index_extracted_json(base: Path) -> dict[tuple[str, str, str, int], ExtractedRecord]:
    out: dict[tuple[str, str, str, int], ExtractedRecord] = {}
    if not base.exists():
        return out
    for path in sorted(base.glob("*/*/*.json")):
        match = _JSON_NAME_RE.search(path.name)
        if not match:
            continue
        city_slug = path.parts[-3]
        raw_issue_dir = path.parts[-2]
        issue_dir = _GRID_SUFFIX_RE.sub("", raw_issue_dir)
        target_date = match.group(1)
        lead_day = int(match.group(2))
        record = ExtractedRecord(
            path=path,
            city_slug=city_slug,
            issue_dir=issue_dir,
            target_date=target_date,
            lead_day=lead_day,
        )
        existing = out.get(record.key)
        if existing is None or "_grid" in raw_issue_dir:
            out[record.key] = record
    return out


def _member_values(payload: dict[str, Any]) -> dict[int, float | None]:
    values: dict[int, float | None] = {}
    for row in payload.get("members", []):
        member = row.get("member")
        if member is None:
            continue
        value = row.get("value_native_unit")
        values[int(member)] = None if value is None else float(value)
    return values


def _selected_steps(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("selected_step_ranges")
    if raw is None:
        raw = payload.get("selected_step_ranges_inner")
    return tuple(str(item) for item in (raw or ()))


def _physical_signature(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "paramId": payload.get("paramId"),
        "short_name": payload.get("short_name"),
        "step_type": payload.get("step_type"),
        "aggregation_window_hours": payload.get("aggregation_window_hours"),
        "physical_quantity": payload.get("physical_quantity"),
        "unit": payload.get("unit"),
    }


def _physical_signature_compatible(
    left: dict[str, Any],
    right: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    return (
        left.get("paramId") == right.get("paramId") == expected["param_id"]
        and left.get("short_name") == right.get("short_name") == expected["short_name"]
        and left.get("step_type") == right.get("step_type") == expected["step_type"]
        and left.get("aggregation_window_hours") == right.get("aggregation_window_hours") == 6
        and left.get("unit") == right.get("unit")
    )


def _raw_grib_signature(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "centre": row.get("centre"),
        "stream": row.get("stream"),
        "paramId": row.get("paramId"),
        "shortName": row.get("shortName"),
        "stepType": row.get("stepType"),
        "startStep": row.get("startStep"),
        "endStep": row.get("endStep"),
        "stepRange": row.get("stepRange"),
        "number": row.get("number"),
    }


def _raw_grib_physical_quantity_same(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    expected: dict[str, Any],
) -> bool:
    """Return true only for the same ECMWF member/step physical object.

    Archive class, packing, and grid are intentionally excluded here.  Those
    fields can make the *local extracted object* non-equivalent, but they do
    not change whether the GRIB message names the same 6-hour physical
    quantity.
    """
    return (
        left.get("centre") == right.get("centre") == "ecmf"
        and left.get("stream") == right.get("stream") == "enfo"
        and left.get("paramId") == right.get("paramId") == expected["param_id"]
        and left.get("shortName") == right.get("shortName") == expected["short_name"]
        and left.get("stepType") == right.get("stepType") == expected["step_type"]
        and left.get("startStep") == right.get("startStep")
        and left.get("endStep") == right.get("endStep")
        and left.get("stepRange") == right.get("stepRange")
        and left.get("number") == right.get("number")
    )


def _row_names_expected_6h_quantity(row: dict[str, Any], *, expected: dict[str, Any]) -> bool:
    try:
        window_hours = int(row.get("endStep")) - int(row.get("startStep"))
    except Exception:
        window_hours = None
    return (
        row.get("centre") == "ecmf"
        and row.get("stream") == "enfo"
        and row.get("paramId") == expected["param_id"]
        and row.get("shortName") == expected["short_name"]
        and row.get("stepType") == expected["step_type"]
        and window_hours == 6
    )


def _raw_grib_acquisition_differences(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    checks = {
        "archive_class": ("marsClass", "class"),
        "message_type": ("type",),
        "grid_type": ("gridType",),
        "grid_resolution": ("iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees"),
        "grid_shape": ("Ni", "Nj"),
        "packing": ("packingType", "bitsPerValue"),
    }
    for label, keys in checks.items():
        if any(left.get(key) != right.get(key) for key in keys):
            differences.append(label)
    return differences


def _raw_grib_candidates(cache_root: Path, metric: str) -> dict[str, list[RawGribCandidate]]:
    config = TRACKS[metric]
    open_pattern = (
        cache_root
        / "ecmwf_open_ens"
        / "*"
        / "*"
        / f"*params_{config['short_name']}.grib2"
    )
    tigge_pattern = (
        cache_root
        / f"tigge_ecmwf_ens_regions_{config['short_name']}"
        / "*"
        / "*"
        / f"*param_{config['param_id']}_128*.grib"
    )
    def sort_key(candidate: RawGribCandidate) -> tuple[int, int, str]:
        dates = [int(match) for match in re.findall(r"20\d{6}", str(candidate.path))]
        newest_date = max(dates) if dates else 0
        perturbed_first = 1 if "perturbed" in candidate.path.name else 0
        return (newest_date, perturbed_first, str(candidate.path))

    open_candidates = [
        RawGribCandidate(path=path, source_family="opendata")
        for path in cache_root.glob(str(open_pattern.relative_to(cache_root)))
    ]
    tigge_candidates = [
        RawGribCandidate(path=path, source_family="tigge")
        for path in cache_root.glob(str(tigge_pattern.relative_to(cache_root)))
    ]
    return {
        "open": sorted(open_candidates, key=sort_key, reverse=True),
        "tigge": sorted(tigge_candidates, key=sort_key, reverse=True),
    }


def _read_grib_metadata_rows(
    *,
    candidate: RawGribCandidate,
    expected: dict[str, Any],
    scan_limit: int,
) -> list[dict[str, Any]]:
    eccodes = importlib.import_module("eccodes")
    codes_grib_new_from_file = eccodes.codes_grib_new_from_file
    codes_get = eccodes.codes_get
    codes_release = eccodes.codes_release

    keys = (
        "edition",
        "centre",
        "class",
        "marsClass",
        "stream",
        "type",
        "dataDate",
        "dataTime",
        "paramId",
        "shortName",
        "stepType",
        "startStep",
        "endStep",
        "stepRange",
        "number",
        "Ni",
        "Nj",
        "gridType",
        "iDirectionIncrementInDegrees",
        "jDirectionIncrementInDegrees",
        "packingType",
        "bitsPerValue",
    )
    rows: list[dict[str, Any]] = []
    messages_seen = 0
    with candidate.path.open("rb") as fh:
        while messages_seen < scan_limit:
            try:
                gid = codes_grib_new_from_file(fh)
            except Exception as exc:
                rows.append(
                    {
                        "path": str(candidate.path),
                        "source_family": candidate.source_family,
                        "read_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                break
            if gid is None:
                break
            messages_seen += 1
            try:
                try:
                    param_id = int(codes_get(gid, "paramId"))
                    end_step = int(codes_get(gid, "endStep"))
                except Exception:
                    continue
                if param_id != expected["param_id"] or end_step not in RAW_GRIB_SAMPLE_STEPS:
                    continue
                row: dict[str, Any] = {
                    "path": str(candidate.path),
                    "source_family": candidate.source_family,
                }
                for key in keys:
                    try:
                        row[key] = codes_get(gid, key)
                    except Exception:
                        row[key] = None
                rows.append(row)
            finally:
                codes_release(gid)
            if len(rows) >= 12:
                break
    return rows


def _grib_pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("dataDate"),
        row.get("dataTime"),
        row.get("paramId"),
        row.get("shortName"),
        row.get("stepType"),
        row.get("startStep"),
        row.get("endStep"),
        row.get("stepRange"),
        row.get("number"),
    )


def _compare_raw_grib_metadata_rows(
    *,
    open_rows: list[dict[str, Any]],
    tigge_rows: list[dict[str, Any]],
    expected: dict[str, Any],
) -> dict[str, Any]:
    open_errors = [row for row in open_rows if row.get("read_error")]
    tigge_errors = [row for row in tigge_rows if row.get("read_error")]
    open_rows = [row for row in open_rows if not row.get("read_error")]
    tigge_rows = [row for row in tigge_rows if not row.get("read_error")]
    source_family_match = (
        bool(open_rows)
        and bool(tigge_rows)
        and all(_row_names_expected_6h_quantity(row, expected=expected) for row in open_rows)
        and all(_row_names_expected_6h_quantity(row, expected=expected) for row in tigge_rows)
    )
    open_by_key = {_grib_pair_key(row): row for row in open_rows}
    tigge_by_key = {_grib_pair_key(row): row for row in tigge_rows}
    common_keys = sorted(set(open_by_key) & set(tigge_by_key))

    example: dict[str, Any] | None = None
    physical_matches = 0
    acquisition_differences: Counter[str] = Counter()
    for key in common_keys:
        open_row = open_by_key[key]
        tigge_row = tigge_by_key[key]
        same = _raw_grib_physical_quantity_same(open_row, tigge_row, expected=expected)
        if same:
            physical_matches += 1
        for difference in _raw_grib_acquisition_differences(open_row, tigge_row):
            acquisition_differences[difference] += 1
        if example is None:
            example = {
                "open": open_row,
                "tigge": tigge_row,
                "physical_signature_equal_ignoring_archive_transport": same,
                "local_acquisition_differences": _raw_grib_acquisition_differences(
                    open_row,
                    tigge_row,
                ),
            }

    if example is None and open_rows and tigge_rows:
        open_row = open_rows[0]
        tigge_row = tigge_rows[0]
        example = {
            "open": open_row,
            "tigge": tigge_row,
            "physical_signature_equal_ignoring_archive_transport": (
                _raw_grib_signature(open_row) == _raw_grib_signature(tigge_row)
            ),
            "local_acquisition_differences": _raw_grib_acquisition_differences(
                open_row,
                tigge_row,
            ),
            "note": "No same issue/member/step pair was found in the scanned rows; this is only a source-family physical-signature sample.",
        }

    return {
        "source_family_6h_quantity_match": source_family_match,
        "same_issue_member_step_pairs": len(common_keys),
        "physical_quantity_matches": physical_matches,
        "all_common_pairs_name_same_physical_quantity": (
            bool(common_keys) and physical_matches == len(common_keys)
        ),
        "acquisition_difference_counts": dict(sorted(acquisition_differences.items())),
        "open_read_errors": open_errors[:5],
        "tigge_read_errors": tigge_errors[:5],
        "example": example,
    }


def _raw_grib_metadata_probe(
    *,
    cache_root: Path,
    scan_limit: int,
) -> dict[str, Any]:
    try:
        importlib.import_module("eccodes")
    except Exception as exc:
        return {
            "enabled": True,
            "eccodes_available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    tracks: dict[str, Any] = {}
    for metric in ("high", "low"):
        expected = TRACKS[metric]
        candidates = _raw_grib_candidates(cache_root, metric)
        open_rows: list[dict[str, Any]] = []
        tigge_rows: list[dict[str, Any]] = []
        open_files_scanned = 0
        tigge_files_scanned = 0
        for candidate in candidates["open"][:RAW_GRIB_CANDIDATE_CAP]:
            open_files_scanned += 1
            open_rows.extend(
                _read_grib_metadata_rows(
                    candidate=candidate,
                    expected=expected,
                    scan_limit=scan_limit,
                )
            )
            if len(open_rows) >= RAW_GRIB_ROW_CAP:
                break
        for candidate in candidates["tigge"][:RAW_GRIB_CANDIDATE_CAP]:
            tigge_files_scanned += 1
            tigge_rows.extend(
                _read_grib_metadata_rows(
                    candidate=candidate,
                    expected=expected,
                    scan_limit=scan_limit,
                )
            )
            if len(tigge_rows) >= RAW_GRIB_ROW_CAP:
                break
        comparison = _compare_raw_grib_metadata_rows(
            open_rows=open_rows,
            tigge_rows=tigge_rows,
            expected=expected,
        )
        tracks[metric] = {
            "expected": {
                "paramId": expected["param_id"],
                "short_name": expected["short_name"],
                "step_type": expected["step_type"],
                "aggregation_window_hours": 6,
            },
            "open_candidate_count": len(candidates["open"]),
            "tigge_candidate_count": len(candidates["tigge"]),
            "open_files_scanned": open_files_scanned,
            "tigge_files_scanned": tigge_files_scanned,
            "open_sample_rows": len(open_rows),
            "tigge_sample_rows": len(tigge_rows),
            **comparison,
        }

    return {
        "enabled": True,
        "eccodes_available": True,
        "interpretation": (
            "paramId/shortName/stepType/startStep/endStep/member equality proves the same ECMWF 6-hour extrema message; "
            "grid, packing, archive class, and local-day extraction remain separate GO gates."
        ),
        "tracks": tracks,
    }


def _compare_records(
    *,
    metric: str,
    open_record: ExtractedRecord,
    tigge_record: ExtractedRecord,
) -> dict[str, Any]:
    expected = TRACKS[metric]
    open_payload = _read_json(open_record.path)
    tigge_payload = _read_json(tigge_record.path)

    open_signature = _physical_signature(open_payload)
    tigge_signature = _physical_signature(tigge_payload)
    open_steps = _selected_steps(open_payload)
    tigge_steps = _selected_steps(tigge_payload)
    open_values = _member_values(open_payload)
    tigge_values = _member_values(tigge_payload)

    diffs: list[float] = []
    null_member_count = 0
    for member in sorted(set(open_values) & set(tigge_values)):
        open_value = open_values[member]
        tigge_value = tigge_values[member]
        if open_value is None or tigge_value is None:
            null_member_count += 1
            continue
        diffs.append(open_value - tigge_value)

    max_abs_diff = max((abs(value) for value in diffs), default=None)
    mean_abs_diff = (
        sum(abs(value) for value in diffs) / len(diffs)
        if diffs
        else None
    )
    physical_compatible = _physical_signature_compatible(
        open_signature,
        tigge_signature,
        expected,
    )

    return {
        "key": {
            "city_slug": open_record.city_slug,
            "issue_dir": open_record.issue_dir,
            "target_date": open_record.target_date,
            "lead_day": open_record.lead_day,
        },
        "physical_signature_compatible": physical_compatible,
        "open_physical_signature": open_signature,
        "tigge_physical_signature": tigge_signature,
        "open_issue_time": open_payload.get("issue_time_utc"),
        "tigge_issue_time": tigge_payload.get("issue_time_utc"),
        "open_selected_steps": list(open_steps),
        "tigge_selected_steps": list(tigge_steps),
        "selected_steps_equal": open_steps == tigge_steps,
        "comparable_member_count": len(diffs),
        "null_member_count": null_member_count,
        "max_abs_member_diff_native_unit": max_abs_diff,
        "mean_abs_member_diff_native_unit": mean_abs_diff,
        "member_extrema_equal": bool(diffs) and max_abs_diff is not None and max_abs_diff <= 1e-9,
        "open_training_allowed": open_payload.get("training_allowed"),
        "tigge_training_allowed": tigge_payload.get("training_allowed"),
        "open_path": str(open_record.path),
        "tigge_path": str(tigge_record.path),
    }


def _coverage_summary(records: dict[tuple[str, str, str, int], ExtractedRecord]) -> dict[str, Any]:
    leads: Counter[int] = Counter()
    issues: Counter[str] = Counter()
    for record in records.values():
        leads[record.lead_day] += 1
        issues[record.issue_dir] += 1
    return {
        "json_files": len(records),
        "lead_days": {str(k): v for k, v in sorted(leads.items())},
        "top_issue_dirs": [
            {"issue_dir": issue, "count": count}
            for issue, count in issues.most_common(10)
        ],
    }


def _compare_track(
    *,
    metric: str,
    cache_root: Path,
    example_limit: int,
) -> dict[str, Any]:
    config = TRACKS[metric]
    open_records = _index_extracted_json(cache_root / config["open_subdir"])
    tigge_records = _index_extracted_json(cache_root / config["tigge_subdir"])
    common_keys = sorted(set(open_records) & set(tigge_records))

    physical_signature_mismatch = 0
    selected_step_mismatch = 0
    member_value_mismatch = 0
    member_value_exact = 0
    comparable_value_records = 0
    max_abs_diffs: list[float] = []
    mean_abs_diffs: list[float] = []
    examples: list[dict[str, Any]] = []

    for key in common_keys:
        comparison = _compare_records(
            metric=metric,
            open_record=open_records[key],
            tigge_record=tigge_records[key],
        )
        if not comparison["physical_signature_compatible"]:
            physical_signature_mismatch += 1
        if not comparison["selected_steps_equal"]:
            selected_step_mismatch += 1
        if comparison["comparable_member_count"]:
            comparable_value_records += 1
            max_abs = comparison["max_abs_member_diff_native_unit"]
            mean_abs = comparison["mean_abs_member_diff_native_unit"]
            if max_abs is not None:
                max_abs_diffs.append(float(max_abs))
                if max_abs <= 1e-9:
                    member_value_exact += 1
                else:
                    member_value_mismatch += 1
            if mean_abs is not None:
                mean_abs_diffs.append(float(mean_abs))
        if len(examples) < example_limit and (
            not comparison["physical_signature_compatible"]
            or not comparison["selected_steps_equal"]
            or not comparison["member_extrema_equal"]
        ):
            examples.append(comparison)

    open_leads = {record.lead_day for record in open_records.values()}
    tigge_leads = {record.lead_day for record in tigge_records.values()}
    horizon_gap = sorted(open_leads - tigge_leads)
    archive_only_leads = sorted(tigge_leads - open_leads)

    return {
        "metric": metric,
        "expected_physical_quantity": {
            "paramId": config["param_id"],
            "short_name": config["short_name"],
            "step_type": config["step_type"],
            "aggregation_window_hours": 6,
            "physical_quantity": config["physical_quantity"],
        },
        "open_cache": _coverage_summary(open_records),
        "tigge_cache": _coverage_summary(tigge_records),
        "common_file_keys": len(common_keys),
        "physical_signature_mismatch_common": physical_signature_mismatch,
        "selected_step_mismatch_common": selected_step_mismatch,
        "comparable_value_common": comparable_value_records,
        "member_value_mismatch_common": member_value_mismatch,
        "member_value_exact_common": member_value_exact,
        "max_of_max_abs_member_diff_native_unit": max(max_abs_diffs) if max_abs_diffs else None,
        "median_mean_abs_member_diff_native_unit": (
            sorted(mean_abs_diffs)[len(mean_abs_diffs) // 2]
            if mean_abs_diffs
            else None
        ),
        "open_leads_missing_in_tigge_cache": horizon_gap,
        "tigge_archive_only_leads": archive_only_leads,
        "low_or_metric_comparator_gap": len(common_keys) == 0,
        "examples": examples,
    }


def _verdict(track_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    high = track_reports["high"]
    low = track_reports["low"]
    current_cache_equivalent = (
        high["common_file_keys"] > 0
        and high["physical_signature_mismatch_common"] == 0
        and high["selected_step_mismatch_common"] == 0
        and high["member_value_mismatch_common"] == 0
        and low["common_file_keys"] > 0
        and low["physical_signature_mismatch_common"] == 0
        and low["selected_step_mismatch_common"] == 0
        and low["member_value_mismatch_common"] == 0
    )
    root_causes: list[str] = []
    for metric, report in track_reports.items():
        if report["common_file_keys"] == 0:
            root_causes.append(f"{metric}_same_key_comparator_gap")
        if report["selected_step_mismatch_common"]:
            root_causes.append(f"{metric}_local_day_window_law_mismatch")
        if report["member_value_mismatch_common"]:
            root_causes.append(f"{metric}_member_extrema_mismatch_current_cache")
        if report["open_leads_missing_in_tigge_cache"]:
            root_causes.append(f"{metric}_tigge_horizon_gap")

    return {
        "physical_quantity_semantics": "same_ecmwf_6h_extrema_when_param_step_member_match",
        "current_cached_extracted_object_equivalent": current_cache_equivalent,
        "calibration_sharing_go": False,
        "law1_relaxation_go": False,
        "why_not_go": root_causes,
        "when_can_be_go": list(GO_GATES),
        "forbidden_until_go": list(NO_GO_PATTERNS),
    }


def _paired_fetch_plan() -> dict[str, Any]:
    return {
        "objective": "prove OpenData live fields and TIGGE archived fields are the same contract forecast object after local asymmetry is removed",
        "runtime_config_gap": {
            "current_observation": "OpenData runtime previously downloaded through step 240. UTC+7/8/9 lead_day=10 local-day extrema can require step 258, and UTC-negative 00Z city-local D+10 windows can require step 276.",
            "required_update": "00Z/12Z full-horizon OpenData and TIGGE recovery fetches must cover every 6h step through 276; 06Z/18Z remain short-horizon/shadow.",
            "why": "The contract object is a city-local calendar day, not a UTC issue-day horizon. A D+10 local day can extend beyond +240h from the source cycle.",
        },
        "minimal_probe": {
            "date": "2026-05-04",
            "cycle": "00Z",
            "area": "Kuala Lumpur point or small box",
            "grid": "0.25/0.25",
            "params": ["121.128", "122.128"],
            "steps": [132, 138, 144, 150, 156],
            "types": ["cf", "pf 1/to/50"],
            "compare": [
                "GRIB class/origin/stream/type/paramId/shortName/stepType/startStep/endStep/member",
                "nearest grid point and packing precision",
                "member values at same grid point",
                "local-day extrema after unified attribution",
                "canonical p_raw vector and bin assignment",
            ],
        },
        "verified_dry_run_commands": [
            "/Users/leofitz/miniconda3/bin/python '51 source data/scripts/tigge_download_ecmwf_ens_region_multistep.py' asia --date 2026-05-04 --steps 144 150 --param 121.128 --region-subdir tigge_ecmwf_ens_regions_mx2t6 --cycle 00 --grid 0.25/0.25 --dry-run",
            "/Users/leofitz/miniconda3/bin/python '51 source data/scripts/tigge_mn2t6_download_resumable.py' --date-from 2026-05-04 --date-to 2026-05-04 --max-target-lead-day 10 --cities 'Kuala Lumpur' Singapore Jakarta --cycle 12 --grid 0.25/0.25 --dry-run --max-passes 1 --max-workers 1",
        ],
        "bulk_recovery": [
            "download TIGGE with the same grid policy as OpenData (0.25/0.25) or prove native-grid transfer tolerance before using native-grid data",
            "extend TIGGE horizon to the contract horizon, lead_day 10 / step 276 for all configured city-local D+10 full-cycle windows",
            "extend OpenData 00Z/12Z runtime step coverage to the same contract horizon; do not treat a 240h source run as complete for D+10 local-day bins",
            "run 00Z and 12Z as separate source-cycle strata",
            "extract OpenData and TIGGE through one shared window-attribution engine",
            "write recovered rows under a new data_version or provenance flag; never mutate old semantics in place",
            "rebuild LOW pairs only when ForecastToBinEvidence proves target local day and bin identity",
        ],
    }


def build_report(
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    generated_at: str | None = None,
    example_limit: int = 8,
    include_grib_metadata: bool = False,
    grib_scan_limit: int = 300,
) -> dict[str, Any]:
    track_reports = {
        metric: _compare_track(
            metric=metric,
            cache_root=cache_root,
            example_limit=example_limit,
        )
        for metric in ("high", "low")
    }
    report = {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "derived_context_only": True,
        "live_behavior_changed": False,
        "cache_root": str(cache_root),
        "tracks": track_reports,
        "verdict": _verdict(track_reports),
        "paired_fetch_plan": _paired_fetch_plan(),
    }
    if include_grib_metadata:
        report["raw_grib_metadata_probe"] = _raw_grib_metadata_probe(
            cache_root=cache_root,
            scan_limit=max(1, grib_scan_limit),
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--example-limit", type=int, default=8)
    parser.add_argument("--include-grib-metadata", action="store_true")
    parser.add_argument("--grib-scan-limit", type=int, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(
        cache_root=args.cache_root,
        example_limit=max(0, args.example_limit),
        include_grib_metadata=args.include_grib_metadata,
        grib_scan_limit=args.grib_scan_limit,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
