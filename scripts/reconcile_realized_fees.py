#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=2026-06-12
# Purpose: fit the realized taker-fee fraction from venue trade-level fee fields —
#   the evidence artifact behind src/contracts/fee_authority.py.
# Reuse: READ-ONLY over zeus_trades.db (file:...?mode=ro): scans MATCHED
#   venue_order_facts raw payloads for trade.fee_rate_bps + cross-checks
#   position_current cost_basis vs entry_price*shares residuals. Writes ONLY
#   state/fee_reconciliation.json. Registered in SQLITE_CONNECT_ALLOWLIST.
#   Rerun after new fills (manual or scheduled); the authority degrades to the
#   venue schedule when evidence is stale (>30d) or thin (<10 fills).
# Last reused/audited: 2026-06-12
# Authority basis: incident 2026-06-12 — CLOB schedule base_fee=1000bps consumed as
#   the actual fee while 12/12 realized fills carried fee_rate_bps=0; calibration
#   authority Task 2.3 (fit fee model from history, reconcile against fills).
"""Reconcile realized venue fees from fills -> state/fee_reconciliation.json.

USAGE
    .venv/bin/python scripts/reconcile_realized_fees.py [--out PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

TRADES_DB = "/Users/leofitz/zeus/state/zeus_trades.db"
OUT_DEFAULT = "/Users/leofitz/zeus/state/fee_reconciliation.json"


def _scan_fee_fields(obj, path=""):
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if "fee_rate_bps" in kl or kl == "fee":
                found[path + str(k)] = v
            found |= _scan_fee_fields(v, path + str(k) + ".")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            found |= _scan_fee_fields(v, path + f"[{i}].")
    return found


def collect_evidence(db_path: str = TRADES_DB) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=8.0)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    fills = []
    try:
        rows = conn.execute(
            "SELECT venue_order_id, state, matched_size, observed_at, raw_payload_json "
            "FROM venue_order_facts WHERE CAST(matched_size AS REAL) > 0"
        ).fetchall()
        for r in rows:
            try:
                payload = json.loads(r["raw_payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            fee_fields = _scan_fee_fields(payload)
            # Trade-level realized fee fields only (the schedule envelope's
            # fee_details.* describes the venue CAP, not the charged fee —
            # the exact data-semantics confusion this artifact exists to kill).
            realized = {
                k: v for k, v in fee_fields.items()
                if "fee_details" not in k and str(v).strip() not in ("", "None")
            }
            bps_values = []
            for v in realized.values():
                try:
                    bps_values.append(float(v))
                except (TypeError, ValueError):
                    continue
            fills.append({
                "venue_order_id": r["venue_order_id"],
                "observed_at": r["observed_at"],
                "matched_size": r["matched_size"],
                "realized_fee_bps_values": bps_values,
                "realized_fee_fields": {k: str(v) for k, v in realized.items()},
            })
    finally:
        conn.close()

    # Cost-basis residual cross-check (independent ledger arithmetic).
    conn2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=8.0)
    conn2.execute("PRAGMA query_only=ON")
    conn2.row_factory = sqlite3.Row
    residuals = []
    try:
        for r in conn2.execute(
            "SELECT city, target_date, entry_price, shares, cost_basis_usd "
            "FROM position_current WHERE shares > 0 AND entry_price > 0"
        ):
            nominal = float(r["entry_price"]) * float(r["shares"])
            cb = float(r["cost_basis_usd"] or 0.0)
            residuals.append(round(cb - nominal, 6))
    finally:
        conn2.close()

    all_bps = [b for f in fills for b in f["realized_fee_bps_values"]]
    max_bps = max(all_bps) if all_bps else 0.0
    return {
        "schema": "fee_reconciliation",
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "n_fills": len(fills),
        "observed_fee_bps_max": max_bps,
        "observed_max_fee_fraction": max_bps / 10000.0,
        "cost_basis_residuals": residuals,
        "cost_basis_residual_max_abs": max((abs(x) for x in residuals), default=0.0),
        "fills": fills,
        "source": "venue_order_facts trade-level fee fields + position_current cost-basis arithmetic",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile realized venue fees from fills.")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--db", default=TRADES_DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    artifact = collect_evidence(args.db)
    summary = (
        f"n_fills={artifact['n_fills']} max_fee_bps={artifact['observed_fee_bps_max']} "
        f"fraction={artifact['observed_max_fee_fraction']} "
        f"cost_basis_resid_max={artifact['cost_basis_residual_max_abs']}"
    )
    if args.dry_run:
        sys.stdout.write("DRY-RUN " + summary + "\n")
        return 0
    with open(args.out, "w") as fh:
        json.dump(artifact, fh, indent=1)
    sys.stdout.write(f"wrote {args.out}  {summary}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
