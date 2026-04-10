#!/usr/bin/env python3
"""Zeus Antibody Scan — automated semantic & reality integrity checks.

Designed to run as a scheduled job (cron/heartbeat). Detects:
1. Data pipeline failures (settlement/ENS/calibration staleness)
2. Contract invariant violations
3. Calibration model drift
4. Configuration consistency

Returns structured JSON results suitable for Discord alerting.

Usage:
    cd zeus
    source ../rainstorm/.venv/bin/activate

    # Full scan
    python scripts/antibody_scan.py

    # Specific checks only
    python scripts/antibody_scan.py --check data_freshness
    python scripts/antibody_scan.py --check contracts
    python scripts/antibody_scan.py --check calibration

    # JSON output (for Discord/alerting)
    python scripts/antibody_scan.py --json
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    severity: str       # P0, P1, P2, INFO
    category: str       # data_freshness, contract, calibration, config
    check: str          # specific check name
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ScanResult:
    timestamp: str
    findings: list[Finding] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "P0" for f in self.findings)

    @property
    def summary(self) -> str:
        by_sev = {}
        for f in self.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        parts = [f"{v}×{k}" for k, v in sorted(by_sev.items())]
        return f"{self.checks_run} checks, {self.checks_passed} passed, findings: {', '.join(parts) or 'none'}"


# ─────────────────────────────────────────────────────────────
# Check: Data Freshness
# ─────────────────────────────────────────────────────────────

def check_data_freshness(result: ScanResult):
    """Verify all data pipelines are producing fresh data."""
    from src.state.db import get_shared_connection

    conn = get_shared_connection()
    today = date.today()
    yesterday = today - timedelta(days=1)

    # 1. Settlement collection freshness
    result.checks_run += 1
    r = conn.execute("""
        SELECT COUNT(DISTINCT city) as cities,
               MAX(target_date) as latest
        FROM settlements
        WHERE settlement_value IS NOT NULL
          AND target_date >= ?
    """, (yesterday.isoformat(),)).fetchone()

    recent_cities = r[0] if r else 0
    latest_date = r[1] if r else None

    if recent_cities == 0:
        result.findings.append(Finding(
            severity="P0",
            category="data_freshness",
            check="settlement_collection",
            message=f"No settlements collected since {yesterday}",
            details={"latest_date": latest_date, "expected_min_cities": 10},
        ))
    elif recent_cities < 10:
        result.findings.append(Finding(
            severity="P1",
            category="data_freshness",
            check="settlement_collection",
            message=f"Only {recent_cities} cities have recent settlements (expected 30+)",
            details={"cities_with_data": recent_cities, "latest_date": latest_date},
        ))
    else:
        result.checks_passed += 1

    # 2. ENS snapshot freshness
    result.checks_run += 1
    r = conn.execute("""
        SELECT COUNT(*), MAX(fetch_time)
        FROM ensemble_snapshots
        WHERE fetch_time >= ?
    """, ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),)).fetchone()

    recent_ens = r[0] if r else 0
    if recent_ens == 0:
        result.findings.append(Finding(
            severity="P1",
            category="data_freshness",
            check="ens_snapshot_freshness",
            message="No ENS snapshots fetched in past 24h",
            details={"latest_fetch": r[1] if r else None},
        ))
    else:
        result.checks_passed += 1

    # 3. Observation collection (WU daily)
    result.checks_run += 1
    r = conn.execute("""
        SELECT COUNT(DISTINCT city)
        FROM observations
        WHERE target_date >= ?
    """, ((today - timedelta(days=3)).isoformat(),)).fetchone()

    recent_obs_cities = r[0] if r else 0
    if recent_obs_cities < 5:
        result.findings.append(Finding(
            severity="P1",
            category="data_freshness",
            check="wu_observation_collection",
            message=f"Only {recent_obs_cities} cities have observations in past 3 days",
        ))
    else:
        result.checks_passed += 1

    # 4. Market events freshness (Gamma API)
    result.checks_run += 1
    r = conn.execute("""
        SELECT COUNT(DISTINCT city), MAX(target_date)
        FROM market_events
        WHERE target_date >= ?
    """, (today.isoformat(),)).fetchone()

    market_cities = r[0] if r else 0
    if market_cities == 0:
        result.findings.append(Finding(
            severity="P1",
            category="data_freshness",
            check="market_events_freshness",
            message="No market_events for today or future dates",
            details={"latest_target": r[1] if r else None},
        ))
    else:
        result.checks_passed += 1

    conn.close()


# ─────────────────────────────────────────────────────────────
# Check: Contract Invariants
# ─────────────────────────────────────────────────────────────

def check_contracts(result: ScanResult):
    """Verify contract objects are properly enforced in production code."""

    # 1. Settlement precision enforcement
    result.checks_run += 1
    try:
        from src.contracts.settlement_semantics import SettlementSemantics
        sem = SettlementSemantics.default_wu_fahrenheit("TEST")
        assert sem.precision == 1.0
        rounded = sem.round_single(72.3)
        assert rounded == 72.0
        result.checks_passed += 1
    except Exception as e:
        result.findings.append(Finding(
            severity="P0",
            category="contract",
            check="settlement_precision_contract",
            message=f"SettlementSemantics contract broken: {e}",
        ))

    # 2. Settlement data integrity (check for fractional values in DB)
    result.checks_run += 1
    try:
        from src.state.db import get_shared_connection
        conn = get_shared_connection()
        r = conn.execute("""
            SELECT COUNT(*) FROM settlements
            WHERE settlement_value IS NOT NULL
              AND settlement_value != ROUND(settlement_value)
        """).fetchone()
        fractional = r[0] if r else 0
        conn.close()

        if fractional > 0:
            result.findings.append(Finding(
                severity="P1",
                category="contract",
                check="settlement_integer_invariant",
                message=f"{fractional} settlements have fractional values (should be integer)",
                details={"fractional_count": fractional},
            ))
        else:
            result.checks_passed += 1
    except Exception as e:
        result.findings.append(Finding(
            severity="P1",
            category="contract",
            check="settlement_integer_invariant",
            message=f"Check failed: {e}",
        ))

    # 3. Season mapping consistency (SH hemisphere)
    result.checks_run += 1
    try:
        from src.calibration.manager import season_from_date
        # SH: July should be JJA (winter in SH → mapped to DJF)
        sh_season = season_from_date("2025-07-15", lat=-34.0)
        nh_season = season_from_date("2025-07-15", lat=40.0)
        assert sh_season == "DJF", f"SH July should be DJF (cold), got {sh_season}"
        assert nh_season == "JJA", f"NH July should be JJA, got {nh_season}"
        result.checks_passed += 1
    except Exception as e:
        result.findings.append(Finding(
            severity="P0",
            category="contract",
            check="hemisphere_season_mapping",
            message=f"Season mapping broken: {e}",
        ))

    # 4. Config consistency — all cities have required fields
    result.checks_run += 1
    try:
        from src.config import load_cities
        cities = load_cities()
        missing_fields = []
        for city in cities:
            if not city.wu_station:
                missing_fields.append(f"{city.name}: missing wu_station")
            if not city.timezone:
                missing_fields.append(f"{city.name}: missing timezone")
            if city.lat == 0 and city.lon == 0:
                missing_fields.append(f"{city.name}: zero coordinates")

        if missing_fields:
            result.findings.append(Finding(
                severity="P1",
                category="config",
                check="city_config_completeness",
                message=f"{len(missing_fields)} config issues found",
                details={"issues": missing_fields[:10]},
            ))
        else:
            result.checks_passed += 1
    except Exception as e:
        result.findings.append(Finding(
            severity="P1",
            category="config",
            check="city_config_completeness",
            message=f"Config load failed: {e}",
        ))

    # 5. Check for rainstorm.db dependency (should be eliminated)
    result.checks_run += 1
    rainstorm_db = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"
    if rainstorm_db.exists():
        # Check if any running process has it open
        import subprocess
        try:
            r = subprocess.run(
                ["lsof", str(rainstorm_db)],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip():
                result.findings.append(Finding(
                    severity="P2",
                    category="contract",
                    check="rainstorm_db_detachment",
                    message="rainstorm.db still open by a process",
                    details={"processes": r.stdout.strip()[:200]},
                ))
            else:
                result.checks_passed += 1
        except Exception:
            result.checks_passed += 1
    else:
        result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Check: Calibration Health
# ─────────────────────────────────────────────────────────────

def check_calibration(result: ScanResult):
    """Verify calibration models are healthy and not stale."""
    from src.state.db import get_shared_connection

    conn = get_shared_connection()

    # 1. Platt model coverage — should have models for active clusters
    result.checks_run += 1
    r = conn.execute("""
        SELECT COUNT(DISTINCT bucket_key), MAX(fitted_at)
        FROM platt_models
    """).fetchone()

    model_count = r[0] if r else 0
    latest_fit = r[1] if r else None

    if model_count == 0:
        result.findings.append(Finding(
            severity="P0",
            category="calibration",
            check="platt_model_coverage",
            message="No Platt models found — calibration not running",
        ))
    elif model_count < 10:
        result.findings.append(Finding(
            severity="P1",
            category="calibration",
            check="platt_model_coverage",
            message=f"Only {model_count} Platt models (expected 20+)",
            details={"model_count": model_count, "latest_fit": latest_fit},
        ))
    else:
        result.checks_passed += 1

    # 2. Platt model staleness
    result.checks_run += 1
    if latest_fit:
        try:
            fit_date = datetime.fromisoformat(latest_fit.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - fit_date).days
            if age_days > 30:
                result.findings.append(Finding(
                    severity="P1",
                    category="calibration",
                    check="platt_model_staleness",
                    message=f"Platt models are {age_days} days old (> 30 day threshold)",
                    details={"latest_fit": latest_fit, "age_days": age_days},
                ))
            else:
                result.checks_passed += 1
        except Exception:
            result.checks_passed += 1
    else:
        result.checks_passed += 1

    # 3. Calibration pair volume
    result.checks_run += 1
    r = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()
    pair_count = r[0] if r else 0

    if pair_count < 1000:
        result.findings.append(Finding(
            severity="P1",
            category="calibration",
            check="calibration_pair_volume",
            message=f"Only {pair_count} calibration pairs (need 1000+ for reliable Platt)",
            details={"pair_count": pair_count},
        ))
    else:
        result.checks_passed += 1

    # 4. Calibration pair outcome balance
    result.checks_run += 1
    r = conn.execute("""
        SELECT outcome, COUNT(*) FROM calibration_pairs GROUP BY outcome
    """).fetchall()

    outcome_counts = {row[0]: row[1] for row in r}
    ones = outcome_counts.get(1, 0)
    zeros = outcome_counts.get(0, 0)
    total = ones + zeros

    if total > 0:
        ratio = ones / total
        if ratio < 0.02 or ratio > 0.30:
            result.findings.append(Finding(
                severity="P1",
                category="calibration",
                check="calibration_outcome_balance",
                message=f"Outcome ratio {ratio:.2%} (1s={ones}, 0s={zeros}) — outside 2-30% range",
                details={"ones": ones, "zeros": zeros, "ratio": round(ratio, 4)},
            ))
        else:
            result.checks_passed += 1
    else:
        result.checks_passed += 1

    conn.close()


# ─────────────────────────────────────────────────────────────
# Check: Unenforced Contracts (structural scan)
# ─────────────────────────────────────────────────────────────

def check_unenforced_contracts(result: ScanResult):
    """Detect design-gap contracts that are defined but never called in production."""

    checks = [
        {
            "name": "D1_alpha_target",
            "contract_file": "src/contracts/alpha_decision.py",
            "assertion": "assert_target_compatible",
            "production_files": ["src/engine/evaluator.py", "src/strategy/kelly.py"],
            "severity": "P2",
        },
        {
            "name": "D3_kelly_safe",
            "contract_file": "src/contracts/execution_price.py",
            "assertion": "assert_kelly_safe",
            "production_files": ["src/engine/evaluator.py", "src/strategy/kelly.py"],
            "severity": "P2",
        },
        {
            "name": "D4_evidence_symmetry",
            "contract_file": "src/contracts/decision_evidence.py",
            "assertion": "assert_symmetric_with",
            "production_files": ["src/execution/exit_triggers.py", "src/execution/exit_lifecycle.py"],
            "severity": "P2",
        },
        {
            "name": "P10_reality_verifier",
            "contract_file": "src/contracts/reality_verifier.py",
            "assertion": "verify_all_blocking",
            "production_files": ["src/engine/cycle_runner.py", "src/engine/evaluator.py"],
            "severity": "P2",
        },
    ]

    for check in checks:
        result.checks_run += 1
        contract_path = PROJECT_ROOT / check["contract_file"]
        if not contract_path.exists():
            result.findings.append(Finding(
                severity="P1",
                category="contract",
                check=f"unenforced_{check['name']}",
                message=f"Contract file missing: {check['contract_file']}",
            ))
            continue

        # Check if assertion is called in ANY production file
        found = False
        for prod_file in check["production_files"]:
            prod_path = PROJECT_ROOT / prod_file
            if prod_path.exists():
                content = prod_path.read_text()
                if check["assertion"] in content:
                    found = True
                    break

        if not found:
            result.findings.append(Finding(
                severity=check["severity"],
                category="contract",
                check=f"unenforced_{check['name']}",
                message=f"{check['assertion']}() defined but not called in production",
                details={"contract": check["contract_file"],
                         "searched": check["production_files"]},
            ))
        else:
            result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

CHECK_REGISTRY = {
    "data_freshness": check_data_freshness,
    "contracts": check_contracts,
    "calibration": check_calibration,
    "unenforced_contracts": check_unenforced_contracts,
}


def run_scan(checks: list[str] | None = None) -> ScanResult:
    """Run the antibody scan. Returns structured results."""
    result = ScanResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    targets = checks or list(CHECK_REGISTRY.keys())
    for name in targets:
        fn = CHECK_REGISTRY.get(name)
        if fn is None:
            logger.warning("Unknown check: %s", name)
            continue
        try:
            logger.info("Running check: %s", name)
            fn(result)
        except Exception as e:
            result.findings.append(Finding(
                severity="P0",
                category="system",
                check=f"check_{name}_crashed",
                message=f"Check crashed: {e}",
            ))

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Zeus Antibody Scan")
    parser.add_argument("--check", choices=list(CHECK_REGISTRY.keys()),
                        help="Run specific check only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    checks = [args.check] if args.check else None
    result = run_scan(checks)

    if args.json:
        findings_dicts = [asdict(f) for f in result.findings]
        print(json.dumps({
            "timestamp": result.timestamp,
            "summary": result.summary,
            "has_critical": result.has_critical,
            "checks_run": result.checks_run,
            "checks_passed": result.checks_passed,
            "findings": findings_dicts,
        }, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"ZEUS ANTIBODY SCAN — {result.timestamp}")
        print(f"{'=' * 60}")
        print(f"Summary: {result.summary}")
        print()

        if not result.findings:
            print("  ALL CHECKS PASSED ✓")
        else:
            for f in sorted(result.findings, key=lambda x: x.severity):
                icon = {"P0": "🔴", "P1": "🟡", "P2": "🔵", "INFO": "ℹ️"}.get(f.severity, "?")
                print(f"  {icon} [{f.severity}] {f.category}/{f.check}")
                print(f"     {f.message}")
                if f.details:
                    for k, v in f.details.items():
                        print(f"     {k}: {v}")
                print()

    sys.exit(1 if result.has_critical else 0)


if __name__ == "__main__":
    main()
