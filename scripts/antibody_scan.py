#!/usr/bin/env python3
"""Zeus Antibody Scan — macro-level system health checks.

DESIGN PHILOSOPHY: Each check answers a BIG question about system health.
NOT a linter. NOT a unit test. This is an immune system — it detects whether
the organism (Zeus) is healthy enough to trade safely.

The 4 macro questions:
1. PIPELINE HEALTH — Are all data feeds flowing? (settlements, ENS, obs, markets)
2. DATA INTEGRITY — Is the data we have semantically correct? (precision, seasons)
3. MODEL HEALTH  — Are calibration models fresh and well-fed?
4. GUARDRAIL COVERAGE — Are safety contracts actually wired into prod?

Returns structured JSON results suitable for Discord alerting.
Exit code 0 = healthy, 1 = critical finding.

Usage:
    cd zeus
    source ../.venv/bin/activate

    python scripts/antibody_scan.py              # full scan, human output
    python scripts/antibody_scan.py --json        # JSON for Discord/cron
    python scripts/antibody_scan.py --check pipeline_health
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
# Q1: PIPELINE HEALTH — Are all data feeds flowing?
# ─────────────────────────────────────────────────────────────

def check_pipeline_health(result: ScanResult):
    """Macro check: are the 4 core data pipelines alive and producing rows?

    Pipelines: WU settlements, ENS ensemble, WU observations, Gamma market_events.
    A pipeline is "dead" if zero rows arrived in its expected freshness window.
    """
    from src.state.db import get_world_connection

    conn = get_world_connection()
    today = date.today()
    result.checks_run += 1

    dead, degraded, details = [], [], {}

    # Settlements (expect daily for 10+ cities)
    r = conn.execute("""
        SELECT COUNT(DISTINCT city), MAX(target_date)
        FROM settlements
        WHERE settlement_value IS NOT NULL AND target_date >= ?
    """, ((today - timedelta(days=1)).isoformat(),)).fetchone()
    stl_cities, stl_latest = (r[0] or 0), r[1]
    details["settlements"] = {"recent_cities": stl_cities, "latest": stl_latest}
    if stl_cities == 0:
        dead.append("settlements")
    elif stl_cities < 10:
        degraded.append(f"settlements ({stl_cities} cities)")

    # ENS ensemble (expect snapshots within 24h)
    r = conn.execute("""
        SELECT COUNT(*), MAX(fetch_time)
        FROM ensemble_snapshots
        WHERE fetch_time >= ?
    """, ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),)).fetchone()
    ens_count, ens_latest = (r[0] or 0), r[1]
    details["ens"] = {"recent_snapshots": ens_count, "latest": ens_latest}
    if ens_count == 0:
        degraded.append("ENS ensemble")

    # WU observations (expect data within 3 days for 5+ cities)
    r = conn.execute("""
        SELECT COUNT(DISTINCT city)
        FROM observations WHERE target_date >= ?
    """, ((today - timedelta(days=3)).isoformat(),)).fetchone()
    obs_cities = r[0] or 0
    details["observations"] = {"recent_cities": obs_cities}
    if obs_cities < 5:
        degraded.append(f"WU observations ({obs_cities} cities)")

    # Market events (expect future-dated events)
    r = conn.execute("""
        SELECT COUNT(DISTINCT city), MAX(target_date)
        FROM market_events WHERE target_date >= ?
    """, (today.isoformat(),)).fetchone()
    mkt_cities, mkt_latest = (r[0] or 0), r[1]
    details["market_events"] = {"future_cities": mkt_cities, "latest": mkt_latest}
    if mkt_cities == 0:
        degraded.append("market_events")

    conn.close()

    if dead:
        result.findings.append(Finding(
            severity="P0",
            category="pipeline_health",
            check="data_pipeline_liveness",
            message=f"DEAD pipelines: {', '.join(dead)}",
            details=details,
        ))
    elif degraded:
        result.findings.append(Finding(
            severity="P1",
            category="pipeline_health",
            check="data_pipeline_liveness",
            message=f"Degraded pipelines: {', '.join(degraded)}",
            details=details,
        ))
    else:
        result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Q2: DATA INTEGRITY — Is stored data semantically correct?
# ─────────────────────────────────────────────────────────────

def check_data_integrity(result: ScanResult):
    """Macro check: do the values in the DB obey Zeus's semantic contracts?

    Checks: settlement precision (integer), SH season mapping, city config completeness.
    One finding per broken invariant category — not per individual row.
    """
    from src.state.db import get_world_connection

    result.checks_run += 1
    problems = []

    # Settlement integer precision
    try:
        conn = get_world_connection()
        r = conn.execute("""
            SELECT COUNT(*) FROM settlements
            WHERE settlement_value IS NOT NULL
              AND settlement_value != ROUND(settlement_value)
        """).fetchone()
        fractional = r[0] or 0
        conn.close()
        if fractional > 0:
            problems.append(f"{fractional} non-integer settlement values")
    except Exception as e:
        problems.append(f"settlement check failed: {e}")

    # SH season mapping smoke test
    try:
        from src.calibration.manager import season_from_date
        sh = season_from_date("2025-07-15", lat=-34.0)
        nh = season_from_date("2025-07-15", lat=40.0)
        if sh != "DJF" or nh != "JJA":
            problems.append(f"season mapping wrong: SH July={sh}, NH July={nh}")
    except Exception as e:
        problems.append(f"season mapping broken: {e}")

    # City config: all 46 cities loadable with required fields
    try:
        from src.config import load_cities
        cities = load_cities()
        bad = [c.name for c in cities if not c.wu_station or not c.timezone]
        if bad:
            problems.append(f"{len(bad)} cities missing wu_station/timezone: {', '.join(bad[:5])}")
    except Exception as e:
        problems.append(f"city config load failed: {e}")

    if problems:
        sev = "P0" if any("settlement" in p or "season" in p for p in problems) else "P1"
        result.findings.append(Finding(
            severity=sev,
            category="data_integrity",
            check="semantic_correctness",
            message=f"{len(problems)} integrity issue(s): {'; '.join(problems[:3])}",
            details={"all_problems": problems},
        ))
    else:
        result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Q3: MODEL HEALTH — Are calibration models fresh and well-fed?
# ─────────────────────────────────────────────────────────────

def check_model_health(result: ScanResult):
    """Macro check: is the Platt calibration layer healthy?

    Checks: model existence, staleness, training data volume, outcome balance.
    One consolidated finding.
    """
    from src.state.db import get_world_connection

    conn = get_world_connection()
    result.checks_run += 1
    problems = []

    # Platt models
    r = conn.execute("""
        SELECT COUNT(DISTINCT bucket_key), MAX(fitted_at)
        FROM platt_models
    """).fetchone()
    model_count, latest_fit = (r[0] or 0), r[1]

    if model_count == 0:
        problems.append("no Platt models exist — calibration not running")
    elif model_count < 10:
        problems.append(f"only {model_count} Platt models (expect 20+)")

    if latest_fit:
        try:
            fit_dt = datetime.fromisoformat(latest_fit.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - fit_dt).days
            if age_days > 30:
                problems.append(f"models {age_days} days stale (> 30d threshold)")
        except Exception:
            pass

    # Calibration pair volume & balance
    r = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()
    pair_count = r[0] or 0
    if pair_count < 1000:
        problems.append(f"only {pair_count} calibration pairs (need 1000+)")

    r = conn.execute("""
        SELECT outcome, COUNT(*) FROM calibration_pairs GROUP BY outcome
    """).fetchall()
    outcome_map = {row[0]: row[1] for row in r}
    ones, zeros = outcome_map.get(1, 0), outcome_map.get(0, 0)
    total = ones + zeros
    if total > 0:
        ratio = ones / total
        if ratio < 0.02 or ratio > 0.30:
            problems.append(f"outcome ratio {ratio:.1%} outside 2-30% band")

    conn.close()

    if problems:
        sev = "P0" if "not running" in str(problems) else "P1"
        result.findings.append(Finding(
            severity=sev,
            category="model_health",
            check="calibration_layer",
            message=f"{len(problems)} issue(s): {'; '.join(problems[:3])}",
            details={
                "model_count": model_count,
                "latest_fit": latest_fit,
                "pair_count": pair_count,
                "outcome_ones": ones,
                "outcome_zeros": zeros,
                "all_problems": problems,
            },
        ))
    else:
        result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Q4: GUARDRAIL COVERAGE — Are safety contracts wired in?
# ─────────────────────────────────────────────────────────────

def check_guardrail_coverage(result: ScanResult):
    """Macro check: how many of Zeus's design-gap contracts are actually enforced?

    Scans production source for assertion calls + runtime scaffolding.
    Reports a single coverage %. This is a structural linter, not a runtime check.
    """
    result.checks_run += 1

    contracts = [
        ("D1 α-target", "src/contracts/alpha_decision.py",
         "assert_target_compatible",
         ["src/engine/evaluator.py", "src/strategy/kelly.py"]),
        ("D3 Kelly-safe", "src/contracts/execution_price.py",
         "assert_kelly_safe",
         ["src/engine/evaluator.py", "src/strategy/kelly.py"]),
        ("D4 evidence-sym", "src/contracts/decision_evidence.py",
         "assert_symmetric_with",
         ["src/execution/exit_triggers.py", "src/execution/exit_lifecycle.py"]),
        ("P10 reality-gate", "src/contracts/reality_verifier.py",
         "verify_all_blocking",
         ["src/engine/cycle_runner.py", "src/engine/evaluator.py"]),
        # Phase 3 contracts
        ("C1 provenance", "src/contracts/provenance_registry.py",
         "require_provenance",
         ["src/strategy/kelly.py", "src/engine/cycle_runner.py"]),
        ("B4 chronicle-dedup", "src/state/chronicler.py",
         "chronicle_dedup",
         ["src/state/chronicler.py"]),
        ("F1 exit-authority", "src/execution/exit_lifecycle.py",
         "mark_settled",
         ["src/execution/exit_lifecycle.py", "src/execution/harvester.py"]),
    ]

    wired, unwired = [], []
    for label, contract_file, assertion, prod_files in contracts:
        if not (PROJECT_ROOT / contract_file).exists():
            unwired.append(label)
            continue
        found = any(
            assertion in (PROJECT_ROOT / pf).read_text()
            for pf in prod_files
            if (PROJECT_ROOT / pf).exists()
        )
        (wired if found else unwired).append(label)

    total = len(contracts)
    coverage = len(wired) / total if total else 0

    if coverage < 0.5:
        result.findings.append(Finding(
            severity="P2",
            category="guardrail_coverage",
            check="contract_enforcement",
            message=f"Guardrail coverage {coverage:.0%} — {len(unwired)}/{total} contracts unwired: {', '.join(unwired)}",
            details={"wired": wired, "unwired": unwired, "coverage": round(coverage, 2)},
        ))
    else:
        result.checks_passed += 1


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

CHECK_REGISTRY = {
    "pipeline_health": check_pipeline_health,
    "data_integrity": check_data_integrity,
    "model_health": check_model_health,
    "guardrail_coverage": check_guardrail_coverage,
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
