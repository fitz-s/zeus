# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md
#   D5 defect audit — BinEdge.entry_price is bare float stamped "implied_probability"
#   at the Kelly boundary in evaluator.py:1550-1557. This script traces live cycle
#   data through the seam and counts occurrences. READ-ONLY. No runtime DB writes.

"""Forensic audit: D5 coercion seam per-bin price-type provenance.

Reads decision_log trade_cases, reconstructs evaluator.py:1550-1557 seam,
counts bins where price_type_in="implied_probability". READ-ONLY.
"""

import argparse
import csv
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Repo root on sys.path so src.* imports work.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import get_trade_connection_read_only  # INV-37: canonical accessor
from src.contracts.execution_price import ExecutionPrice, polymarket_fee

# Fee rate: try config; fallback to spec-anchored default.
_FEE_RATE_DEFAULT = 0.05
_FEE_RATE_SOURCE = "fallback(0.05)"


def _load_fee_rate() -> tuple[float, str]:
    try:
        import yaml  # type: ignore
        with open(_REPO_ROOT / "config" / "reality_contracts" / "economic.yaml") as f:
            data = yaml.safe_load(f)
        for c in data if isinstance(data, list) else data.get("reality_contracts", []):
            if isinstance(c, dict) and c.get("contract_id") == "FEE_RATE_WEATHER":
                if "pinned_value" in c:
                    return float(c["pinned_value"]), "economic.yaml:FEE_RATE_WEATHER"
        return _FEE_RATE_DEFAULT, "fallback(no pinned_value in yaml)"
    except Exception:
        return _FEE_RATE_DEFAULT, _FEE_RATE_SOURCE


def _get_conn(db_path: str | None):
    if db_path:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    conn = get_trade_connection_read_only()
    conn.row_factory = __import__("sqlite3").Row
    return conn


def _fetch_cycle(conn, cycle_id: int | None) -> tuple[int, str, dict] | None:
    """Return (id, started_at, artifact) for the requested or latest cycle."""
    if cycle_id is not None:
        row = conn.execute(
            "SELECT id, started_at, artifact_json FROM decision_log WHERE id = ?",
            (cycle_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, started_at, artifact_json FROM decision_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return row["id"], row["started_at"], json.loads(row["artifact_json"])


def _reconstruct_seam(entry_price_float: float, fee_rate: float) -> dict:
    """Mirror evaluator.py:1550-1557 exactly (D5 seam)."""
    ep = ExecutionPrice(value=float(entry_price_float), price_type="implied_probability",
                        fee_deducted=False, currency="probability_units")
    ep_fee = ep.with_taker_fee(fee_rate)
    return {"price_type_in": ep.price_type, "value_in": ep.value,
            "price_type_out": ep_fee.price_type, "value_out": ep_fee.value,
            "fee_delta": ep_fee.value - ep.value}


def _build_rows(artifact: dict, city_filter: str | None, fee_rate: float) -> list[dict]:
    rows = []
    for tc in artifact.get("trade_cases", []):
        city = tc.get("city", "")
        if city_filter and city_filter.lower() not in city.lower():
            continue
        target_date = tc.get("target_date", "")
        bin_label = tc.get("range_label", "")
        direction = tc.get("direction", "")
        p_posterior = tc.get("p_posterior")
        entry_price_float = tc.get("entry_price")
        bin_labels = tc.get("bin_labels", [])
        p_market_vec = tc.get("p_market_vector", [])
        p_cal_vec = tc.get("p_cal_vector", [])

        # Resolve per-bin p_market and p_cal from vectors
        try:
            bin_idx = bin_labels.index(bin_label)
            p_market = p_market_vec[bin_idx] if bin_idx < len(p_market_vec) else None
            p_cal = p_cal_vec[bin_idx] if bin_idx < len(p_cal_vec) else None
        except (ValueError, IndexError):
            p_market = None
            p_cal = None

        if entry_price_float is None:
            continue

        seam = _reconstruct_seam(entry_price_float, fee_rate)
        rows.append({
            "city": city,
            "target_date": target_date,
            "bin_label": bin_label,
            "direction": direction,
            "p_posterior": p_posterior,
            "p_cal": p_cal,
            "p_market": p_market,
            "entry_price_raw": entry_price_float,
            "price_type_in": seam["price_type_in"],
            "price_type_out": seam["price_type_out"],
            "value_in": seam["value_in"],
            "value_out": seam["value_out"],
            "fee_delta": seam["fee_delta"],
        })
    return rows


def _print_table(rows: list[dict], cycle_id: int, started_at: str, fee_rate: float,
                 fee_rate_source: str) -> None:
    d5_count = sum(1 for r in rows if r["price_type_in"] == "implied_probability")

    print(f"\n=== D5 Coercion Seam Audit | cycle_id={cycle_id} | started={started_at[:19]}Z ===")
    print(f"fee_rate={fee_rate} (source: {fee_rate_source})")
    print(f"Bins audited: {len(rows)}  |  D5 defect count (price_type_in=implied_probability): {d5_count}/{len(rows)}")
    if len(rows) == 0:
        print("  (no trade_cases with entry_price in this cycle)")
        return

    print()
    hdr = f"{'city':<18} {'target':<12} {'dir':<9} {'p_post':>7} {'p_cal':>7} {'p_mkt':>7} "
    hdr += f"{'ep_in':>10} {'type_in':<22} {'type_out':<14} {'ep_out':>10} {'fee_d':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        city_s = (r["city"] or "")[:17]
        tdate_s = str(r.get("target_date", ""))[:12]
        dir_s = (r["direction"] or "")[:8]
        ppost_s = f"{r['p_posterior']:.4f}" if r["p_posterior"] is not None else "  N/A"
        pcal_s = f"{r['p_cal']:.4f}" if r["p_cal"] is not None else "  N/A"
        pmkt_s = f"{r['p_market']:.4f}" if r["p_market"] is not None else "  N/A"
        ep_in_s = f"{r['value_in']:.6f}"
        type_in_s = (r["price_type_in"] or "")[:21]
        type_out_s = (r["price_type_out"] or "")[:13]
        ep_out_s = f"{r['value_out']:.6f}"
        fee_d_s = f"{r['fee_delta']:+.6f}"
        print(f"{city_s:<18} {tdate_s:<12} {dir_s:<9} {ppost_s:>7} {pcal_s:>7} {pmkt_s:>7} "
              f"{ep_in_s:>10} {type_in_s:<22} {type_out_s:<14} {ep_out_s:>10} {fee_d_s:>8}")


def _write_csv(rows: list[dict], output_dir: Path, cycle_id: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"audit_market_price_semantics_{ts}.csv"
    fields = ["city", "target_date", "bin_label", "direction", "p_posterior",
              "p_cal", "p_market", "entry_price_raw", "price_type_in",
              "price_type_out", "value_in", "value_out", "fee_delta"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only forensic audit: D5 coercion seam in zeus_trades.db decision_log."
    )
    parser.add_argument("--cycle-id", type=int, default=None,
                        help="decision_log.id to audit (default: latest row)")
    parser.add_argument("--city", default=None,
                        help="Filter by city name (case-insensitive substring)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max rows to print (default: 50)")
    parser.add_argument("--output-dir", default="docs/research",
                        help="Directory for CSV output (default: docs/research)")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (default: canonical get_trade_connection_read_only()). "
                             "Use live state/zeus_trades.db when running from a worktree.")
    args = parser.parse_args()

    fee_rate, fee_rate_source = _load_fee_rate()
    conn = _get_conn(args.db_path)

    result = _fetch_cycle(conn, args.cycle_id)
    if result is None:
        print("No decision_log rows found. Pass --db-path to point at live state/zeus_trades.db.")
        sys.exit(0)

    cycle_id, started_at, artifact = result
    rows = _build_rows(artifact, args.city, fee_rate)
    rows = rows[: args.limit]

    _print_table(rows, cycle_id, started_at, fee_rate, fee_rate_source)

    output_dir = Path(args.output_dir)
    csv_path = _write_csv(rows, output_dir, cycle_id)
    print(f"\nCSV: {csv_path}")


if __name__ == "__main__":
    main()
