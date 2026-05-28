# Created: 2026-05-27
# Last reused or audited: 2026-05-27 (Wave 7 verification rewrite)
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md
#   Wave 1: original D5 forensic audit that PROVED the fabrication count on live
#           data (1/1 on cycle 1580; CSV in docs/research/).
#   Wave 7: dual-mode rewrite. After Wave 2 removed the fabrication at
#           evaluator.py:_size_at_execution_price_boundary, the original
#           reconstruction-based audit always emitted "implied_probability"
#           because the script BUILT one that way — so re-running it post-
#           Wave-2 did NOT show the fix had landed. This rewrite reads the
#           current state of src/engine/evaluator.py + src/strategy/market_analysis.py
#           and verifies the fabrication is structurally absent, then prints
#           the per-bin trade-case table for visual confirmation.
#
# READ-ONLY. No runtime DB writes. SELECT-only on decision_log.

"""Wave 7 verification audit: market-cost seam status + cycle trade-cases.

Two modes:

  --mode source-check (default):  static check on src/engine/evaluator.py +
      src/strategy/market_analysis.py — passes when the D5 fabrication
      pattern (``ExecutionPrice(price_type="implied_probability", ...)``
      followed by ``with_taker_fee()``) is NOT present at the Kelly
      boundary AND find_edges constructs typed ExecutionPrice (Wave 2
      contract). Exits 0 on pass, 1 on detected regression.

  --mode trade-cases: dumps the per-bin trade-case table from the
      requested decision_log row + writes CSV to docs/research/. Useful
      for spot-checking real cycle values; does NOT itself prove the
      Wave-2 fix landed (the trade_cases dict-pack stores
      float(edge.entry_price) per Wave-2's JSON-boundary coercion, so
      the value is identical pre/post-Wave-2 — only the upstream type
      moved).

  --mode both: source-check first; if it passes, dump trade-cases.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root on sys.path so src.* imports work.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

_FEE_RATE_DEFAULT = 0.05
_FEE_RATE_SOURCE = "fallback(0.05)"


# ----------------------------------------------------------------------------
# Source-code static check (Wave 7 verification)
# ----------------------------------------------------------------------------

# The D5 defect pattern: an ExecutionPrice constructed with
# price_type="implied_probability" immediately followed by .with_taker_fee()
# inside _size_at_execution_price_boundary. After Wave 2 the boundary is
# allowed to construct implied_probability ONLY as a legacy-float-fallback
# branch (caller is a bare float, not a typed BinEdge). The unconditional
# fabrication is what's forbidden.

_EVALUATOR_PATH = _REPO_ROOT / "src" / "engine" / "evaluator.py"
_MARKET_ANALYSIS_PATH = _REPO_ROOT / "src" / "strategy" / "market_analysis.py"


def _check_evaluator_seam_fabrication(text: str) -> tuple[bool, list[str]]:
    """Return (ok, evidence_lines). ok=True iff the unconditional fabrication is absent.

    Wave 2 contract: the boundary may still construct implied_probability for
    bare-float callers but ONLY inside an explicit else branch of an isinstance
    check. The forbidden pattern is unconditional fabrication.
    """
    findings: list[str] = []
    # Scan _size_at_execution_price_boundary for the legacy unconditional pattern.
    func_match = re.search(
        r"def\s+_size_at_execution_price_boundary[\s\S]+?(?=^\s*def\s|\Z)",
        text, re.MULTILINE,
    )
    if not func_match:
        findings.append("could not locate _size_at_execution_price_boundary")
        return False, findings
    body = func_match.group(0)

    has_isinstance_guard = "isinstance(entry_price, ExecutionPrice)" in body
    # The forbidden pattern is fabrication WITHOUT the isinstance guard. The
    # presence of the guard means legacy floats are correctly branched separately.
    if not has_isinstance_guard:
        findings.append(
            "FAIL: _size_at_execution_price_boundary missing `isinstance(entry_price, ExecutionPrice)` guard "
            "— D5 fabrication is unconditional."
        )
        return False, findings

    findings.append(
        "PASS: _size_at_execution_price_boundary has isinstance(entry_price, ExecutionPrice) guard; "
        "legacy float coercion is in the explicit else branch."
    )
    return True, findings


def _check_find_edges_typed_construction(text: str) -> tuple[bool, list[str]]:
    """Return (ok, evidence_lines). ok=True iff find_edges constructs typed ExecutionPrice."""
    findings: list[str] = []
    if "ExecutionPrice(" not in text:
        findings.append("FAIL: market_analysis.py does not import / use ExecutionPrice")
        return False, findings
    # Must construct ExecutionPrice with price_type="vwmp" (Wave 2 contract).
    vwmp_constructions = re.findall(
        r"ExecutionPrice\(\s*[\s\S]{0,200}?price_type\s*=\s*[\"']vwmp[\"']",
        text,
    )
    if len(vwmp_constructions) < 2:
        findings.append(
            f"FAIL: find_edges does not construct ExecutionPrice(price_type='vwmp') "
            f"for both buy_yes and buy_no (found {len(vwmp_constructions)} sites; expect >= 2)."
        )
        return False, findings
    findings.append(
        f"PASS: market_analysis.py find_edges constructs typed ExecutionPrice(price_type='vwmp') "
        f"at {len(vwmp_constructions)} sites (buy_yes + buy_no)."
    )
    return True, findings


def _run_source_check() -> int:
    """Run all Wave 7 static checks. Returns process exit code (0 pass, 1 fail)."""
    print("\n=== Wave 7 verification: market-cost seam source-code static check ===\n")
    overall_ok = True

    try:
        ev_text = _EVALUATOR_PATH.read_text()
    except OSError as exc:
        print(f"  FAIL: could not read {_EVALUATOR_PATH}: {exc}")
        return 1
    try:
        ma_text = _MARKET_ANALYSIS_PATH.read_text()
    except OSError as exc:
        print(f"  FAIL: could not read {_MARKET_ANALYSIS_PATH}: {exc}")
        return 1

    print(f"[1/2] {_EVALUATOR_PATH.relative_to(_REPO_ROOT)}: D5 boundary fabrication check")
    ok, lines = _check_evaluator_seam_fabrication(ev_text)
    overall_ok = overall_ok and ok
    for line in lines:
        print(f"      {line}")

    print(f"\n[2/2] {_MARKET_ANALYSIS_PATH.relative_to(_REPO_ROOT)}: find_edges typed-ExecutionPrice construction")
    ok, lines = _check_find_edges_typed_construction(ma_text)
    overall_ok = overall_ok and ok
    for line in lines:
        print(f"      {line}")

    print()
    if overall_ok:
        print("OVERALL: PASS — Wave 2 D5 seam fix is structurally present.")
        return 0
    print("OVERALL: FAIL — D5 regression detected (see lines above).")
    return 1


# ----------------------------------------------------------------------------
# Trade-cases table mode (informational; reads decision_log)
# ----------------------------------------------------------------------------

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
        # K5#2 fix (Copilot review of PR #348): force SQLite URI read-only
        # mode so the --db-path override matches the script's READ-ONLY
        # contract. A normal sqlite3.connect can take write locks and
        # create WAL sidecars on the operator's live state/zeus_trades.db.
        import sqlite3
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=0",
            uri=True,
        )
        conn.row_factory = sqlite3.Row
        return conn
    from src.state.db import get_trade_connection_read_only
    import sqlite3 as _sqlite3
    conn = get_trade_connection_read_only()
    conn.row_factory = _sqlite3.Row
    return conn


def _fetch_cycle(conn, cycle_id: int | None):
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


def _build_rows(artifact: dict, city_filter: str | None) -> list[dict]:
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

        try:
            bin_idx = bin_labels.index(bin_label)
            p_market = p_market_vec[bin_idx] if bin_idx < len(p_market_vec) else None
            p_cal = p_cal_vec[bin_idx] if bin_idx < len(p_cal_vec) else None
        except (ValueError, IndexError):
            p_market = None
            p_cal = None

        if entry_price_float is None:
            continue

        rows.append({
            "city": city,
            "target_date": target_date,
            "bin_label": bin_label,
            "direction": direction,
            "p_posterior": p_posterior,
            "p_cal": p_cal,
            "p_market": p_market,
            "entry_price_json_value": entry_price_float,
            # Post-Wave-2 note: the entry_price stored in decision_log is the
            # float-coerced value (see cycle_runtime.py:~5832 +
            # NoTradeCase.market_price). The TYPED ExecutionPrice + provenance
            # only exists in-memory on the live BinEdge; the JSON-serialised
            # decision_log retains only the numeric value (which equals the
            # pre-Wave-2 stored value — Wave 2 was purely additive on the
            # in-memory type). The static source-check (--mode source-check)
            # is the right verification surface for the typed boundary.
            "note": "json_value == in_memory_ep.value (Wave 2: type-bump in-memory only)",
        })
    return rows


def _print_table(rows: list[dict], cycle_id: int, started_at: str, fee_rate: float, fee_rate_source: str) -> None:
    print(f"\n=== Trade Cases | cycle_id={cycle_id} | started={started_at[:19]}Z ===")
    print(f"fee_rate={fee_rate} (source: {fee_rate_source})")
    print(f"Bins audited: {len(rows)}")
    if not rows:
        print("  (no trade_cases with entry_price in this cycle)")
        return
    print()
    hdr = f"{'city':<18} {'target':<12} {'dir':<9} {'p_post':>7} {'p_cal':>7} {'p_mkt':>7} {'ep_value':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ppost_s = f"{r['p_posterior']:.4f}" if r["p_posterior"] is not None else "  N/A"
        pcal_s = f"{r['p_cal']:.4f}" if r["p_cal"] is not None else "  N/A"
        pmkt_s = f"{r['p_market']:.4f}" if r["p_market"] is not None else "  N/A"
        ep_s = f"{r['entry_price_json_value']:.6f}"
        city_s = (r["city"] or "")[:17]
        tdate_s = str(r.get("target_date", ""))[:12]
        dir_s = (r["direction"] or "")[:8]
        print(f"{city_s:<18} {tdate_s:<12} {dir_s:<9} {ppost_s:>7} {pcal_s:>7} {pmkt_s:>7} {ep_s:>10}")


def _write_csv(rows: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"audit_market_price_semantics_{ts}.csv"
    fields = ["city", "target_date", "bin_label", "direction", "p_posterior",
              "p_cal", "p_market", "entry_price_json_value", "note"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wave 7 verification audit: market-cost seam source check + trade-cases."
    )
    parser.add_argument("--mode", choices=("source-check", "trade-cases", "both"),
                        default="source-check",
                        help="source-check (default): static src/ check; "
                             "trade-cases: per-bin table from decision_log; "
                             "both: source check then trade-cases.")
    parser.add_argument("--cycle-id", type=int, default=None,
                        help="decision_log.id (default: latest) — only for trade-cases mode")
    parser.add_argument("--city", default=None,
                        help="Filter by city substring (trade-cases mode only)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max trade-case rows to print (default: 50)")
    parser.add_argument("--output-dir", default="docs/research",
                        help="CSV output directory (trade-cases mode)")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (default: canonical "
                             "get_trade_connection_read_only). Use live "
                             "state/zeus_trades.db when running from a worktree.")
    args = parser.parse_args()

    exit_code = 0

    if args.mode in ("source-check", "both"):
        exit_code = _run_source_check()
        if args.mode == "source-check":
            sys.exit(exit_code)

    if args.mode in ("trade-cases", "both"):
        if exit_code != 0:
            print("\nSkipping trade-cases dump because source check failed.")
            sys.exit(exit_code)
        fee_rate, fee_rate_source = _load_fee_rate()
        conn = _get_conn(args.db_path)
        result = _fetch_cycle(conn, args.cycle_id)
        if result is None:
            print("\nNo decision_log rows found. Pass --db-path to point at live state/zeus_trades.db.")
            sys.exit(0)
        cycle_id, started_at, artifact = result
        rows = _build_rows(artifact, args.city)
        rows = rows[: args.limit]
        _print_table(rows, cycle_id, started_at, fee_rate, fee_rate_source)
        output_dir = Path(args.output_dir)
        csv_path = _write_csv(rows, output_dir)
        print(f"\nCSV: {csv_path}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
