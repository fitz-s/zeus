#!/usr/bin/env python3
# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~05:00Z (cycle policy + 06/18Z deep offline
#   investigation), docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md §OPERATOR
#   DIRECTIVES + K4.0b(d). PURE OFFLINE study: live DBs READ-ONLY, no daemon/flag/src-live edits.
#   Writes ONLY to a scratch DB (state/cycle_phase_study.db). NEVER live zeus-forecasts.db.
"""Offline 06Z/18Z cycle-phase qualification study.

Settlement-grades cycle-phase quality for SETTLED past targets across all four model-cycle
phases (00/06/12/18Z) from a SCRATCH database.

Stages (sub-commands):
  hydrate     - copy the live read-only substrate (raw_model_forecasts, raw_forecast_artifacts,
                source_run_coverage, source_run, market_events, settlement_outcomes) into the
                scratch DB, preserving primary-key IDs (the materializer identity gates check
                artifact_id + raw_model_forecast natural keys against these rows).
  grade       - join scratch posteriors to VERIFIED settlement truth and emit per-phase metrics.

The AIFS-backed `backfill`/`materialize` stages (built around
`src/data/ecmwf_aifs_*`/`src/strategy/ecmwf_aifs_*`) were removed 2026-07-20 per the AIFS
banned-source deletion order (docs/evidence/capital_efficiency_2026_07_19/
banned_source_deletion_audit.md): they already called a `ReplacementForecastMaterializeRequest`
constructor shape (`aifs_extraction=`/`aifs_source_run_id=`/`anchor_weight=`/`anchor_sigma_c=`)
that no longer exists on the live dataclass, so they were dead code before this edit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIVE_FORECASTS_DB = ROOT / "state" / "zeus-forecasts.db"
LIVE_TRADES_DB = ROOT / "state" / "zeus_trades.db"
SCRATCH_DB = ROOT / "state" / "cycle_phase_study.db"

# Tables copied verbatim from the live forecasts DB (read-only) into the scratch DB. IDs are
# preserved so the materializer's artifact-identity + fusion natural-key lookups resolve.
_COPY_TABLES = (
    "raw_model_forecasts",
    "raw_forecast_artifacts",
    "source_run_coverage",
    "source_run",
    "market_events",
    "settlement_outcomes",
)

_PHASES = (0, 6, 12, 18)
_SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"


def _vlog(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# STAGE: hydrate
# ---------------------------------------------------------------------------
def hydrate(scratch_db: Path, *, live_db: Path, force: bool) -> dict[str, object]:
    if scratch_db.exists() and not force:
        raise SystemExit(f"{scratch_db} exists; pass --force to rebuild")
    if scratch_db.exists():
        scratch_db.unlink()
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(scratch_db))
    counts: dict[str, int] = {}
    try:
        conn.execute(f"ATTACH DATABASE 'file:{live_db}?mode=ro' AS live")
        for table in _COPY_TABLES:
            # Copy the EXACT schema (CREATE TABLE + its UNIQUE/PK constraints + indices) from live,
            # then bulk-INSERT the rows. CREATE ... AS SELECT would drop the UNIQUE constraints, and
            # write_manifest_to_db's INSERT ... ON CONFLICT requires the raw_forecast_artifacts
            # natural-key UNIQUE index to exist (the backfill stage writes new artifact rows).
            create_sql = conn.execute(
                "SELECT sql FROM live.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            conn.execute(create_sql)
            conn.execute(f"INSERT INTO {table} SELECT * FROM live.{table}")
            # Recreate the table's indices (including UNIQUE ones the ON CONFLICT target needs).
            for (idx_sql,) in conn.execute(
                "SELECT sql FROM live.sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            ).fetchall():
                try:
                    conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass  # auto-created/duplicate index names — skip
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = int(n)
            _vlog(f"  copied {table}: {n} rows")
        conn.commit()
        conn.execute("DETACH DATABASE live")
        # Helpful indices for the materializer's natural-key reads (live has them; the copy does not).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rmf_nat ON raw_model_forecasts(city, metric, target_date, source_cycle_time, endpoint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rfa_artifact ON raw_forecast_artifacts(artifact_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_me_nat ON market_events(city, target_date, temperature_metric)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_so_nat ON settlement_outcomes(city, target_date, temperature_metric, authority)"
        )
        # Initialise the replacement-forecast write schema (posteriors / anchors / readiness)
        # on the scratch DB so materialization has a place to write.
        from src.state.db import _create_readiness_state  # noqa: PLC0415
        from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: PLC0415

        ensure_replacement_forecast_live_schema(conn)
        _create_readiness_state(conn)
        conn.commit()
    finally:
        conn.close()
    return {"status": "HYDRATED", "scratch_db": str(scratch_db), "row_counts": counts}


# ---------------------------------------------------------------------------
# STAGE: grade
# ---------------------------------------------------------------------------
import math  # noqa: E402

_TS = 0.03  # taker edge threshold (operator: buy_no clears at ts=0.03)


def _settled_bin_qkey(market_bins: list[dict], settlement_value: float) -> str | None:
    """Map a settled numeric value to the q-key (range_label) of the bin that contains it."""
    for b in market_bins:
        lo = b["range_low"]
        hi = b["range_high"]
        if lo is not None and settlement_value < float(lo):
            continue
        if hi is not None and settlement_value > float(hi):
            continue
        return str(b["range_label"])
    return None


def _phase_of(cyc_iso: str) -> int:
    return datetime.fromisoformat(cyc_iso).astimezone(UTC).hour


def _mad_sigma(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    med = sorted(values)[len(values) // 2]
    mad = sorted(abs(v - med) for v in values)[len(values) // 2]
    return 1.4826 * mad


def grade(scratch_db: Path, *, trades_db: Path) -> dict[str, object]:
    """Settlement-grade scratch posteriors by cycle phase on identical family-days.

    Metrics per phase: (a) certified-bounds coverage (settled bin's q in [q_lcb,q_ucb]),
    (b) LogLoss of q on settled bin, (c) modal-bin hit rate, (d) simulated after-cost buy_no
    win-rate where the certified edge cleared ts=0.03 (fee = 0.05*p*(1-p)*shares), (e) fused-center
    residual (settled - mu) mean + MAD-sigma. Paired: a (city,target,metric) cell contributes to a
    phase only if it materialized for that phase; the paired set per metric is the cells present in
    ALL graded phases (reported separately as the paired cohort).
    """
    conn = sqlite3.connect(str(scratch_db))
    conn.row_factory = sqlite3.Row
    trade_conn = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True)
    trade_conn.row_factory = sqlite3.Row
    per_phase: dict[int, dict] = {p: {"cells": []} for p in _PHASES}
    try:
        rows = conn.execute(
            """
            SELECT p.posterior_id, p.city, p.target_date, p.temperature_metric, p.source_cycle_time,
                   p.q_json, p.q_lcb_json, p.q_ucb_json, p.provenance_json
            FROM forecast_posteriors p
            WHERE p.source_id = ?
            ORDER BY p.city, p.target_date, p.temperature_metric, p.source_cycle_time
            """,
            (_SOURCE_ID,),
        ).fetchall()
        for r in rows:
            phase = _phase_of(str(r["source_cycle_time"]))
            if phase not in per_phase:
                continue
            settle = conn.execute(
                "SELECT settlement_value, winning_bin FROM settlement_outcomes WHERE city=? AND target_date=? AND temperature_metric=? AND authority='VERIFIED' LIMIT 1",
                (r["city"], r["target_date"], r["temperature_metric"]),
            ).fetchone()
            if settle is None or settle["settlement_value"] is None:
                continue
            settle_val = float(settle["settlement_value"])
            mkt = conn.execute(
                "SELECT range_label, range_low, range_high FROM market_events WHERE city=? AND target_date=? AND temperature_metric=? AND token_id IS NOT NULL ORDER BY COALESCE(range_low,-999),COALESCE(range_high,999)",
                (r["city"], r["target_date"], r["temperature_metric"]),
            ).fetchall()
            mkt_bins = [dict(m) for m in mkt]
            settled_qkey = _settled_bin_qkey(mkt_bins, settle_val)
            if settled_qkey is None:
                continue
            q = json.loads(r["q_json"] or "{}")
            if settled_qkey not in q:
                continue
            prov = json.loads(r["provenance_json"] or "{}")
            q_settled = float(q[settled_qkey])
            modal_key = max(q, key=lambda k: q[k]) if q else None
            # The fused center mu (bayes_precision_fusion.anchor_value_c) is in CELSIUS; settlement_value is in
            # the city's settlement unit (F for US cities). Convert settle_val to C so the residual
            # (settled - mu) is a true degC error, not a unit-mismatch artifact.
            from src.config import cities_by_name as _cbn  # noqa: PLC0415
            _su = str(getattr(_cbn.get(r["city"]), "settlement_unit", "C") or "C").upper()
            settle_val_c = (settle_val - 32.0) * 5.0 / 9.0 if _su == "F" else settle_val
            cell = {
                "city": r["city"],
                "target_date": r["target_date"],
                "metric": r["temperature_metric"],
                "settle_val": settle_val,
                "settle_val_c": settle_val_c,
                "settled_qkey": settled_qkey,
                "q_settled": q_settled,
                "modal_hit": int(modal_key == settled_qkey),
                "logloss": -math.log(max(1e-12, min(1.0, q_settled))),
                "q_mode": prov.get("replacement_q_mode"),
                "q_shape": prov.get("q_shape"),
                "mu": (prov.get("bayes_precision_fusion") or {}).get("anchor_value_c"),
            }
            # (a) bounds coverage. NOTE: the per-cell "settled bin's q within [lcb,ucb]" check is
            # VACUOUS-BY-CONSTRUCTION (the materializer clips q_lcb <= q_point <= q_ucb per bin),
            # so we also accumulate the MEANINGFUL aggregate bound-honesty rows: over all
            # (cell, bin) pairs, an honest certified band must straddle realized frequency:
            # mean(q_lcb) <= mean(y) <= mean(q_ucb) on any pre-registered subset.
            lcb = json.loads(r["q_lcb_json"] or "null")
            ucb = json.loads(r["q_ucb_json"] or "null")
            cell["bin_rows"] = []
            if isinstance(lcb, dict) and isinstance(ucb, dict) and settled_qkey in lcb and settled_qkey in ucb:
                cell["has_bounds"] = 1
                cell["covered"] = int(float(lcb[settled_qkey]) <= q_settled <= float(ucb[settled_qkey]))
                cell["q_lcb_settled"] = float(lcb[settled_qkey])
                for bk, qv in q.items():
                    if bk in lcb and bk in ucb:
                        cell["bin_rows"].append(
                            (1.0 if bk == settled_qkey else 0.0, float(qv), float(lcb[bk]), float(ucb[bk]))
                        )
            else:
                cell["has_bounds"] = 0
                cell["covered"] = None
                cell["q_lcb_settled"] = None
            # (d) buy_no win-rate: for each NON-settled bin, the certified no-edge = no_lcb - ask_no.
            # no_lcb = 1 - q_ucb(bin) (complement of the upper bound on YES). We need the executable
            # NO ask; approximate from YES top ask: ask_no = 1 - yes_bid ~ but we only have YES ask in
            # snapshots, so we use ask_no_proxy = 1 - (1 - yes_ask) = yes_ask is wrong. Instead grade
            # buy_no on the per-bin realized outcome: a buy_no on bin B wins iff settled != B. The edge
            # gate uses no_lcb vs the YES ask of bin B: buying NO(B) at price (1 - yes_ask_B) wins
            # (1/(1-yes_ask_B) - 1) per dollar if settled != B. We require certified no_lcb - (1-yes_ask) >= ts.
            cell["buy_no_trades"] = 0
            cell["buy_no_wins"] = 0
            cell["buy_no_pnl"] = 0.0
            if isinstance(ucb, dict):
                for b in mkt_bins:
                    bk = str(b["range_label"])
                    if bk == settled_qkey:
                        pass  # buying NO on the settled bin LOSES — still allowed if it cleared the gate
                    if bk not in ucb:
                        continue
                    q_ucb_b = float(ucb[bk])
                    no_lcb = 1.0 - q_ucb_b  # certified lower bound on P(not bin)
                    snap = _yes_ask_snapshot(trade_conn, conn, city=r["city"], target_date=r["target_date"], metric=r["temperature_metric"], range_label=bk)
                    if snap is None:
                        continue
                    yes_ask = snap
                    no_ask = 1.0 - yes_ask
                    if no_ask <= 0.0 or no_ask >= 1.0:
                        continue
                    edge = no_lcb - no_ask
                    if edge < _TS:
                        continue
                    # DIRECTION LAW: buy_no only when bin != forecast modal (the favorite-longshot harvest).
                    if bk == modal_key:
                        continue
                    cell["buy_no_trades"] += 1
                    shares = 1.0
                    fee = 0.05 * no_ask * (1.0 - no_ask) * shares
                    if settle_val_not_in_bin(b, settle_val):
                        cell["buy_no_wins"] += 1
                        cell["buy_no_pnl"] += shares * (1.0 / no_ask - 1.0) - fee
                    else:
                        cell["buy_no_pnl"] += -shares - fee
            per_phase[phase]["cells"].append(cell)
    finally:
        conn.close()
        trade_conn.close()
    return _summarize_phases(per_phase)


def settle_val_not_in_bin(bin_row: dict, settle_val: float) -> bool:
    lo = bin_row["range_low"]
    hi = bin_row["range_high"]
    inside = (lo is None or settle_val >= float(lo)) and (hi is None or settle_val <= float(hi))
    return not inside


def _yes_ask_snapshot(trade_conn, fcst_conn, *, city, target_date, metric, range_label) -> float | None:
    """Best executable YES ask for (city,target,metric,bin) at the pre-day decision time.

    Resolves the market_slug + condition_id from the forecast DB market_events, then reads the
    latest executable_market_snapshots YES ask before the target local day. Returns a float in
    (0,1) or None if no executable ask exists.
    """
    me = fcst_conn.execute(
        "SELECT market_slug, condition_id FROM market_events WHERE city=? AND target_date=? AND temperature_metric=? AND range_label=? AND condition_id IS NOT NULL LIMIT 1",
        (city, target_date, metric, range_label),
    ).fetchone()
    if me is None:
        return None
    # Use end-of-prior-day as decision cutoff (pre-day decision regime; coarse but consistent).
    cutoff = f"{target_date}T00:00:00+00:00"
    rows = trade_conn.execute(
        """
        SELECT orderbook_top_ask FROM executable_market_snapshots
        WHERE event_slug=? AND condition_id=? AND outcome_label='YES' AND captured_at <= ?
        ORDER BY captured_at DESC LIMIT 20
        """,
        (me["market_slug"], me["condition_id"], cutoff),
    ).fetchall()
    for row in rows:
        v = row["orderbook_top_ask"]
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 < f < 1.0:
            return f
    return None


def _summarize_phases(per_phase: dict[int, dict]) -> dict[str, object]:
    out: dict[str, object] = {"phases": {}}
    # paired family-day cells: keys present in every phase that has any cells.
    keysets = {}
    for p, d in per_phase.items():
        keysets[p] = {(c["city"], c["target_date"], c["metric"]) for c in d["cells"]}
    nonempty = [p for p in _PHASES if keysets[p]]
    paired = set.intersection(*[keysets[p] for p in nonempty]) if nonempty else set()
    out["paired_cell_keys_n"] = len(paired)
    out["paired_phases"] = nonempty
    for p in _PHASES:
        cells = per_phase[p]["cells"]
        paired_cells = [c for c in cells if (c["city"], c["target_date"], c["metric"]) in paired]
        fused_cells = [c for c in cells if c["has_bounds"]]
        # Aggregate bound-honesty over all (cell, bin) rows of fused cells: mean realized outcome
        # vs the mean certified band. An honest band straddles mean(y).
        bin_rows = [row for c in fused_cells for row in c.get("bin_rows", [])]
        if bin_rows:
            mean_y = sum(r[0] for r in bin_rows) / len(bin_rows)
            mean_q = sum(r[1] for r in bin_rows) / len(bin_rows)
            mean_lcb = sum(r[2] for r in bin_rows) / len(bin_rows)
            mean_ucb = sum(r[3] for r in bin_rows) / len(bin_rows)
            bound_honesty = {
                "n_bin_rows": len(bin_rows),
                "mean_y": mean_y,
                "mean_q": mean_q,
                "mean_lcb": mean_lcb,
                "mean_ucb": mean_ucb,
                "band_straddles_reality": bool(mean_lcb <= mean_y <= mean_ucb),
            }
        else:
            bound_honesty = {"n_bin_rows": 0}
        out["phases"][f"{p:02d}Z"] = {
            "n_all": len(cells),
            "n_paired": len(paired_cells),
            "n_fused": len(fused_cells),
            **_metrics_for(cells, "all"),
            **{f"paired_{k}": v for k, v in _metrics_for(paired_cells, "paired").items()},
            **{f"fused_{k}": v for k, v in _metrics_for(fused_cells, "fused").items()},
            "bound_honesty": bound_honesty,
        }
    # Pairwise FUSED-vs-FUSED comparison on common cells (the substrate-fair phase comparison:
    # the all-phase strict pairing compares single-anchor 00/06Z q against fused 12/18Z q, which
    # confounds cycle phase with capture substrate; fused-common pairs remove that confound).
    fused_keys = {
        p: {(c["city"], c["target_date"], c["metric"]): c for c in per_phase[p]["cells"] if c["has_bounds"]}
        for p in _PHASES
    }
    pairwise: dict[str, object] = {}
    for i, a in enumerate(_PHASES):
        for b in _PHASES[i + 1:]:
            common = sorted(set(fused_keys[a]) & set(fused_keys[b]))
            if not common:
                pairwise[f"{a:02d}Z_vs_{b:02d}Z"] = {"n": 0}
                continue
            ca = [fused_keys[a][k] for k in common]
            cb = [fused_keys[b][k] for k in common]
            pairwise[f"{a:02d}Z_vs_{b:02d}Z"] = {
                "n": len(common),
                f"logloss_{a:02d}Z": sum(c["logloss"] for c in ca) / len(ca),
                f"logloss_{b:02d}Z": sum(c["logloss"] for c in cb) / len(cb),
                "logloss_delta_b_minus_a": (sum(c["logloss"] for c in cb) - sum(c["logloss"] for c in ca)) / len(ca),
                f"modal_{a:02d}Z": sum(c["modal_hit"] for c in ca) / len(ca),
                f"modal_{b:02d}Z": sum(c["modal_hit"] for c in cb) / len(cb),
                "logloss_win_b": sum(1 for x, y in zip(ca, cb) if y["logloss"] < x["logloss"]),
                "logloss_win_a": sum(1 for x, y in zip(ca, cb) if x["logloss"] < y["logloss"]),
            }
    out["pairwise_fused"] = pairwise
    return out


def _metrics_for(cells: list[dict], _tag: str) -> dict:
    if not cells:
        return {"coverage": None, "logloss": None, "modal_hit_rate": None,
                "buy_no_trades": 0, "buy_no_winrate": None, "buy_no_pnl": 0.0,
                "resid_mean": None, "resid_mad_sigma": None, "n_bounds": 0}
    bounded = [c for c in cells if c["has_bounds"]]
    cov = [c["covered"] for c in bounded if c["covered"] is not None]
    resids = [c["settle_val_c"] - c["mu"] for c in cells if c.get("mu") is not None and c.get("settle_val_c") is not None]
    tno = sum(c["buy_no_trades"] for c in cells)
    wno = sum(c["buy_no_wins"] for c in cells)
    pnl = sum(c["buy_no_pnl"] for c in cells)
    return {
        "coverage": (sum(cov) / len(cov)) if cov else None,
        "n_bounds": len(bounded),
        "logloss": sum(c["logloss"] for c in cells) / len(cells),
        "modal_hit_rate": sum(c["modal_hit"] for c in cells) / len(cells),
        "buy_no_trades": tno,
        "buy_no_winrate": (wno / tno) if tno else None,
        "buy_no_pnl": pnl,
        "resid_mean": (sum(resids) / len(resids)) if resids else None,
        "resid_mad_sigma": _mad_sigma(resids) if len(resids) >= 2 else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_h = sub.add_parser("hydrate")
    p_h.add_argument("--force", action="store_true")
    p_h.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_g = sub.add_parser("grade")
    p_g.add_argument("--scratch-db", type=Path, default=SCRATCH_DB)
    p_g.add_argument("--trades-db", type=Path, default=LIVE_TRADES_DB)
    p_g.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.cmd == "hydrate":
        out = hydrate(args.scratch_db, live_db=LIVE_FORECASTS_DB, force=args.force)
    elif args.cmd == "grade":
        out = grade(args.scratch_db, trades_db=args.trades_db)
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(out, sort_keys=True, indent=2, default=str) + "\n")
    else:
        parser.error("unknown command")
    print(json.dumps(out, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
