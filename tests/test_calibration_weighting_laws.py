# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-05-08; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Safe calibration weighting LAW antibodies that do not require schema/data migration.
# Reuse: Run for calibration weighting LAW 2-5 code/config changes.
# Authority basis: docs/reference/zeus_calibration_weighting_authority.md; Wave37 PLAN.
"""Calibration weighting LAW antibodies for the safe, non-migration subset.

LAW 1 row-level ``precision_weight`` checks are intentionally not asserted here:
the current schema has no persisted precision-weight authority. Wave37 records
that as a data-layer known gap rather than making an impossible active test.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import (
    calibration_batch_rebuild_n_mc,
    ensemble_n_mc,
    load_cities,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CITIES_JSON = PROJECT_ROOT / "config" / "cities.json"
REBUILD_V2 = PROJECT_ROOT / "scripts" / "rebuild_calibration_pairs_v2.py"

LAW2_WEIGHTED_LOW_OPT_OUT_CITIES = {
    "Jakarta",
    "Busan",
    "Hong Kong",
    "NYC",
    "Houston",
    "Chicago",
    "Guangzhou",
    "Beijing",
}

PRODUCTION_WEIGHTING_SOURCES = (
    PROJECT_ROOT / "scripts" / "rebuild_calibration_pairs_v2.py",
    PROJECT_ROOT / "scripts" / "refit_platt_v2.py",
    PROJECT_ROOT / "src" / "calibration" / "manager.py",
    PROJECT_ROOT / "src" / "calibration" / "platt.py",
    PROJECT_ROOT / "src" / "calibration" / "store.py",
    PROJECT_ROOT / "src" / "contracts" / "snapshot_ingest_contract.py",
    PROJECT_ROOT / "src" / "data" / "calibration_transfer_policy.py",
    PROJECT_ROOT / "src" / "strategy" / "market_fusion.py",
)

TEMP_DELTA_WEIGHT_PATTERNS = (
    re.compile(r"\btemp(?:erature)?_delta_(?:weight|weighted|weighting)\b", re.I),
    re.compile(r"\bdelta_t_(?:weight|weighted|weighting)\b", re.I),
    re.compile(r"\bweight(?:ed|ing)?_by_(?:temp|temperature)_delta\b", re.I),
    re.compile(r"\bE_temp_delta\b"),
)

PER_CITY_ALPHA_PATTERNS = (
    re.compile(r"\bper_city_alpha\b", re.I),
    re.compile(r"\balpha_by_city\b", re.I),
    re.compile(r"\bcity_alpha\b", re.I),
    re.compile(r"\bcities\s*\[[^\]]+\]\s*\.\s*alpha\b", re.I),
)


def _source_text_without_line_comments(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_per_city_weighting_eligibility_explicit_and_matches_law2():
    raw = json.loads(CITIES_JSON.read_text(encoding="utf-8"))
    by_name = {city["name"]: city for city in raw["cities"]}

    non_bool = {
        name: city.get("weighted_low_calibration_eligible")
        for name, city in by_name.items()
        if type(city.get("weighted_low_calibration_eligible")) is not bool
    }
    assert non_bool == {}

    false_cities = {
        name
        for name, city in by_name.items()
        if city["weighted_low_calibration_eligible"] is False
    }
    assert false_cities == LAW2_WEIGHTED_LOW_OPT_OUT_CITIES

    loaded = {city.name: city.weighted_low_calibration_eligible for city in load_cities()}
    assert false_cities == {name for name, eligible in loaded.items() if not eligible}


def test_rebuild_n_mc_default_bounded_for_batch_training():
    default = calibration_batch_rebuild_n_mc()

    assert 100 <= default <= 2000
    assert default < ensemble_n_mc()

    source = REBUILD_V2.read_text(encoding="utf-8")
    assert "calibration_batch_rebuild_n_mc()" in source
    assert "else ensemble_n_mc()" not in source


def test_no_temp_delta_magnitude_weighting_in_production():
    violations: list[str] = []
    for path in PRODUCTION_WEIGHTING_SOURCES:
        source = _source_text_without_line_comments(path)
        for pattern in TEMP_DELTA_WEIGHT_PATTERNS:
            if pattern.search(source):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} matches {pattern.pattern}")

    assert violations == []


def test_rebuild_uses_per_city_metric_savepoint_not_outer_monolith():
    source = REBUILD_V2.read_text(encoding="utf-8")

    assert "SAVEPOINT v2_rebuild_bucket" in source
    assert "ROLLBACK TO SAVEPOINT v2_rebuild_bucket" in source
    assert "RELEASE SAVEPOINT v2_rebuild_bucket" in source
    assert "SAVEPOINT v2_rebuild_all" not in source


def test_no_per_city_alpha_tuning_in_production():
    violations: list[str] = []
    for path in PRODUCTION_WEIGHTING_SOURCES:
        source = _source_text_without_line_comments(path)
        for pattern in PER_CITY_ALPHA_PATTERNS:
            if pattern.search(source):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} matches {pattern.pattern}")

    assert violations == []
