# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: pe_reconstruction_plan.md §2.3 T1-T8 (Fitz Constraint: relationship tests BEFORE implementation)
#
# P-E relationship tests — verify that the cross-module invariant holds for every
# (city, target_date) entry in pe_reconstruction_plan.json:
#
#   "For every (city, target_date) with source-correct obs, the reconstructed
#    settlement row is self-consistent: SettlementSemantics(obs.high_temp) ==
#    settlement_value; settlement_value ∈ [pm_bin_lo, pm_bin_hi] iff
#    authority='VERIFIED'; provenance_json.decision_time_snapshot_id ==
#    obs.fetched_at."
#
# These tests read the dry-run plan JSON, not the live DB. Post-execution they
# can be re-run with a plan-equivalent SELECT against the live settlements
# table.

from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = (
    REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-23_data_readiness_remediation"
    / "evidence"
    / "pe_reconstruction_plan.json"
)
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"

# critic-opus R2: toggle source between plan.json (pre-execution) and live DB
# (post-execution validation). Defaults to plan.
TEST_SOURCE = os.environ.get("PE_TEST_SOURCE", "plan")

EXPECTED_TOTAL = 1561
ALLOWED_AUTHORITIES = {"VERIFIED", "QUARANTINED"}
ALLOWED_TEMPERATURE_METRIC = {"high", "low"}
ALLOWED_OBSERVATION_FIELD = {"high_temp", "low_temp"}
ALLOWED_ROUNDING = {"wmo_half_up", "oracle_truncate"}
ALLOWED_DATA_VERSION = {
    "wu_icao_history_v1",
    "ogimet_metar_v1",
    "hko_daily_api_v1",
    "cwa_no_collector_v0",
}
CLOSED_QUARANTINE_REASONS = {
    # P-F carried-forward (6)
    "pc_audit_dst_spring_forward_bin_mismatch",
    "pc_audit_shenzhen_drift_nonreproducible",
    "pc_audit_seoul_station_drift_2026-03_through_2026-04",
    "pc_audit_station_remap_needed_no_cwa_collector",
    "pc_audit_source_role_collapse_no_source_correct_obs_available",
    "pc_audit_1unit_drift",
    # P-E-new (3)
    "pe_obs_outside_bin",
    "pe_no_source_correct_obs",
    "pe_unit_mismatch_obs_vs_settlement",
}

WHOLE_BUCKET_QUARANTINE_REASONS = {
    "pc_audit_shenzhen_drift_nonreproducible",
    "pc_audit_station_remap_needed_no_cwa_collector",
    "pc_audit_source_role_collapse_no_source_correct_obs_available",
}

RE_INSERT_CITIES_APR15 = {"London", "NYC", "Seoul", "Tokyo", "Shanghai"}


def _db_rows_as_plan() -> list[dict]:
    """critic-opus R2: load the live settlements table and reshape each row
    into the plan-equivalent dict so the existing assertions work unchanged.
    Used for post-execution validation via `PE_TEST_SOURCE=db pytest`."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT city, target_date, settlement_source, settlement_source_type,
                  unit, pm_bin_lo, pm_bin_hi, authority, settlement_value,
                  winning_bin, temperature_metric, physical_quantity,
                  observation_field, data_version, provenance_json
           FROM settlements
           ORDER BY city, target_date"""
    ).fetchall()
    conn.close()

    plan = []
    for r in rows:
        prov = json.loads(r["provenance_json"]) if r["provenance_json"] else {}
        obs_found = prov.get("obs_id") is not None
        sv = r["settlement_value"]
        contained = None
        rounded = None
        if obs_found and sv is not None:
            rounded = sv  # P-E stored the rounded value
            lo, hi = r["pm_bin_lo"], r["pm_bin_hi"]
            if lo is not None and hi is not None:
                contained = lo <= rounded <= hi
            elif lo is None and hi is not None:
                contained = rounded <= hi
            elif hi is None and lo is not None:
                contained = rounded >= lo
        plan.append({
            "city": r["city"],
            "target_date": r["target_date"],
            "src": prov.get("pm_bin_source", "current_db"),
            "settlement_source_type": r["settlement_source_type"],
            "unit": r["unit"],
            "pm_bin_lo": r["pm_bin_lo"],
            "pm_bin_hi": r["pm_bin_hi"],
            "obs_found": obs_found,
            "obs_high_temp": sv if obs_found else None,  # post-exec we only have the rounded value; close enough for integer rounding cases, but exact match of T1 uses `rounded`
            "obs_source": prov.get("obs_source"),
            "obs_id": prov.get("obs_id"),
            "obs_fetched_at": prov.get("decision_time_snapshot_id"),
            "rounded": rounded,
            "contained": contained,
            "new_authority": r["authority"],
            "new_settlement_value": sv,
            "new_winning_bin": r["winning_bin"],
            "new_temperature_metric": r["temperature_metric"],
            "new_physical_quantity": r["physical_quantity"],
            "new_observation_field": r["observation_field"],
            "new_data_version": r["data_version"],
            "new_settlement_source": r["settlement_source"],
            "new_provenance_json": prov,
            "quarantine_reason": prov.get("quarantine_reason"),
        })
    return plan


@pytest.fixture(scope="module")
def plan() -> list[dict]:
    if TEST_SOURCE == "db":
        return _db_rows_as_plan()
    with open(PLAN_PATH) as f:
        doc = json.load(f)
    return doc["plan"]


def wmo_half_up(x: float) -> float:
    return math.floor(x + 0.5)


def oracle_truncate(x: float) -> float:
    return math.floor(x)


def _verified_rows(plan: list[dict]) -> list[dict]:
    return [p for p in plan if p["new_authority"] == "VERIFIED"]


def _quarantined_rows(plan: list[dict]) -> list[dict]:
    return [p for p in plan if p["new_authority"] == "QUARANTINED"]


def test_T1_self_consistency_verified(plan):
    """VERIFIED rows: round(obs) ∈ [lo,hi] AND settlement_value == round(obs)
    AND decision_time_snapshot_id == obs.fetched_at.

    In DB mode we only have the rounded value (obs.high_temp isn't stored on
    settlements), so the round-equivalence check reduces to "settlement_value
    in [lo,hi]".
    """
    verified = _verified_rows(plan)
    assert len(verified) > 0, "expected some VERIFIED rows"
    for p in verified:
        prov = p["new_provenance_json"]
        assert p["obs_found"], f"VERIFIED row without obs_found: {p['city']}/{p['target_date']}"
        assert p["new_settlement_value"] is not None
        if TEST_SOURCE == "plan":
            # Full round-trip check using obs.high_temp from plan.json
            assert p["obs_high_temp"] is not None
            if prov["rounding_rule"] == "wmo_half_up":
                expected = wmo_half_up(float(p["obs_high_temp"]))
            else:
                expected = oracle_truncate(float(p["obs_high_temp"]))
            assert p["rounded"] == expected, (
                f"rounding mismatch for {p['city']}/{p['target_date']}: "
                f"expected {expected} got {p['rounded']}"
            )
            assert p["new_settlement_value"] == expected
            assert p["contained"] is True
        # Containment boundary conditions (works in both modes)
        lo, hi = p["pm_bin_lo"], p["pm_bin_hi"]
        sv = p["new_settlement_value"]
        if lo is not None and hi is not None:
            assert lo <= sv <= hi
        elif lo is None:
            assert sv <= hi
        elif hi is None:
            assert sv >= lo
        # decision_time_snapshot_id matches obs.fetched_at
        assert prov["decision_time_snapshot_id"] == p["obs_fetched_at"]
        assert prov["reconstruction_method"] == "obs_plus_settlement_semantics"


def test_T2_self_consistency_quarantined_obs_outside_bin(plan):
    """Quarantined with `pe_obs_outside_bin`: round(obs) NOT in [lo,hi] AND
    settlement_value preserved as evidence AND authority=QUARANTINED."""
    outside = [p for p in plan if p.get("quarantine_reason") == "pe_obs_outside_bin"]
    for p in outside:
        assert p["new_authority"] == "QUARANTINED"
        assert p["obs_found"], f"pe_obs_outside_bin without obs: {p['city']}/{p['target_date']}"
        assert p["contained"] is False
        # settlement_value preserved as evidence
        assert p["new_settlement_value"] is not None


def test_T3_self_consistency_quarantined_no_obs(plan):
    """Quarantined with pe_no_source_correct_obs OR AP-4 OR CWA reasons:
    obs_id IS None AND obs_high_temp IS None AND decision_time_snapshot_id IS None."""
    no_obs_reasons = {
        "pe_no_source_correct_obs",
        "pc_audit_source_role_collapse_no_source_correct_obs_available",
        "pc_audit_station_remap_needed_no_cwa_collector",
    }
    no_obs_rows = [p for p in plan if p.get("quarantine_reason") in no_obs_reasons]
    for p in no_obs_rows:
        assert p["new_authority"] == "QUARANTINED"
        assert p["obs_found"] is False, (
            f"row {p['city']}/{p['target_date']} claims no_obs reason but obs_found=True"
        )
        assert p["obs_id"] is None
        assert p["obs_high_temp"] is None
        assert p["new_provenance_json"]["decision_time_snapshot_id"] is None
        assert p["new_settlement_value"] is None


def test_T4_inv14_completeness(plan):
    """Every plan row has non-null temperature_metric/physical_quantity/observation_field/data_version."""
    for p in plan:
        assert p["new_temperature_metric"] in ALLOWED_TEMPERATURE_METRIC, p
        assert p["new_physical_quantity"], p
        assert p["new_observation_field"] in ALLOWED_OBSERVATION_FIELD, p
        assert p["new_data_version"] in ALLOWED_DATA_VERSION, p


def test_T5_hko_oracle_truncate(plan):
    """Every HKO-source row has rounding_rule='oracle_truncate' and
    settlement_value == floor(obs.high_temp) when obs exists."""
    hko = [p for p in plan if p["settlement_source_type"] == "HKO"]
    assert len(hko) == 29, f"expected 29 HKO rows, got {len(hko)}"
    for p in hko:
        assert p["new_provenance_json"]["rounding_rule"] == "oracle_truncate"
        if p["obs_found"]:
            assert p["rounded"] == oracle_truncate(float(p["obs_high_temp"]))


def test_T6_source_family_routing_fail_closed(plan):
    """No cross-family obs substitution."""
    for p in plan:
        if not p["obs_found"]:
            continue
        st = p["settlement_source_type"]
        src = p["obs_source"]
        if st == "WU":
            assert src == "wu_icao_history", f"{p['city']}/{p['target_date']} WU-labeled got {src}"
        elif st == "NOAA":
            assert src.startswith("ogimet_metar_"), (
                f"{p['city']}/{p['target_date']} NOAA-labeled got {src}"
            )
        elif st == "HKO":
            assert src == "hko_daily_api", f"{p['city']}/{p['target_date']} HKO-labeled got {src}"
        elif st == "CWA":
            # CWA has no accepted proxy; obs_found MUST be False
            pytest.fail(f"{p['city']}/{p['target_date']} CWA-labeled should not have obs")


def test_T7_reinsert_5_high_market_apr15(plan):
    """The plan includes 5 (city, '2026-04-15') entries for London/NYC/Seoul/Tokyo/Shanghai
    with HIGH-market bins from JSON EARLY indices."""
    apr15 = [p for p in plan if p["target_date"] == "2026-04-15" and p["city"] in RE_INSERT_CITIES_APR15]
    cities_found = {p["city"] for p in apr15}
    assert cities_found == RE_INSERT_CITIES_APR15, f"missing re-insert cities: {RE_INSERT_CITIES_APR15 - cities_found}"
    # HIGH-market bin values (per Gamma verification)
    expected_bins = {
        "London": (17.0, 17.0),
        "NYC": (86.0, 87.0),
        "Seoul": (21.0, None),  # high shoulder (999 → NULL)
        "Tokyo": (22.0, 22.0),
        "Shanghai": (18.0, 18.0),
    }
    for p in apr15:
        lo, hi = expected_bins[p["city"]]
        assert p["pm_bin_lo"] == lo, f"{p['city']} 2026-04-15 pm_bin_lo {p['pm_bin_lo']} != expected {lo}"
        assert p["pm_bin_hi"] == hi, f"{p['city']} 2026-04-15 pm_bin_hi {p['pm_bin_hi']} != expected {hi}"
        assert p["src"].startswith("pm_settlement_truth_early_idx_"), p["src"]


def test_T8_total_count(plan):
    """Plan partitions to exactly 1561 (city, target_date) pairs."""
    assert len(plan) == EXPECTED_TOTAL, f"plan has {len(plan)} entries, expected {EXPECTED_TOTAL}"
    keys = {(p["city"], p["target_date"]) for p in plan}
    assert len(keys) == EXPECTED_TOTAL, "duplicate (city, target_date) keys in plan"


def test_T9_authority_enum(plan):
    """Authority is always one of the allowed values."""
    for p in plan:
        assert p["new_authority"] in ALLOWED_AUTHORITIES, p


def test_T10_quarantine_reason_enum(plan):
    """All quarantine reasons are from the closed set."""
    for p in plan:
        if p["new_authority"] == "QUARANTINED":
            r = p.get("quarantine_reason")
            assert r in CLOSED_QUARANTINE_REASONS, (
                f"unknown quarantine_reason: {r} on {p['city']}/{p['target_date']}"
            )


def test_T11_whole_bucket_quarantine_no_verified_leak(plan):
    """For rows whose prior_quarantine_reason is a whole-bucket reason, NONE can
    end up VERIFIED even if containment passes by coincidence. Critic-opus P-C
    caveat for Shenzhen."""
    for p in plan:
        prior = p["new_provenance_json"]["prior_quarantine_reason"]
        if prior in WHOLE_BUCKET_QUARANTINE_REASONS:
            assert p["new_authority"] == "QUARANTINED", (
                f"{p['city']}/{p['target_date']} leaked to VERIFIED despite whole-bucket reason {prior}"
            )


def test_T12_provenance_minimum_fields(plan):
    """Every row's provenance_json has writer + reconstruction_method + source_family
    + pm_bin_source + reconstructed_at."""
    required = {"writer", "reconstruction_method", "source_family", "pm_bin_source", "reconstructed_at"}
    for p in plan:
        keys = set(p["new_provenance_json"].keys())
        missing = required - keys
        assert not missing, f"{p['city']}/{p['target_date']} provenance missing {missing}"
        assert p["new_provenance_json"]["writer"] == "p_e_reconstruction_2026-04-23"


def test_T13_verified_rows_have_winning_bin_label(plan):
    """Every VERIFIED row has a non-null winning_bin (canonical label)."""
    for p in _verified_rows(plan):
        assert p["new_winning_bin"], f"VERIFIED row without winning_bin: {p['city']}/{p['target_date']}"


def test_T14_aggregate_arithmetic(plan):
    """Row-level aggregates match plan.json metadata."""
    with open(PLAN_PATH) as f:
        doc = json.load(f)
    agg = doc["metadata"]["aggregates"]
    from collections import Counter
    actual_authority = dict(Counter(p["new_authority"] for p in plan))
    assert agg["authority"] == actual_authority
    assert agg["total"] == len(plan)
    assert sum(agg["authority"].values()) == EXPECTED_TOTAL
