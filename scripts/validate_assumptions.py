#!/usr/bin/env python3
"""Validate that assumptions.json matches live code and configuration contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities, day0_n_mc, ensemble_member_count, ensemble_n_mc
from src.contracts import SettlementSemantics
from src.signal.ensemble_signal import DEFAULT_N_MC

ASSUMPTIONS_PATH = PROJECT_ROOT / "state" / "assumptions.json"
MAIN_PATH = PROJECT_ROOT / "src" / "main.py"
MONITOR_PATH = PROJECT_ROOT / "src" / "engine" / "monitor_refresh.py"


def run_validation() -> dict:
    assumptions = json.loads(ASSUMPTIONS_PATH.read_text())
    mismatches: list[str] = []
    checks: list[str] = []

    expected_f = sorted(assumptions["cities"]["fahrenheit"])
    expected_c = sorted(assumptions["cities"]["celsius"])
    actual_f = sorted(c.name for c in cities if c.settlement_unit == "F")
    actual_c = sorted(c.name for c in cities if c.settlement_unit == "C")
    if expected_f != actual_f:
        mismatches.append(f"fahrenheit city partition mismatch: assumptions={expected_f}, config={actual_f}")
    else:
        checks.append("city partition F matches config")
    if expected_c != actual_c:
        mismatches.append(f"celsius city partition mismatch: assumptions={expected_c}, config={actual_c}")
    else:
        checks.append("city partition C matches config")

    for city in cities:
        semantics = SettlementSemantics.for_city(city)
        expected_precision = (
            assumptions["settlement"]["precision_f"]
            if city.settlement_unit == "F"
            else assumptions["settlement"]["precision_c"]
        )
        if semantics.measurement_unit != city.settlement_unit:
            mismatches.append(f"{city.name} settlement unit mismatch: {semantics.measurement_unit} vs {city.settlement_unit}")
        if semantics.precision != expected_precision:
            mismatches.append(f"{city.name} settlement precision mismatch: {semantics.precision} vs {expected_precision}")
        expected_rounding = (
            assumptions["settlement"].get("rounding_rule_overrides", {}).get(city.name)
            or assumptions["settlement"]["rounding_rule"]
        )
        if semantics.rounding_rule != expected_rounding:
            mismatches.append(f"{city.name} rounding mismatch: {semantics.rounding_rule}")
    checks.append("SettlementSemantics.for_city matches config units/precision/rounding")

    expected_members = ensemble_member_count()
    if expected_members != assumptions["signal"]["ens_member_count"]:
        mismatches.append(f"validate_ensemble expected_members={expected_members}, assumptions={assumptions['signal']['ens_member_count']}")
    else:
        checks.append("validate_ensemble member-count contract matches assumptions")

    if DEFAULT_N_MC != assumptions["signal"]["mc_count_entry"]:
        mismatches.append(f"ensemble entry MC mismatch: DEFAULT_N_MC={DEFAULT_N_MC}, assumptions={assumptions['signal']['mc_count_entry']}")
    else:
        checks.append("EnsembleSignal entry MC matches assumptions")

    day0_mc = day0_n_mc()
    if day0_mc != assumptions["signal"]["mc_count_entry"]:
        mismatches.append(f"day0 entry MC mismatch: day0_n_mc()={day0_mc}, assumptions={assumptions['signal']['mc_count_entry']}")
    else:
        checks.append("Day0Signal entry MC matches assumptions")

    monitor_source = MONITOR_PATH.read_text(encoding="utf-8")
    if "ensemble_n_mc()" not in monitor_source or "day0_n_mc()" not in monitor_source:
        mismatches.append("monitor_refresh does not source MC counts from config helpers")
    elif ensemble_n_mc() != assumptions["signal"]["mc_count_entry"] or day0_n_mc() != assumptions["signal"]["mc_count_entry"]:
        mismatches.append(
            "monitor_refresh MC helpers diverge from assumptions: "
            f"ensemble={ensemble_n_mc()}, day0={day0_n_mc()}, assumptions={assumptions['signal']['mc_count_entry']}"
        )
    else:
        checks.append("monitor_refresh MC counts are sourced from config helpers")

    main_source = MAIN_PATH.read_text(encoding="utf-8")
    for required_script in [
        "etl_diurnal_curves.py",
        "etl_hourly_observations.py",
    ]:
        if required_script not in main_source:
            mismatches.append(f"startup ETL missing required script {required_script}")
    checks.append("startup ETL references time-semantic sync scripts")

    return {
        "valid": not mismatches,
        "checks": checks,
        "mismatches": mismatches,
    }


if __name__ == "__main__":
    print(json.dumps(run_validation(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if not run_validation()["mismatches"] else 1)
