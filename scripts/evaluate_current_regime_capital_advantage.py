#!/usr/bin/env python3
# Lifecycle: created=2026-08-12; last_reviewed=2026-08-12; last_reused=2026-08-12
# Purpose: Grade exact current selection/probability revisions on causal capital outcomes.
# Reuse: Run read-only against canonical WORLD/FORECAST/TRADES DBs; output is evidence, not authority.
"""Fail-closed evaluator for current-regime capital advantage.

Old profit, model scores, marks, and mixed-revision fills cannot satisfy this
contract.  The evaluator reports the narrowest missing proof line and writes a
deterministic evidence artifact; it never mutates canonical state or submits an
order.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.global_batch_runtime import (  # noqa: E402
    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
)
from src.events.day0_authority import (  # noqa: E402
    DAY0_PROBABILITY_SEMANTICS_REVISION,
)
from src.riskguard import riskguard as rg  # noqa: E402
from src.state.db import (  # noqa: E402
    get_forecasts_connection_read_only,
    get_trade_connection_read_only,
    get_world_connection_read_only,
)
from src.data.replacement_forecast_cycle_policy import (  # noqa: E402
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)

MIN_INDEPENDENT_FAMILY_DAYS = 30
WINDOW_DAYS = 35.0


def _read_only(
    path: Path,
    required_tables: frozenset[str],
    *,
    connection_factory: Callable[[], sqlite3.Connection],
) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 4096:
        raise ValueError(f"canonical DB missing or placeholder: {resolved}")
    conn = connection_factory()
    database_rows = conn.execute("PRAGMA database_list").fetchall()
    main_paths = [
        Path(str(row[2])).resolve()
        for row in database_rows
        if str(row[1]) == "main" and str(row[2]).strip()
    ]
    if main_paths != [resolved]:
        conn.close()
        raise ValueError(
            f"configured canonical DB path mismatch: expected={resolved}:actual={main_paths}"
        )
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(required_tables.difference(tables))
    if missing:
        conn.close()
        raise ValueError(
            f"canonical DB schema mismatch: {resolved}:missing={','.join(missing)}"
        )
    return conn


def _receipt_revision_coverage(conn: sqlite3.Connection) -> dict[str, object]:
    row = conn.execute(
        "SELECT id,completed_at,artifact_json FROM decision_log "
        "WHERE mode='global_single_order_auction' AND completed_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {"ready": False, "reason": "global_auction_receipt_missing"}
    try:
        artifact = json.loads(str(row["artifact_json"] or ""))
        summary = artifact["summary"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"ready": False, "reason": "global_auction_receipt_invalid"}
    exact_revision = (
        summary.get("global_selection_revision")
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    )
    wealth = summary.get("portfolio_wealth")
    wealth_ready = isinstance(wealth, dict) and all(
        str(wealth.get(field) or "").strip()
        for field in (
            "ledger_snapshot_id",
            "position_set_hash",
            "wealth_floor_usd",
            "wealth_ceiling_usd",
            "spendable_cash_usd",
            "reservations_usd",
            "collateral_authority",
        )
    )
    coverage_ready = all(
        summary.get(field) is True
        for field in (
            "scope_family_coverage_complete",
            "candidate_coverage_complete",
            "held_position_coverage_complete",
            "book_capture_freshness_complete",
        )
    )
    return {
        "ready": bool(exact_revision and wealth_ready and coverage_ready),
        "decision_log_id": int(row["id"]),
        "completed_at": str(row["completed_at"]),
        "observed_selection_revision": summary.get(
            "global_selection_revision"
        ),
        "expected_selection_revision": (
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "selection_revision_ready": exact_revision,
        "portfolio_wealth_ready": wealth_ready,
        "coverage_ready": coverage_ready,
    }


def _shadow_evidence(
    world: sqlite3.Connection,
    *,
    strategy_key: str,
    forecast_factory: Callable[[], sqlite3.Connection],
    as_of: datetime,
) -> dict[str, object]:
    rows, status = rg._settled_market_relative_alpha_shadow_rows(
        world,
        strategy_key=strategy_key,
        window_days=WINDOW_DAYS,
        as_of=as_of,
        forecasts_connection_factory=forecast_factory,
    )
    # Existing shadow certificates freeze an older per-target-date edge rule,
    # not the current complete-universe expected-growth winner.  Preserve their
    # observations but never launder them into the current selection cohort.
    target_dates = {
        str((row.get("entry_market_benchmark_family") or ("", "", ""))[1])
        for row in rows
        if len(tuple(row.get("entry_market_benchmark_family") or ())) == 3
    }
    return {
        "status": status,
        "settled_row_count": len(rows),
        "independent_family_day_count": len(target_dates),
        "global_selection_revision_bound": False,
        "delta_log_wealth_lcb95": None,
        "reason": "current_global_selection_counterfactual_not_yet_persisted",
    }


def _build_verdict(
    *,
    receipt: dict[str, object],
    shadows: dict[str, dict[str, object]],
    live_curves: dict[str, dict[str, object]],
) -> tuple[str, list[str]]:
    failures: list[str] = []
    if receipt.get("ready") is not True:
        failures.append("CURRENT_GLOBAL_SELECTION_RECEIPT_UNPROVEN")
    independent = sum(
        int(row.get("independent_family_day_count") or 0)
        for row in shadows.values()
        if row.get("global_selection_revision_bound") is True
    )
    if independent < MIN_INDEPENDENT_FAMILY_DAYS:
        failures.append("INSUFFICIENT_CURRENT_REGIME_SETTLED_FAMILY_DAYS")
    lcbs = [
        row.get("delta_log_wealth_lcb95")
        for row in shadows.values()
        if row.get("global_selection_revision_bound") is True
    ]
    if not lcbs or any(
        value is None or not math.isfinite(float(value)) or float(value) <= 0.0
        for value in lcbs
    ):
        failures.append("AFTER_COST_DELTA_LOG_WEALTH_LCB_NOT_POSITIVE")
    exact_live = [
        row for row in live_curves.values()
        if row.get("selection_revision_bound") is True
    ]
    if not exact_live or sum(
        float(row.get("net_realized_pnl_usd") or 0.0) for row in exact_live
    ) <= 0.0:
        failures.append("EXACT_REVISION_LIVE_NET_CAPITAL_GAIN_NOT_POSITIVE")
    return ("PASS" if not failures else "FAIL", failures)


def evaluate(
    *,
    world_path: Path,
    forecasts_path: Path,
    trades_path: Path,
    as_of: datetime,
) -> dict[str, object]:
    world = _read_only(
        world_path,
        frozenset({"no_trade_regret_events"}),
        connection_factory=get_world_connection_read_only,
    )
    trades = _read_only(
        trades_path,
        frozenset(
            {
                "decision_log",
                "venue_commands",
                "venue_submission_envelopes",
                "execution_fact",
                "position_events",
                "position_current",
            }
        ),
        connection_factory=get_trade_connection_read_only,
    )

    def forecast_factory() -> sqlite3.Connection:
        return _read_only(
            forecasts_path,
            frozenset({"settlement_outcomes", "market_events"}),
            connection_factory=get_forecasts_connection_read_only,
        )

    try:
        receipt = _receipt_revision_coverage(trades)
        shadows = {
            strategy: _shadow_evidence(
                world,
                strategy_key=strategy,
                forecast_factory=forecast_factory,
                as_of=as_of,
            )
            for strategy in (
                "day0_nowcast_entry",
                "forecast_qkernel_entry",
            )
        }
        live_curves = {
            "day0_nowcast_entry": {
                **rg._day0_live_realized_capital_curve(
                    trades, window_days=WINDOW_DAYS, as_of=as_of
                ),
                "selection_revision_bound": False,
            },
            "forecast_qkernel_entry": {
                **rg._qkernel_live_realized_capital_curve(
                    trades, window_days=WINDOW_DAYS, as_of=as_of
                ),
                "selection_revision_bound": False,
            },
        }
    finally:
        world.close()
        trades.close()
    verdict, failures = _build_verdict(
        receipt=receipt,
        shadows=shadows,
        live_curves=live_curves,
    )
    return {
        "schema_version": 1,
        "artifact_role": "OBSERVATIONAL_EVIDENCE_NOT_ORDER_AUTHORITY",
        "evaluated_at": as_of.isoformat(),
        "verdict": verdict,
        "admission_eligible": verdict == "PASS",
        "failures": failures,
        "contract": {
            "minimum_independent_family_days": MIN_INDEPENDENT_FAMILY_DAYS,
            "delta_log_wealth_lcb95_must_exceed": 0.0,
            "live_net_realized_pnl_must_exceed_usd": 0.0,
            "global_selection_revision": (
                CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_revisions": {
                "day0_nowcast_entry": DAY0_PROBABILITY_SEMANTICS_REVISION,
                "forecast_qkernel_entry": CURRENT_EVIDENCE_SEMANTICS_REVISION,
            },
        },
        "database_paths": {
            "world": str(world_path.resolve()),
            "forecasts": str(forecasts_path.resolve()),
            "trades": str(trades_path.resolve()),
        },
        "latest_global_receipt": receipt,
        "settled_counterfactuals": shadows,
        "live_realized_capital": live_curves,
    }


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--world", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    world = args.world or args.trades.with_name("zeus-world.db")
    as_of = datetime.now(timezone.utc)
    try:
        artifact = evaluate(
            world_path=world,
            forecasts_path=args.forecasts,
            trades_path=args.trades,
            as_of=as_of,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        artifact = {
            "schema_version": 1,
            "artifact_role": "OBSERVATIONAL_EVIDENCE_NOT_ORDER_AUTHORITY",
            "evaluated_at": as_of.isoformat(),
            "verdict": "FAIL",
            "admission_eligible": False,
            "failures": [f"CAPITAL_TRUTH_UNAVAILABLE:{type(exc).__name__}:{exc}"],
        }
    _atomic_write(args.artifact, artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
