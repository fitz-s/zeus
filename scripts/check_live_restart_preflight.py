#!/usr/bin/env python3
# Lifecycle: created=2026-06-18; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Read-only preflight before restarting the live trading daemon.
# Reuse: Run immediately before loading com.zeus.live-trading or python -m src.main.
# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: Zeus live-money restart proof gates in AGENTS.md.
"""Read-only live restart preflight.

This script does not submit, cancel, repair, or write any DB/state files.  It
separates restart evidence that is often conflated in operator summaries:
process state, configured submit authority, posterior freshness, pending-exit
actuation risk, and held-position belief coverage.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_data_pipeline_live_e2e import _connect_live_readonly

TRADE_DB = ROOT / "state" / "zeus_trades.db"
WORLD_DB = ROOT / "state" / "zeus-world.db"
FORECAST_DB = ROOT / "state" / "zeus-forecasts.db"
SETTINGS_PATH = ROOT / "config" / "settings.json"
DUST_SHARE_LIMIT = 0.01


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    evidence: dict[str, Any]


def _connect_live_ro():
    return _connect_live_readonly(
        trade_db=TRADE_DB,
        world_db=WORLD_DB,
        forecasts_db=FORECAST_DB,
    )


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _live_main_processes() -> list[str]:
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        return []
    rows: list[str] = []
    for line in out.splitlines():
        if "python" in line and ("-m src.main" in line or "src.main" in line):
            if "check_live_restart_preflight" not in line:
                rows.append(line.strip())
    return rows


def _settings() -> dict[str, Any]:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def _parse_dt(raw: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _posterior_summary() -> CheckResult:
    now = datetime.now(timezone.utc)
    with _connect_live_ro() as conn:
        runtime_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT COALESCE(runtime_layer, '') AS runtime_layer,
                       COUNT(*) AS rows,
                       MIN(computed_at) AS min_computed_at,
                       MAX(computed_at) AS max_computed_at
                  FROM forecasts.forecast_posteriors
                 GROUP BY COALESCE(runtime_layer, '')
                 ORDER BY runtime_layer
                """
            )
        ]
        latest = conn.execute(
            """
            SELECT computed_at, source_cycle_time
              FROM forecasts.forecast_posteriors
             WHERE runtime_layer = 'live'
             ORDER BY datetime(computed_at) DESC, posterior_id DESC
             LIMIT 1
            """
        ).fetchone()
    latest_dt = _parse_dt(latest["computed_at"]) if latest else None
    age_hours = None
    if latest_dt is not None:
        age_hours = (now - latest_dt).total_seconds() / 3600.0
    non_live = sum(int(row["rows"]) for row in runtime_rows if row["runtime_layer"] != "live")
    ok = non_live == 0 and age_hours is not None and 0.0 <= age_hours <= 3.0
    return CheckResult(
        "live_posterior_freshness",
        ok,
        "latest live posterior is fresh" if ok else "latest live posterior is stale/missing or non-live rows exist",
        {
            "runtime_layers": runtime_rows,
            "latest_live_computed_at": latest["computed_at"] if latest else None,
            "latest_live_age_hours": age_hours,
            "non_live_rows": non_live,
            "fresh_age_limit_hours": 3.0,
        },
    )


def _open_positions() -> list[Any]:
    with _connect_live_ro() as conn:
        return list(
            conn.execute(
                """
                SELECT position_id, phase, city, target_date, temperature_metric,
                       bin_label, direction, shares, chain_shares, order_status,
                       exit_reason, exit_retry_count, next_exit_retry_at,
                       last_monitor_prob, last_monitor_prob_is_fresh,
                       last_monitor_market_price, last_monitor_market_price_is_fresh,
                       updated_at
                  FROM position_current
                 WHERE phase IN ('active', 'day0_window', 'pending_exit')
                   AND COALESCE(chain_shares, shares, 0) > 0
                 ORDER BY CASE phase WHEN 'pending_exit' THEN 0 ELSE 1 END,
                          city, target_date, bin_label
                """
            )
        )


def _pending_exit_check(rows: list[sqlite3.Row]) -> CheckResult:
    risky: list[dict[str, Any]] = []
    tolerated: list[dict[str, Any]] = []
    for row in rows:
        if row["phase"] != "pending_exit":
            continue
        shares = float(row["chain_shares"] if row["chain_shares"] is not None else row["shares"] or 0.0)
        reason = str(row["exit_reason"] or "")
        order_status = str(row["order_status"] or "")
        item = {
            "position_id": row["position_id"],
            "city": row["city"],
            "target_date": row["target_date"],
            "bin_label": row["bin_label"],
            "shares": shares,
            "order_status": order_status,
            "exit_reason": reason,
        }
        if reason == "MARKET_CLOSED_AWAITING_SETTLEMENT":
            tolerated.append(item)
        elif reason == "EXIT_CHAIN_DUST_STILL_HELD" and shares <= DUST_SHARE_LIMIT:
            tolerated.append(item)
            if order_status != "backoff_exhausted":
                item = dict(item)
                item["risk"] = "dust_projection_needs_backoff_exhausted_reload_repair"
                risky.append(item)
        else:
            risky.append(item)
    return CheckResult(
        "pending_exit_restart_risk",
        not risky,
        "no restart-dangerous pending exits" if not risky else "pending exits need resolution before armed restart",
        {"risky": risky, "tolerated": tolerated},
    )


def _belief_check(rows: list[sqlite3.Row]) -> CheckResult:
    from src.engine.position_belief import load_replacement_belief, monitor_belief_max_age_hours

    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    max_age = monitor_belief_max_age_hours()
    for row in rows:
        if row["phase"] == "pending_exit":
            continue
        belief = load_replacement_belief(
            city=str(row["city"] or ""),
            target_date=str(row["target_date"] or ""),
            temperature_metric=str(row["temperature_metric"] or "high"),
            bin_label=str(row["bin_label"] or ""),
            direction=str(row["direction"] or ""),
            max_age_hours=max_age,
            db_path=str(FORECAST_DB),
        )
        item = {
            "position_id": row["position_id"],
            "city": row["city"],
            "target_date": row["target_date"],
            "temperature_metric": row["temperature_metric"],
            "bin_label": row["bin_label"],
            "direction": row["direction"],
        }
        if belief is None:
            risky.append({**item, "risk": "missing_live_belief"})
            continue
        evidence = {
            **item,
            "posterior_id": belief.posterior_id,
            "computed_at": belief.computed_at,
            "age_hours": belief.age_hours,
            "source_cycle_age_hours": belief.source_cycle_age_hours,
            "fresh": belief.fresh,
            "held_side_prob": belief.held_side_prob,
            "q_yes_bin": belief.q_yes_bin,
            "freshness_basis": belief.freshness_basis,
        }
        covered.append(evidence)
        if not belief.fresh:
            risky.append({**evidence, "risk": "stale_live_belief"})
    return CheckResult(
        "held_position_belief_coverage",
        not risky,
        "all active held positions have fresh live belief" if not risky else "active held positions have stale/missing live belief",
        {"risky": risky, "covered": covered, "max_age_hours": max_age},
    )


def evaluate() -> dict[str, Any]:
    cfg = _settings()
    real_submit = bool((cfg.get("edli") or {}).get("real_order_submit_enabled", False))
    rows = _open_positions()
    checks = [
        CheckResult(
            "live_trading_process_absent",
            not _live_main_processes(),
            "src.main is not running" if not _live_main_processes() else "src.main is already running",
            {"processes": _live_main_processes()},
        ),
        CheckResult(
            "submit_authority_config",
            True,
            "real order submit config read",
            {"edli.real_order_submit_enabled": real_submit},
        ),
        _posterior_summary(),
        _pending_exit_check(rows),
        _belief_check(rows),
    ]
    blockers = [asdict(check) for check in checks if not check.ok]
    return {
        "ok": not blockers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "trade_db": str(TRADE_DB),
        "forecast_db": str(FORECAST_DB),
        "open_position_count": len(rows),
        "real_order_submit_enabled": real_submit,
        "checks": [asdict(check) for check in checks],
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)
    result = evaluate()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"live restart preflight: {'PASS' if result['ok'] else 'FAIL'}")
        print(f"generated_at={result['generated_at']} git_head={result['git_head']}")
        for check in result["checks"]:
            status = "PASS" if check["ok"] else "FAIL"
            print(f"{status} {check['name']}: {check['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
