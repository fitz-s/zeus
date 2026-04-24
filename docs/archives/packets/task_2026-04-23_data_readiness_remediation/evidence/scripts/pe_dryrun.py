# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: pe_reconstruction_plan.md §2 (dry-run design)
#
# P-E dry-run: read-only. Produces evidence/pe_reconstruction_plan.json — one entry
# per (city, target_date) in the 1561 target set — containing the new authority,
# settlement_value, INV-14 identity fields, and provenance_json keys that the
# execution phase will INSERT.
#
# Sources:
#   - state/zeus-world.db (current 1556 rows; their pm_bin_lo/hi/unit/settlement_source_type/settlement_source)
#   - data/pm_settlement_truth.json (EARLY indices 1513/1517/1520/1530/1532 for 5
#     HIGH-market 2026-04-15 re-inserts DELETEd by P-G)
#   - observations table (source-family-correct obs per P-C routing rules)
#
# Rounding rule: wmo_half_up for WU/NOAA/CWA, oracle_truncate for HKO
#   (matches src/contracts/settlement_semantics.py:69,79 exactly).

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
PM_JSON_PATH = REPO_ROOT / "data" / "pm_settlement_truth.json"
OUTPUT_PATH = (
    REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-23_data_readiness_remediation"
    / "evidence"
    / "pe_reconstruction_plan.json"
)

# 5 HIGH-market 2026-04-15 JSON EARLY indices that P-G DELETEd as LOW-contaminated
# and must be re-inserted with the HIGH-market bin values.
RE_INSERT_JSON_INDICES = [1513, 1517, 1520, 1530, 1532]

DATA_VERSION_BY_SOURCE_TYPE = {
    "WU": "wu_icao_history_v1",
    "NOAA": "ogimet_metar_v1",
    "HKO": "hko_daily_api_v1",
    "CWA": "cwa_no_collector_v0",
}

ROUNDING_RULE_BY_SOURCE_TYPE = {
    "WU": "wmo_half_up",
    "NOAA": "wmo_half_up",
    "HKO": "oracle_truncate",
    "CWA": "wmo_half_up",  # hypothetical; no rows actually use this in practice
}


def wmo_half_up(x: float) -> float:
    return math.floor(x + 0.5)


def oracle_truncate(x: float) -> float:
    return math.floor(x)


def round_for(source_type: str, value: float) -> float:
    if ROUNDING_RULE_BY_SOURCE_TYPE[source_type] == "oracle_truncate":
        return oracle_truncate(value)
    return wmo_half_up(value)


def contains(rounded: float, lo, hi) -> bool:
    """Point/range/shoulder-aware containment. Returns False for both-NULL."""
    if lo is not None and hi is not None:
        return lo <= rounded <= hi
    if lo is None and hi is not None:
        return rounded <= hi
    if hi is None and lo is not None:
        return rounded >= lo
    return False


def canonical_bin_label(lo, hi, unit: str) -> str | None:
    """Per plan §1.5 + critic-opus C1 correction.

    The naive unicode form `≥X°C` / `≤X°C` silently misparses through
    `src/data/market_scanner.py::_parse_temp_range` as a POINT bin
    (regex uses `re.search`, not `re.fullmatch`, so the prefix chars are
    ignored). The text form `X°C or higher` / `X°C or below` round-trips
    correctly through the parser and is the canonical P-E choice.
    """
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{int(lo)}°{unit}"
        return f"{int(lo)}-{int(hi)}°{unit}"
    if lo is None and hi is not None:
        return f"{int(hi)}°{unit} or below"
    return f"{int(lo)}°{unit} or higher"


def pick_source_correct_obs(
    obs_rows: list[dict], source_type: str
) -> dict | None:
    """Return first obs matching source-family per P-C routing, or None."""
    for o in obs_rows:
        src = o["source"]
        if source_type == "WU" and src == "wu_icao_history":
            return o
        if source_type == "NOAA" and src.startswith("ogimet_metar_"):
            return o
        if source_type == "HKO" and src == "hko_daily_api":
            return o
    return None  # CWA or unavailable


def sentinel_to_null(v):
    """Map JSON sentinels -999 / 999 to SQL NULL (per bulk-writer convention)."""
    if v is None:
        return None
    if v in (-999, -999.0):
        return None
    if v in (999, 999.0):
        return None
    return v


def load_candidates() -> list[dict]:
    """Build the 1561 candidate rows: current DB + 5 re-inserts.

    Each candidate has: city, target_date, pm_bin_lo, pm_bin_hi, unit,
    settlement_source_type, settlement_source, source ('current_db' or
    'pm_settlement_truth_early_idx_N'), prior_authority,
    prior_quarantine_reason.
    """
    candidates: list[dict] = []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1556 current rows
    for row in conn.execute(
        """SELECT id, city, target_date, pm_bin_lo, pm_bin_hi, unit,
                  settlement_source_type, settlement_source, authority,
                  json_extract(provenance_json, '$.quarantine_reason') AS q_reason
           FROM settlements
           ORDER BY city, target_date"""
    ):
        candidates.append({
            "city": row["city"],
            "target_date": row["target_date"],
            "pm_bin_lo": row["pm_bin_lo"],
            "pm_bin_hi": row["pm_bin_hi"],
            "unit": row["unit"],
            "settlement_source_type": row["settlement_source_type"],
            "settlement_source": row["settlement_source"],
            "source": "current_db",
            "prior_id": row["id"],
            "prior_authority": row["authority"],
            "prior_quarantine_reason": row["q_reason"],
        })
    conn.close()

    # 5 re-insert rows from JSON EARLY indices
    with open(PM_JSON_PATH) as f:
        pm_json = json.load(f)

    for idx in RE_INSERT_JSON_INDICES:
        e = pm_json[idx]
        city = e["city"]
        target_date = e["date"]
        pm_lo = sentinel_to_null(e.get("pm_bin_lo"))
        pm_hi = sentinel_to_null(e.get("pm_bin_hi"))
        unit = e["unit"]
        # All 5 re-insert candidates are WU-resolved per existing DB pattern for these cities
        candidates.append({
            "city": city,
            "target_date": target_date,
            "pm_bin_lo": pm_lo,
            "pm_bin_hi": pm_hi,
            "unit": unit,
            "settlement_source_type": "WU",
            "settlement_source": e.get("resolution_source", ""),
            "source": f"pm_settlement_truth_early_idx_{idx}",
            "prior_id": None,
            "prior_authority": "deleted_by_p_g_low_market_contamination",
            "prior_quarantine_reason": None,
        })

    return candidates


def load_obs_index() -> dict[tuple[str, str], list[dict]]:
    """Index observations by (city, target_date)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT id, city, target_date, source, high_temp, unit, fetched_at "
        "FROM observations WHERE high_temp IS NOT NULL"
    ):
        idx[(row["city"], row["target_date"])].append({
            "id": row["id"],
            "source": row["source"],
            "high_temp": row["high_temp"],
            "unit": row["unit"],
            "fetched_at": row["fetched_at"],
        })
    conn.close()
    return idx


WHOLE_BUCKET_QUARANTINE_REASONS = {
    # Reasons that apply to EVERY row in a bucket, regardless of per-row obs containment.
    # Shenzhen: the 16 apparent-matches cannot be trusted (station-drift bucket-wide).
    # Per P-C §4.3 + critic-opus P-C post-review caveat + P-F whole-bucket quarantine.
    "pc_audit_shenzhen_drift_nonreproducible",
    # (station_remap_needed_no_cwa_collector is implicitly whole-bucket because NO source-correct
    #  obs exists; the obs-is-None branch handles it. Listed here for completeness / robustness.)
    "pc_audit_station_remap_needed_no_cwa_collector",
    # Similarly AP-4 is implicitly whole-bucket (no source-correct obs by definition).
    "pc_audit_source_role_collapse_no_source_correct_obs_available",
}


def plan_row(candidate: dict, obs_idx, reconstruct_time: str) -> dict:
    st = candidate["settlement_source_type"]
    city = candidate["city"]
    target_date = candidate["target_date"]
    obs_rows = obs_idx.get((city, target_date), [])
    obs = pick_source_correct_obs(obs_rows, st)

    rounding_rule = ROUNDING_RULE_BY_SOURCE_TYPE[st]
    data_version = DATA_VERSION_BY_SOURCE_TYPE[st]

    new_authority = "QUARANTINED"
    new_settlement_value = None
    new_winning_bin = None
    reason = None
    reconstruction_method = None
    rounded = None
    contained = None

    prior_reason = candidate.get("prior_quarantine_reason")
    whole_bucket_carry_forward = prior_reason in WHOLE_BUCKET_QUARANTINE_REASONS

    if obs is None:
        new_authority = "QUARANTINED"
        reason = prior_reason or "pe_no_source_correct_obs"
        reconstruction_method = "quarantine_no_obs"
    elif whole_bucket_carry_forward:
        # Force-carry-forward: obs might pass containment by coincidence, but the
        # station-drift / whole-bucket judgment from P-F is stronger than per-row
        # containment. Preserve the P-F reason without trying to reconstruct.
        new_authority = "QUARANTINED"
        # Still compute rounded for evidence preservation
        if obs["unit"] == candidate["unit"]:
            rounded = round_for(st, float(obs["high_temp"]))
            contained = contains(rounded, candidate["pm_bin_lo"], candidate["pm_bin_hi"])
            new_settlement_value = rounded
        reason = prior_reason
        reconstruction_method = "quarantine_whole_bucket_carry_forward"
    else:
        # Unit mismatch safety (should be zero per P-C scan)
        if obs["unit"] != candidate["unit"]:
            reason = "pe_unit_mismatch_obs_vs_settlement"
            reconstruction_method = "quarantine_unit_mismatch"
        else:
            rounded = round_for(st, float(obs["high_temp"]))
            contained = contains(rounded, candidate["pm_bin_lo"], candidate["pm_bin_hi"])
            if contained:
                new_authority = "VERIFIED"
                new_settlement_value = rounded
                new_winning_bin = canonical_bin_label(
                    candidate["pm_bin_lo"], candidate["pm_bin_hi"], candidate["unit"]
                )
                reconstruction_method = "obs_plus_settlement_semantics"
                reason = None
            else:
                new_authority = "QUARANTINED"
                new_settlement_value = rounded  # preserved as evidence
                reason = prior_reason or "pe_obs_outside_bin"
                reconstruction_method = "quarantine_obs_outside_bin"

    provenance = {
        "writer": "p_e_reconstruction_2026-04-23",
        "writer_script": "docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pe_reconstruct.py",
        "source_family": st,
        "obs_source": obs["source"] if obs else None,
        "obs_id": obs["id"] if obs else None,
        "decision_time_snapshot_id": obs["fetched_at"] if obs else None,
        "rounding_rule": rounding_rule,
        "reconstruction_method": reconstruction_method,
        "prior_authority": candidate["prior_authority"],
        "prior_quarantine_reason": candidate.get("prior_quarantine_reason"),
        "pm_bin_source": candidate["source"],
        "reconstructed_at": reconstruct_time,
        "audit_ref": "pc_agreement_audit_postPG.json + pe_reconstruction_plan.json",
    }
    if reason is not None:
        provenance["quarantine_reason"] = reason

    return {
        "city": city,
        "target_date": target_date,
        "src": candidate["source"],
        "settlement_source_type": st,
        "unit": candidate["unit"],
        "pm_bin_lo": candidate["pm_bin_lo"],
        "pm_bin_hi": candidate["pm_bin_hi"],
        "obs_found": obs is not None,
        "obs_high_temp": obs["high_temp"] if obs else None,
        "obs_source": obs["source"] if obs else None,
        "obs_id": obs["id"] if obs else None,
        "obs_fetched_at": obs["fetched_at"] if obs else None,
        "rounded": rounded,
        "contained": contained,
        "new_authority": new_authority,
        "new_settlement_value": new_settlement_value,
        "new_winning_bin": new_winning_bin,
        "new_temperature_metric": "high",
        "new_physical_quantity": "daily_maximum_air_temperature",
        "new_observation_field": "high_temp",
        "new_data_version": data_version,
        "new_settlement_source": candidate["settlement_source"],
        "new_provenance_json": provenance,
        "quarantine_reason": reason,
    }


def main() -> int:
    reconstruct_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{reconstruct_time}] P-E dry-run starting", file=sys.stderr)

    candidates = load_candidates()
    print(f"candidates loaded: {len(candidates)}", file=sys.stderr)
    if len(candidates) != 1561:
        print(f"FAIL: expected 1561 candidates, got {len(candidates)}", file=sys.stderr)
        return 1

    obs_idx = load_obs_index()
    print(f"obs index built: {sum(len(v) for v in obs_idx.values())} rows across {len(obs_idx)} keys", file=sys.stderr)

    plan = [plan_row(c, obs_idx, reconstruct_time) for c in candidates]

    # Aggregates
    authority_counts = Counter(p["new_authority"] for p in plan)
    source_type_counts = Counter(p["settlement_source_type"] for p in plan)
    reason_counts = Counter(p["quarantine_reason"] for p in plan if p["new_authority"] == "QUARANTINED")
    rounding_counts = Counter(p["new_provenance_json"]["rounding_rule"] for p in plan)

    aggregates = {
        "total": len(plan),
        "authority": dict(authority_counts),
        "source_type": dict(source_type_counts),
        "quarantine_reasons": dict(reason_counts),
        "rounding_rules": dict(rounding_counts),
    }
    print(f"aggregates: {json.dumps(aggregates, indent=2, default=str)}", file=sys.stderr)

    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "metadata": {
                "reconstruct_time": reconstruct_time,
                "total_rows": len(plan),
                "aggregates": aggregates,
            },
            "plan": plan,
        }, f, indent=2, default=str)

    print(f"wrote {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
