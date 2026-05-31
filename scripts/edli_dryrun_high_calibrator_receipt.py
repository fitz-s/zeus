# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: EDLI cert fix verification (fix(edli-cert) 9ce856...); read-only decision dry-run.
"""Read-only EDLI no-submit decision dry-run for HIGH-calibrator cities.

Reconstructs already-committed FORECAST_SNAPSHOT_READY events from the live world DB and runs the
REAL build_event_bound_no_submit_receipt adapter against the live forecasts/world connections,
wired exactly as src/main._edli_event_reactor_cycle wires them. Pure read: build_event_bound_no_submit_receipt
only SELECTs; it returns an EventSubmissionReceipt object and does not INSERT/UPDATE. No event injection,
no DB mutation. Proves whether the two cert fixes let a COMPLETE FSR reach a receipt, or surfaces the
exact next cert reason verbatim.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    get_world_connection,
    get_forecasts_connection_read_only,
    get_trade_connection_with_world_required,
)
from src.events.event_store import _event_from_row
from src.engine.event_reactor_adapter import build_event_bound_no_submit_receipt
from src.riskguard.risk_level import RiskLevel

UTC = timezone.utc

TARGET_PREFIXES = sys.argv[1:] or [
    "Wuhan|", "Taipei|", "Tel Aviv|", "Toronto|", "Wellington|",
]


def main() -> None:
    world = get_world_connection()
    # Mirror main.py: ATTACH forecasts read-only so calibration_pairs resolves on the world conn.
    try:
        attached = {row[1] for row in world.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in attached:
            world.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] attach forecasts failed: {exc!r}")
    forecasts_conn = get_forecasts_connection_read_only()
    trade_conn = get_trade_connection_with_world_required(write_class=None)

    # Warm bankroll cache (Kelly reads cached bankroll; must not live-fetch per decision).
    try:
        from src.runtime import bankroll_provider
        bankroll_provider.current()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] bankroll warm failed: {exc!r}")

    decision_time = datetime.now(UTC)
    print(f"decision_time={decision_time.isoformat()}  targets={TARGET_PREFIXES}\n")

    world.row_factory = __import__("sqlite3").Row
    for prefix in TARGET_PREFIXES:
        rows = world.execute(
            "SELECT * FROM opportunity_events "
            "WHERE event_type='FORECAST_SNAPSHOT_READY' AND entity_key LIKE ? "
            "ORDER BY available_at DESC LIMIT 2",
            (prefix + "%high%",),
        ).fetchall()
        if not rows:
            print(f"=== {prefix} : NO FSR event found")
            continue
        for row in rows:
            event = _event_from_row(row)
            try:
                receipt = build_event_bound_no_submit_receipt(
                    event,
                    trade_conn=trade_conn,
                    decision_time=decision_time,
                    forecast_conn=forecasts_conn,
                    topology_conn=forecasts_conn,
                    calibration_conn=world,
                    get_current_level=lambda: RiskLevel.GREEN,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"=== {event.entity_key}  causal={event.causal_snapshot_id}")
                print(f"    EXCEPTION: {type(exc).__name__}: {exc}\n")
                continue
            accepted = receipt.proof_accepted
            print(f"=== {event.entity_key}  causal={event.causal_snapshot_id}")
            print(f"    proof_accepted={accepted}  reason={receipt.reason!r}")
            if accepted:
                print(
                    f"    RECEIPT: city={receipt.city} bin={receipt.bin_label} dir={receipt.direction} "
                    f"q_live={receipt.q_live} q_lcb5={receipt.q_lcb_5pct} "
                    f"c_fee_adj={receipt.c_fee_adjusted} c_cost95={receipt.c_cost_95pct} "
                    f"trade_score={receipt.trade_score} token={receipt.token_id} "
                    f"snap={receipt.executable_snapshot_id}"
                )
            else:
                print(
                    f"    (city={receipt.city} bin={receipt.bin_label} dir={receipt.direction} "
                    f"q_live={receipt.q_live} trade_score={receipt.trade_score} "
                    f"native_quote={receipt.native_quote_available} family_complete={receipt.family_complete})"
                )
            print()


if __name__ == "__main__":
    main()
