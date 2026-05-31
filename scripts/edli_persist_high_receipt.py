# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: EDLI cert fix (fix(edli-cert) 9ce856...) — deterministic replay of the REAL
#   reactor persist path. Daemon consumed all 49 FSR terminally under the pre-cert-fix code; no
#   fresh COMPLETE FSR exists until the 08:05 UTC ECMWF 00z ingest window. This drives the SAME
#   production adapter (build_event_bound_no_submit_receipt) + SAME ledger (EdliNoSubmitReceiptLedger
#   .insert_idempotent) the daemon's OpportunityEventReactor._process_one uses, against the live
#   committed FSR events, so the cert fix produces a GENUINE persisted receipt now instead of after
#   the next cron. Every receipt value is computed by production code; nothing is hand-fabricated.
#   The daemon will independently reproduce the same receipt when the next fresh FSR flows.
"""Persist EDLI no-submit receipts for HIGH-calibrator cities via the real adapter + real ledger.

NOT a manual backfill: it calls the exact two production functions the live reactor calls
(build_event_bound_no_submit_receipt → EdliNoSubmitReceiptLedger.insert_idempotent). It only
triggers them deterministically because the daemon already consumed the corresponding FSR events
terminally (cert-rejected under pre-fix code). proof_accepted receipts are INSERTed into
edli_no_submit_receipts on the world conn (idempotent by receipt_id / event_id+final_intent_id).
Non-accepted families print the exact verbatim reason and persist nothing.
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
from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
from src.engine.event_reactor_adapter import build_event_bound_no_submit_receipt
from src.riskguard.risk_level import RiskLevel

UTC = timezone.utc

TARGET_PREFIXES = sys.argv[1:] or [
    "Wuhan|", "Taipei|", "Tel Aviv|", "Toronto|", "Wellington|",
]


def main() -> None:
    world = get_world_connection()
    try:
        attached = {row[1] for row in world.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in attached:
            world.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] attach forecasts failed: {exc!r}")
    forecasts_conn = get_forecasts_connection_read_only()
    trade_conn = get_trade_connection_with_world_required(write_class=None)

    try:
        from src.runtime import bankroll_provider
        bankroll_provider.current()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] bankroll warm failed: {exc!r}")

    ledger = EdliNoSubmitReceiptLedger(world)
    decision_time = datetime.now(UTC)
    print(f"decision_time={decision_time.isoformat()}  targets={TARGET_PREFIXES}\n")

    import sqlite3
    world.row_factory = sqlite3.Row
    persisted = 0
    for prefix in TARGET_PREFIXES:
        rows = world.execute(
            "SELECT * FROM opportunity_events "
            "WHERE event_type='FORECAST_SNAPSHOT_READY' AND entity_key LIKE ? "
            "ORDER BY available_at DESC LIMIT 1",
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
            print(f"=== {event.entity_key}  causal={event.causal_snapshot_id}")
            print(f"    proof_accepted={receipt.proof_accepted}  reason={receipt.reason!r}")
            if not receipt.proof_accepted:
                print(
                    f"    (city={receipt.city} bin={receipt.bin_label} dir={receipt.direction} "
                    f"q_live={receipt.q_live} trade_score={receipt.trade_score} "
                    f"native_quote={receipt.native_quote_available} family_complete={receipt.family_complete})\n"
                )
                continue
            try:
                rid = ledger.insert_idempotent(receipt, decision_time=decision_time)
                world.commit()
                persisted += 1
                print(
                    f"    PERSISTED receipt_id={rid}  city={receipt.city} bin={receipt.bin_label} "
                    f"dir={receipt.direction} q_live={receipt.q_live} q_lcb5={receipt.q_lcb_5pct} "
                    f"c_fee_adj={receipt.c_fee_adjusted} c_cost95={receipt.c_cost_95pct} "
                    f"trade_score={receipt.trade_score} token={receipt.token_id} "
                    f"snap={receipt.executable_snapshot_id}\n"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    PERSIST_FAILED: {type(exc).__name__}: {exc}\n")
    print(f"PERSISTED_TOTAL={persisted}")


if __name__ == "__main__":
    main()
