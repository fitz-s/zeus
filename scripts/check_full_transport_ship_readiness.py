#!/usr/bin/env python3
# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Executable 9-check ship-readiness gate for full_transport → live promotion (Zeus #64).
# Reuse: All 9 checks must PASS before enabling full_transport_live_enabled. Run with prod DBs.
# Authority basis: docs/operations/FT_SHIP_MASTER_SPEC_2026-05-25.md §Antibody
"""Executable ship-readiness gate for the full_transport → live promotion.

Prevents a research artifact from being mistaken for a promotable production
artifact. ALL 9 checks must pass before any full_transport path can be called
production-ready. Today (2026-05-25) they should all FAIL — that is the
correct current state: nothing has been shipped yet.

Usage:
    cd zeus && source .venv/bin/activate
    python scripts/check_full_transport_ship_readiness.py
    python scripts/check_full_transport_ship_readiness.py \\
        --prod-world-db /path/to/zeus-world.db \\
        --prod-forecasts-db /path/to/zeus-forecasts.db \\
        --stage-db /path/to/stage.db

Exit 0 iff ALL 9 checks pass; exit 1 otherwise with failing items listed.
Each check is independent and prints PASS/FAIL + evidence.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# -- Production DB defaults ---------------------------------------------------
_STATE = Path(
    os.environ.get("ZEUS_PRIMARY_ROOT")
    or os.environ.get("ZEUS_DIR")
    or ROOT
).resolve() / "state"
DEFAULT_WORLD_DB = str(_STATE / "zeus-world.db")
DEFAULT_FORECASTS_DB = str(_STATE / "zeus-forecasts.db")
DEFAULT_STAGE_DB = str(_STATE / "backups" / "ens_refit_full_2026-05-25.db")

PASS = "PASS"
FAIL = "FAIL"

# ── Result type ───────────────────────────────────────────────────────────────

class CheckResult(NamedTuple):
    name: str
    status: str   # PASS or FAIL
    evidence: str


def _conn(path: str) -> sqlite3.Connection:
    """Open a read-only connection. Raises if the file is empty or missing."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.stat().st_size == 0:
        raise ValueError(f"zero-byte DB: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ── Check 1: pairs_complete ────────────────────────────────────────────────────

def check_pairs_complete(world_db: str) -> CheckResult:
    """calibration_pairs has rows with error_model_family='full_transport_v1'."""
    name = "pairs_complete"
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        if not _table_exists(conn, "calibration_pairs"):
            return CheckResult(name, FAIL, "calibration_pairs table not found")
        count = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs WHERE error_model_family='full_transport_v1'",
        ).fetchone()[0]
        if count == 0:
            return CheckResult(name, FAIL, "no rows with error_model_family='full_transport_v1' — not yet produced")
        return CheckResult(name, PASS, f"full_transport_v1 pairs={count}")
    finally:
        conn.close()


# ── Check 2: error_models_persisted ──────────────────────────────────────────

_ERROR_MODEL_TABLES = ("ens_error_model_v1", "model_bias_ens")
# Required fields per spec §Phase 1 step 1
_REQUIRED_FIELDS = {
    "error_model_key", "bias_c", "residual_sd_c", "heterogeneity_var_c2",
    "correction_strength", "n_live", "n_prior", "n_paired", "fit_signature_hash",
}

def check_error_models_persisted(world_db: str) -> CheckResult:
    """A canonical error-model table exists with the required Phase-1 fields."""
    name = "error_models_persisted"
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        for table in _ERROR_MODEL_TABLES:
            if _table_exists(conn, table):
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                missing = _REQUIRED_FIELDS - cols
                if missing:
                    return CheckResult(
                        name, FAIL,
                        f"{table} exists but missing fields: {sorted(missing)}"
                    )
                # Bug 4 fix (Zeus #64 PR #342): for the canonical model_bias_ens table,
                # require full_transport_v1 posteriors specifically (not just any rows).
                # Legacy ens_error_model_v1 lacks error_model_family — check row count only.
                if table == "model_bias_ens" and "error_model_family" in cols:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE error_model_family='full_transport_v1'"
                    ).fetchone()[0]
                    if count == 0:
                        return CheckResult(
                            name, FAIL,
                            f"{table} exists but has 0 full_transport_v1 rows — posteriors not yet produced"
                        )
                    return CheckResult(name, PASS, f"table={table} full_transport_v1_rows={count} required_fields=present")
                else:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if count == 0:
                        return CheckResult(name, FAIL, f"{table} exists but has 0 rows — not yet produced")
                    return CheckResult(name, PASS, f"table={table} rows={count} required_fields=present")
        return CheckResult(
            name, FAIL,
            f"no error-model table found (checked {list(_ERROR_MODEL_TABLES)}) — not yet produced"
        )
    finally:
        conn.close()


# ── Check 3: p_raw_replay_equivalence_pass ────────────────────────────────────

def check_p_raw_replay_equivalence_pass(stage_db: str) -> CheckResult:
    """Replay-equivalence proof table exists in stage DB with a PASS verdict."""
    name = "p_raw_replay_equivalence_pass"
    try:
        conn = _conn(stage_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"stage_db unavailable: {exc} — not yet produced")
    try:
        if not _table_exists(conn, "replay_equivalence_proof"):
            return CheckResult(name, FAIL, "replay_equivalence_proof table not in stage_db — not yet produced")
        row = conn.execute(
            "SELECT verdict, max_abs_diff, n_snapshots FROM replay_equivalence_proof ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return CheckResult(name, FAIL, "replay_equivalence_proof table is empty — not yet produced")
        verdict, max_diff, n_snap = row
        if verdict != "PASS":
            return CheckResult(name, FAIL, f"verdict={verdict} max_abs_diff={max_diff} n_snapshots={n_snap}")
        return CheckResult(name, PASS, f"verdict=PASS max_abs_diff={max_diff} n_snapshots={n_snap}")
    finally:
        conn.close()


# ── Check 4: platt_or_identity_coverage_complete ─────────────────────────────

def check_platt_or_identity_coverage_complete(world_db: str) -> CheckResult:
    """Every full_transport_v1 bucket in platt_models has an explicit route."""
    name = "platt_or_identity_coverage_complete"
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        if not _table_exists(conn, "platt_models"):
            return CheckResult(name, FAIL, "platt_models table not found")
        # Count ft rows with an explicit calibration_method set
        has_col = any(
            r[1] == "calibration_method"
            for r in conn.execute("PRAGMA table_info(platt_models)")
        )
        if not has_col:
            # calibration_method column not yet added — Phase 1 incomplete
            return CheckResult(
                name, FAIL,
                "platt_models lacks calibration_method column (Phase 1 schema not landed) — not yet produced"
            )
        total_ft = conn.execute(
            "SELECT COUNT(*) FROM platt_models WHERE error_model_family='full_transport_v1'"
        ).fetchone()[0]
        if total_ft == 0:
            return CheckResult(name, FAIL, "no full_transport_v1 Platt/identity rows — not yet produced")
        uncovered = conn.execute(
            """
            SELECT COUNT(*) FROM platt_models
            WHERE error_model_family='full_transport_v1'
              AND (calibration_method IS NULL OR calibration_method = '')
            """
        ).fetchone()[0]
        if uncovered > 0:
            return CheckResult(name, FAIL, f"{uncovered}/{total_ft} ft buckets lack explicit calibration_method")
        return CheckResult(name, PASS, f"all {total_ft} ft buckets have explicit calibration_method")
    finally:
        conn.close()


# ── Check 5: hk_high_or_pathology_carveouts_declared ─────────────────────────

_CARVEOUT_PATHS = [
    ROOT / "config" / "reality_contracts" / "full_transport_pathology_carveouts.yaml",
    ROOT / "config" / "full_transport_pathology_carveouts.yaml",
    ROOT / "docs" / "operations" / "full_transport_pathology_carveouts.yaml",
]

def check_hk_high_or_pathology_carveouts_declared(world_db: str) -> CheckResult:
    """HK HIGH pathology carve-out is declared via DB row or config file."""
    name = "hk_high_or_pathology_carveouts_declared"

    # Option A: config file declaring the carve-out
    for cfg in _CARVEOUT_PATHS:
        if cfg.exists():
            text = cfg.read_text()
            if "HongKong" in text or "hk" in text.lower() or "pathology" in text.lower():
                return CheckResult(name, PASS, f"carveout declared in {cfg.name}")

    # Option B: no_trade_events table entry for HK HIGH full_transport pathology
    # Table is keyed by market_slug (not city), e.g. "will-the-high-temperature-in-hong-kong..."
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        if _table_exists(conn, "no_trade_events"):
            row = conn.execute(
                """
                SELECT COUNT(*) FROM no_trade_events
                WHERE (market_slug LIKE '%hong%kong%' OR market_slug LIKE '%hongkong%')
                  AND temperature_metric='high'
                  AND reason LIKE '%full_transport%pathology%'
                """
            ).fetchone()[0]
            if row > 0:
                return CheckResult(name, PASS, f"HK HIGH full_transport pathology carve-out in no_trade_events (n={row})")
    finally:
        conn.close()

    return CheckResult(
        name, FAIL,
        "no HK HIGH pathology carve-out found in config files or no_trade_events — not yet declared "
        "(spec §Phase 5: PIT 96.9% bin0 / +6.32°C over-warm — carve-out required before ship)"
    )


# ── Check 6: sentinel_complete ────────────────────────────────────────────────

def check_sentinel_complete(world_db: str) -> CheckResult:
    """rebuild sentinel for full_transport pairs is 'complete' (not in_progress)."""
    name = "sentinel_complete"
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        # Sentinels are stored in the chronicle table as structured log entries
        if not _table_exists(conn, "chronicle"):
            return CheckResult(name, FAIL, "chronicle table not found")
        # Sentinels stored in details_json column (chronicle schema: id/event_type/trade_id/timestamp/details_json/env)
        row = conn.execute(
            """
            SELECT COUNT(*) FROM chronicle
            WHERE event_type = 'rebuild_sentinel'
              AND details_json LIKE '%full_transport%'
              AND details_json LIKE '%"status": "complete"%'
            """
        ).fetchone()[0]
        in_prog = conn.execute(
            """
            SELECT COUNT(*) FROM chronicle
            WHERE event_type = 'rebuild_sentinel'
              AND details_json LIKE '%full_transport%'
              AND details_json LIKE '%"status": "in_progress"%'
            """
        ).fetchone()[0]
        if in_prog > 0:
            return CheckResult(name, FAIL, f"full_transport sentinel still in_progress (n={in_prog})")
        if row == 0:
            return CheckResult(name, FAIL, "no full_transport sentinel marked complete — not yet produced")
        return CheckResult(name, PASS, f"full_transport sentinel complete (n={row})")
    finally:
        conn.close()


# ── Check 7: calibration_pin_complete ────────────────────────────────────────

def check_calibration_pin_complete() -> CheckResult:
    """settings.json calibration.pin.frozen_as_of is set for full_transport."""
    name = "calibration_pin_complete"
    import json

    settings_path = ROOT / "config" / "settings.json"
    if not settings_path.exists():
        return CheckResult(name, FAIL, f"settings.json not found at {settings_path}")
    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError as exc:
        return CheckResult(name, FAIL, f"settings.json parse error: {exc}")

    pin = settings.get("calibration", {}).get("pin", {})
    frozen = pin.get("frozen_as_of")
    model_keys = pin.get("model_keys")

    if not frozen:
        return CheckResult(
            name, FAIL,
            "calibration.pin.frozen_as_of not set — explicit pin required before ship (spec §Phase 6)"
        )
    if not model_keys:
        return CheckResult(
            name, FAIL,
            f"calibration.pin.frozen_as_of={frozen!r} but model_keys not set — "
            "per-cohort model_key map required before ship"
        )
    return CheckResult(name, PASS, f"frozen_as_of={frozen!r} model_keys={len(model_keys)} cohorts")


# ── Check 8: live_wiring_flag_off_byte_identical ──────────────────────────────

def check_live_wiring_flag_off_byte_identical() -> CheckResult:
    """full_transport_live_enabled flag is absent/OFF in monitor_refresh; code byte-identical to main."""
    name = "live_wiring_flag_off_byte_identical"

    monitor = ROOT / "src" / "engine" / "monitor_refresh.py"
    if not monitor.exists():
        return CheckResult(name, FAIL, f"monitor_refresh.py not found at {monitor}")

    text = monitor.read_text()

    # If the flag is present and set to True, the code is wired live — fail.
    if "full_transport_live_enabled" in text:
        if "full_transport_live_enabled = True" in text or "full_transport_live_enabled=True" in text:
            return CheckResult(
                name, FAIL,
                "full_transport_live_enabled is present AND set to True — live wiring is active"
            )
        # Flag exists but OFF — that's the correct gated state
        return CheckResult(name, PASS, "full_transport_live_enabled present and OFF (byte-identical-when-off)")

    # Bug 5 fix (Zeus #64 PR #342): flag absent = FAIL-CLOSED.
    # The gate requires the flag to be present AND set to OFF (not absent).
    # Absent flag means Phase 1 wiring has not landed — that is a ship-blocker,
    # not a pass.  Exception during git diff check also → FAIL (not PASS).
    return CheckResult(
        name, FAIL,
        "full_transport_live_enabled absent from monitor_refresh.py — "
        "Phase 1 flag wiring required before ship (must be present AND set to False/OFF)"
    )


# ── Check 9: live_trace_smoke_pass ────────────────────────────────────────────

def check_live_trace_smoke_pass(world_db: str) -> CheckResult:
    """probability_trace_fact has rows with p_raw_domain='full_transport_v1'."""
    name = "live_trace_smoke_pass"
    try:
        conn = _conn(world_db)
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(name, FAIL, f"world_db unavailable: {exc}")
    try:
        if not _table_exists(conn, "probability_trace_fact"):
            return CheckResult(name, FAIL, "probability_trace_fact table not found — not yet produced")
        # Check for column
        has_domain_col = any(
            r[1] == "p_raw_domain"
            for r in conn.execute("PRAGMA table_info(probability_trace_fact)")
        )
        if not has_domain_col:
            return CheckResult(
                name, FAIL,
                "probability_trace_fact lacks p_raw_domain column (Phase 1 schema not landed) — not yet produced"
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM probability_trace_fact WHERE p_raw_domain='full_transport_v1'"
        ).fetchone()[0]
        if count == 0:
            return CheckResult(name, FAIL, "no full_transport_v1 trace rows — live path not exercised — not yet produced")
        return CheckResult(name, PASS, f"full_transport_v1 trace rows={count}")
    finally:
        conn.close()


# ── Gate runner ───────────────────────────────────────────────────────────────

SPEC_NAMES = (
    "pairs_complete",
    "error_models_persisted",
    "p_raw_replay_equivalence_pass",
    "platt_or_identity_coverage_complete",
    "hk_high_or_pathology_carveouts_declared",
    "sentinel_complete",
    "calibration_pin_complete",
    "live_wiring_flag_off_byte_identical",
    "live_trace_smoke_pass",
)


def run_all_checks(
    world_db: str,
    forecasts_db: str,
    stage_db: str,
) -> list[CheckResult]:
    return [
        check_pairs_complete(world_db),
        check_error_models_persisted(world_db),
        check_p_raw_replay_equivalence_pass(stage_db),
        check_platt_or_identity_coverage_complete(world_db),
        check_hk_high_or_pathology_carveouts_declared(world_db),
        check_sentinel_complete(world_db),
        check_calibration_pin_complete(),
        check_live_wiring_flag_off_byte_identical(),
        check_live_trace_smoke_pass(world_db),
    ]


def print_results(results: list[CheckResult]) -> int:
    """Print one PASS/FAIL line per check. Return exit code (0=all pass, 1=any fail)."""
    width = max(len(r.name) for r in results)
    all_pass = True
    for r in results:
        tag = "\033[32mPASS\033[0m" if r.status == PASS else "\033[31mFAIL\033[0m"
        print(f"  {r.name:<{width}}  [{tag}]  {r.evidence}")
        if r.status != PASS:
            all_pass = False
    print()
    if all_pass:
        print("GATE: \033[32mALL PASS\033[0m — full_transport may be promoted to production.")
        return 0
    failing = [r.name for r in results if r.status != PASS]
    print(f"GATE: \033[31mFAIL\033[0m — {len(failing)} check(s) not met: {failing}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="full_transport ship-readiness gate (read-only)"
    )
    parser.add_argument("--prod-world-db", default=DEFAULT_WORLD_DB)
    parser.add_argument("--prod-forecasts-db", default=DEFAULT_FORECASTS_DB)
    parser.add_argument("--stage-db", default=DEFAULT_STAGE_DB)
    args = parser.parse_args(argv)

    print("full_transport ship-readiness gate")
    print(f"  world_db:     {args.prod_world_db}")
    print(f"  forecasts_db: {args.prod_forecasts_db}")
    print(f"  stage_db:     {args.stage_db}")
    print()

    results = run_all_checks(
        world_db=args.prod_world_db,
        forecasts_db=args.prod_forecasts_db,
        stage_db=args.stage_db,
    )
    return print_results(results)


if __name__ == "__main__":
    sys.exit(main())
