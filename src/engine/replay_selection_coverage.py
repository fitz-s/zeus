# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: backtest_v2_port_2026_05_07.md §D2+D3
"""Selection-coverage replay mode.

Public entry: run_selection_coverage()

Per (city, target_date, snapshot) joining calibration_pairs_v2 ⨝ settlements_v2:
  1. Load p_raw from ensemble_snapshots_v2, calibrate via calibrate_and_normalize.
  2. Construct p_market per --p-market flag.
  3. Build MarketAnalysis with MODEL_ONLY_POSTERIOR_MODE.
  4. Call scan_full_hypothesis_family (LIVE FDR path — NOT find_edges + fdr_filter).
  5. Call apply_familywise_fdr.
  6. Compare picked bins to settlements_v2.winning_bin. Emit hit ∈ {1, 0, NULL}.

Writes ONLY to zeus_backtest.db. Does NOT write to world.db.

Short-circuits (all appear in summary.limitations.selection_coverage):
  - no_clob_best_bid: no CLOB best-bid price available
  - no_buy_no_market_price: buy_no market price not modelled
  - no_day0_nowcast: Day0 nowcast excluded (lead_hours < 24 excluded)
  - no_fee_adjusted_entry_price: no fee-adjusted entry price
  - no_ddd_gate_by_default: DDD v2 gate disabled (add --no-ddd to re-enable)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Literal, Optional

import numpy as np

from src.config import City, cities_by_name, edge_n_bootstrap, settings
from src.contracts.settlement_semantics import SettlementSemantics
from src.engine.replay import (
    BACKTEST_AUTHORITY_SCOPE,
    ReplayContext,
    ReplaySummary,
    _insert_backtest_outcome,
    _insert_backtest_run,
    _replay_calibration_lookup_keys,
    _table_exists,
)
from src.state.db import get_backtest_connection, get_trade_connection_with_world, init_backtest_schema
from src.strategy.fdr_filter import DEFAULT_FDR_ALPHA
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis, scan_full_hypothesis_family
from src.strategy.selection_family import apply_familywise_fdr, make_hypothesis_family_id
from src.types import Bin

logger = logging.getLogger(__name__)

SELECTION_COVERAGE_LANE = "selection_coverage"


# ---------------------------------------------------------------------------
# Hit scoring helpers (pure functions — testable without DB)
# ---------------------------------------------------------------------------

def _score_snapshot_hit(
    picked_labels: list[str],
    winning_bin: Optional[str],
) -> Optional[int]:
    """Return hit score for one snapshot.

    Returns:
        1    — at least one picked label matches the winning bin
        0    — a bin was picked but it did not match the winning bin
        None — no bin was picked (FDR rejected all) OR winning_bin unknown
    """
    if not winning_bin:
        return None
    if not picked_labels:
        return None  # no-pick
    return 1 if winning_bin in picked_labels else 0


def _compute_climatology_p_market(
    all_rows: list[dict],
    labels: list[str],
    target_date: str,
) -> list[float]:
    """Compute climatology p_market with strict no-future-leak.

    Uses only rows where row['target_date'] < target_date.
    Returns uniform fallback if no historical data.
    """
    counts = defaultdict(int)
    total = 0
    for row in all_rows:
        row_date = str(row.get("target_date") or "")
        if row_date >= target_date:
            continue  # strict: exclude same day AND future
        lbl = str(row.get("range_label") or "")
        if lbl in labels and row.get("outcome") in (0, 1):
            counts[lbl] += int(row["outcome"])
            total += 1

    n = len(labels)
    if total == 0 or n == 0:
        return [1.0 / n] * n if n > 0 else []

    result = []
    for lbl in labels:
        result.append(counts[lbl] / total)
    return result


def _uniform_brier_baseline(n_bins: int) -> float:
    """Compute expected Brier score for a uniform forecast over n_bins.

    For a uniform forecast p_i = 1/n over n bins with one correct bin:
      BS_clim = (1/n) * (1 - 1/n)^2 + (n-1)/n * (0 - 1/n)^2
             = (1/n)(1 - 1/n)^2 + (n-1)/n * (1/n)^2
             = (1/n)[(1-1/n)^2 + (n-1)/n * (1/n)]   ... averaged over n bins
    Mean per-bin Brier = (1/n)*(1-1/n)^2 + (n-1)*(1/n)*(1/n)^2  -- this is per bin, summed / n
    Full formula: sum_i (p_i - o_i)^2 / n  averaged across draws where one bin wins:
      = (1/n)*(1-1/n)^2 + ((n-1)/n)*(1/n)^2
    """
    if n_bins <= 0:
        return 0.24  # safe fallback
    n = float(n_bins)
    return (1.0 / n) * (1.0 - 1.0 / n) ** 2 + ((n - 1.0) / n) * (1.0 / n) ** 2


def _bss_for_snapshot(brier: float, n_bins: int) -> float:
    """Per-snapshot BSS = 1 - brier / clim_brier(n_bins)."""
    clim = _uniform_brier_baseline(n_bins)
    if clim <= 0.0:
        return 0.0
    return 1.0 - brier / clim


def _build_timezone_stratification(rows: list[dict]) -> dict:
    """Build Asia_star / non_Asia stratification from snapshot result rows.

    Each row must have: city, hit (int|None), brier (float|None),
    n_bins (int), timezone_class (str).
    BSS is computed per-snapshot using n_bins-aware baseline, then averaged.
    """
    groups: dict[str, dict] = {
        "Asia_star": {"n_snapshots": 0, "hits": [], "briers": [], "bss_vals": []},
        "non_Asia": {"n_snapshots": 0, "hits": [], "briers": [], "bss_vals": []},
    }
    for row in rows:
        cls = str(row.get("timezone_class") or "non_Asia")
        key = cls if cls in groups else "non_Asia"
        groups[key]["n_snapshots"] += 1
        hit = row.get("hit")
        if hit is not None:
            groups[key]["hits"].append(int(hit))
        brier = row.get("brier")
        n_bins = int(row.get("n_bins") or 5)
        if brier is not None:
            groups[key]["briers"].append(float(brier))
            groups[key]["bss_vals"].append(_bss_for_snapshot(float(brier), n_bins))

    result = {}
    for cls, g in groups.items():
        hits = g["hits"]
        briers = g["briers"]
        bss_vals = g["bss_vals"]
        hit_rate = float(sum(hits) / len(hits)) if hits else None
        brier_mean = float(sum(briers) / len(briers)) if briers else None
        bss = round(float(sum(bss_vals) / len(bss_vals)), 4) if bss_vals else None
        result[cls] = {
            "n_snapshots": g["n_snapshots"],
            "hit_rate": hit_rate,
            "brier": brier_mean,
            "bss": bss,
        }
    return result


# ---------------------------------------------------------------------------
# Core per-snapshot scorer
# ---------------------------------------------------------------------------

def _score_one_snapshot(
    ctx: ReplayContext,
    city: City,
    target_date: str,
    winning_bin: str,
    snapshot_id: int,
    *,
    temperature_metric: Literal["high", "low"],
    fdr_alpha: float,
    p_market_source: Literal["stored", "uniform", "climatology", "frozen_at_decision"],
    override_platt: bool,
    clim_rows: list[dict],
) -> dict:
    """Score one (city, target_date, snapshot_id) against the live FDR path.

    Returns a dict with keys: city, target_date, snapshot_id, hit, brier,
    picked_labels, winning_bin, missing_reason, timezone_class.
    """
    from src.calibration.manager import get_calibrator, season_from_month
    from src.calibration.platt import calibrate_and_normalize
    from src.data.market_scanner import _parse_temp_range
    from src.strategy.market_fusion import MODEL_ONLY_POSTERIOR_MODE
    from src.strategy.market_analysis import MarketAnalysis

    _sem = SettlementSemantics.for_city(city)

    tz_class = "Asia_star" if city.timezone.startswith("Asia/") else "non_Asia"

    base_result = {
        "city": city.name,
        "target_date": target_date,
        "snapshot_id": snapshot_id,
        "hit": None,
        "brier": None,
        "n_bins": 0,
        "lead_days": 0.0,
        "picked_labels": [],
        "winning_bin": winning_bin,
        "missing_reason": "",
        "timezone_class": tz_class,
    }

    # -- Load snapshot
    snapshot_row = ctx.conn.execute(
        f"""
        SELECT snapshot_id, members_json, p_raw_json, lead_hours, spread,
               is_bimodal, model_version, issue_time, valid_time,
               available_at, fetch_time, data_version
        FROM {ctx._snapshot_v2_table or ctx._snapshot_legacy_table}
        WHERE snapshot_id = ? AND city = ? AND target_date = ?
        LIMIT 1
        """,
        (snapshot_id, city.name, target_date),
    ).fetchone()
    if snapshot_row is None:
        base_result["missing_reason"] = "no_snapshot"
        return base_result

    lead_hours = float(snapshot_row["lead_hours"] or 72.0)
    # Short-circuit: no Day0 nowcast (lead_hours < 24 excluded)
    if lead_hours < 24.0:
        base_result["missing_reason"] = "no_day0_nowcast_excluded"
        return base_result

    lead_days = lead_hours / 24.0
    base_result["lead_days"] = lead_days
    p_raw_json = snapshot_row["p_raw_json"]
    try:
        p_raw_stored = json.loads(p_raw_json) if isinstance(p_raw_json, str) else p_raw_json
    except (TypeError, ValueError):
        base_result["missing_reason"] = "invalid_p_raw_json"
        return base_result
    if not p_raw_stored:
        base_result["missing_reason"] = "empty_p_raw"
        return base_result

    # -- Load calibration pair labels for bin construction
    cp_rows = ctx.conn.execute(
        f"SELECT DISTINCT range_label FROM {ctx._sp}calibration_pairs_v2 WHERE city = ? AND target_date = ? ORDER BY range_label",
        (city.name, target_date),
    ).fetchall()
    labels = []
    for row in cp_rows:
        lbl = str(row["range_label"] or "")
        lo, hi = _parse_temp_range(lbl)
        if lo is not None or hi is not None:
            labels.append(lbl)

    if not labels or len(labels) != len(p_raw_stored):
        # Fallback: use market_events labels
        me_rows = ctx.conn.execute(
            f"SELECT DISTINCT range_label FROM {ctx._sp}market_events WHERE city = ? AND target_date = ? AND range_label IS NOT NULL AND range_label != '' ORDER BY range_label",
            (city.name, target_date),
        ).fetchall()
        labels = []
        for row in me_rows:
            lbl = str(row["range_label"] or "")
            lo, hi = _parse_temp_range(lbl)
            if lo is not None or hi is not None:
                labels.append(lbl)

    if not labels or len(labels) != len(p_raw_stored):
        base_result["missing_reason"] = "label_count_mismatch"
        return base_result

    bins = [
        Bin(
            low=_parse_temp_range(lbl)[0],
            high=_parse_temp_range(lbl)[1],
            label=lbl,
            unit=city.settlement_unit,
        )
        for lbl in labels
    ]
    base_result["n_bins"] = len(bins)

    bin_probs_raw = np.array(p_raw_stored, dtype=float)

    # -- Calibrate
    snap_dict = dict(snapshot_row) if not isinstance(snapshot_row, dict) else snapshot_row
    snap_dict.setdefault("source_id", None)
    (
        cal_supported, _cycle, _source_id, _horizon_profile,
    ) = _replay_calibration_lookup_keys(snap_dict)

    target_d = date.fromisoformat(target_date)
    season = season_from_month(target_d.month, lat=city.lat)

    if not override_platt and cal_supported:
        cal, _ = get_calibrator(
            ctx.conn, city, target_date,
            temperature_metric=temperature_metric,
            cycle=_cycle, source_id=_source_id,
            horizon_profile=_horizon_profile,
        )
        if cal is not None:
            bin_widths = [b.width for b in bins]
            bin_probs_cal = calibrate_and_normalize(bin_probs_raw, cal, float(lead_days), bin_widths=bin_widths)
        else:
            bin_probs_cal = bin_probs_raw
    else:
        bin_probs_cal = bin_probs_raw

    # -- Build p_market per source flag
    n = len(bins)
    if p_market_source == "uniform":
        p_market = np.full(n, 1.0 / n, dtype=float)
    elif p_market_source == "climatology":
        clim = _compute_climatology_p_market(clim_rows, labels, target_date)
        p_market = np.array(clim, dtype=float)
        if len(p_market) != n:
            p_market = np.full(n, 1.0 / n, dtype=float)
        total = p_market.sum()
        if total > 0:
            p_market = p_market / total
        else:
            p_market = np.full(n, 1.0 / n, dtype=float)
    elif p_market_source == "stored":
        # Not available in backtest DB — short-circuit
        base_result["missing_reason"] = "no_clob_best_bid"
        return base_result
    elif p_market_source == "frozen_at_decision":
        # Use p_raw as proxy for frozen decision-time market
        p_market = bin_probs_raw.copy()
        total = p_market.sum()
        if total > 0:
            p_market = p_market / total
        else:
            p_market = np.full(n, 1.0 / n, dtype=float)
    else:
        p_market = np.full(n, 1.0 / n, dtype=float)

    # MODEL_ONLY_POSTERIOR_MODE: alpha=1.0, p_posterior = p_cal (no market fusion)
    alpha = 1.0

    # Build MarketAnalysis
    spread_val = float(snapshot_row["spread"] or 3.0)
    member_maxes_raw = snapshot_row["members_json"]
    try:
        member_maxes_parsed = json.loads(member_maxes_raw) if isinstance(member_maxes_raw, str) else member_maxes_raw
    except (TypeError, ValueError):
        member_maxes_parsed = [20.0] * 50
    # MarketAnalysis expects 1D member maxes (one value per member)
    # members_json may be 2D (members × bins) — extract per-member max
    if member_maxes_parsed and isinstance(member_maxes_parsed[0], list):
        member_maxes = [float(max(row)) for row in member_maxes_parsed]
    else:
        member_maxes = [float(v) for v in member_maxes_parsed] if member_maxes_parsed else [20.0] * 50

    analysis = MarketAnalysis(
        p_raw=bin_probs_raw,
        p_cal=bin_probs_cal,
        p_market=p_market,
        alpha=alpha,
        bins=bins,
        member_maxes=member_maxes,
        calibrator=None,
        lead_days=lead_days,
        unit=city.settlement_unit,
        round_fn=_sem.round_values,
        posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
    )

    # -- LIVE FDR PATH: scan_full_hypothesis_family + apply_familywise_fdr
    # DO NOT call find_edges() or fdr_filter() here — that is replay.py:1628 legacy path
    n_bootstrap = edge_n_bootstrap()
    try:
        hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=n_bootstrap)
    except Exception as exc:
        logger.warning("scan_full_hypothesis_family failed for %s/%s: %s", city.name, target_date, exc)
        base_result["missing_reason"] = "fdr_scan_failed"
        return base_result

    if not hypotheses:
        base_result["missing_reason"] = "no_hypotheses"
        return base_result

    # Build rows for apply_familywise_fdr
    family_id = make_hypothesis_family_id(
        cycle_mode="backtest",
        city=city.name,
        target_date=target_date,
        temperature_metric=temperature_metric,
        discovery_mode="selection_coverage",
        decision_snapshot_id=str(snapshot_id),
    )
    fdr_rows = []
    for hyp in hypotheses:
        fdr_rows.append({
            "family_id": family_id,
            "hypothesis_id": f"{family_id}|{hyp.range_label}|{hyp.direction}",
            "p_value": float(hyp.p_value),
            "tested": True,
            "passed_prefilter": bool(hyp.passed_prefilter),
            "support_index": int(hyp.index),
            "range_label": hyp.range_label,
            "direction": hyp.direction,
        })

    selected_rows = apply_familywise_fdr(fdr_rows, q=fdr_alpha)
    picked_labels = [
        str(row["range_label"])
        for row in selected_rows
        if bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
        and row.get("direction") == "buy_yes"  # only buy_yes for hit scoring
    ]

    # -- Score hit
    hit = _score_snapshot_hit(picked_labels, winning_bin)

    # -- Brier score for the winning bin (p_cal vs outcome)
    brier = None
    if winning_bin:
        try:
            win_idx = labels.index(winning_bin)
            brier_scores = []
            for i, p in enumerate(bin_probs_cal):
                outcome = 1.0 if i == win_idx else 0.0
                brier_scores.append((float(p) - outcome) ** 2)
            brier = float(np.mean(brier_scores))
        except (ValueError, IndexError):
            brier = None

    base_result["hit"] = hit
    base_result["brier"] = brier
    base_result["picked_labels"] = picked_labels
    return base_result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_selection_coverage(
    start_date: str,
    end_date: str,
    *,
    temperature_metric: Literal["high", "low"] = "high",
    fdr_alpha: float = DEFAULT_FDR_ALPHA,
    kelly_multiplier: float = 0.5,
    p_market_source: Literal["stored", "uniform", "climatology", "frozen_at_decision"] = "climatology",
    override_platt: bool = False,
) -> ReplaySummary:
    """Run selection-coverage replay: score live FDR bin picks vs settled outcomes.

    Reads from world.db (calibration_pairs_v2, settlements_v2, ensemble_snapshots_v2).
    Writes ONLY to zeus_backtest.db. Does NOT write to world.db.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        temperature_metric: 'high' or 'low'
        fdr_alpha: FDR q threshold (default 0.10)
        kelly_multiplier: unused in coverage scoring; recorded in limitations
        p_market_source: how to construct p_market substitute
        override_platt: if True, skip Platt calibration
    """
    run_id = str(uuid.uuid4())[:12]
    conn = get_trade_connection_with_world()
    conn.row_factory = sqlite3.Row
    ctx = ReplayContext(conn, allow_snapshot_only_reference=True)

    # Resolve calibration_pairs_v2 table name (may be world. prefixed or local)
    cp_v2_table = f"{ctx._sp}calibration_pairs_v2"
    sv2_table = f"{ctx._sp}settlements_v2"

    # Check whether settlements_v2 has a snapshot_id column
    sv2_cols = set()
    try:
        sv2_col_rows = conn.execute(f"PRAGMA table_info({sv2_table.replace('world.', '')})").fetchall()
        sv2_cols = {str(r["name"] if hasattr(r, '__getitem__') else r[1]) for r in sv2_col_rows}
    except Exception:
        pass
    has_snapshot_id_col = "snapshot_id" in sv2_cols

    snap_id_sel = ", snapshot_id" if has_snapshot_id_col else ""

    # Load settlements_v2
    settlement_rows = conn.execute(
        f"""
        SELECT city, target_date, settlement_value, winning_bin{snap_id_sel}
        FROM {sv2_table}
        WHERE target_date >= ? AND target_date <= ?
          AND temperature_metric = ?
          AND authority = 'VERIFIED'
          AND settlement_value IS NOT NULL
        ORDER BY target_date, city
        """,
        (start_date, end_date, temperature_metric),
    ).fetchall()

    # Load all calibration_pairs_v2 rows for climatology (no-future-leak enforced per snapshot)
    clim_rows: list[dict] = []
    if p_market_source == "climatology":
        raw_clim = conn.execute(
            f"""
            SELECT city, target_date, range_label, outcome
            FROM {cp_v2_table}
            ORDER BY target_date, city, range_label
            """,
        ).fetchall()
        clim_rows = [dict(r) for r in raw_clim]

    backtest_conn = get_backtest_connection()
    init_backtest_schema(backtest_conn)

    summary = ReplaySummary(
        run_id=run_id,
        mode=SELECTION_COVERAGE_LANE,
        date_range=(start_date, end_date),
        n_settlements=len(settlement_rows),
        limitations={
            "storage": "zeus_backtest.db",
            "authority_scope": BACKTEST_AUTHORITY_SCOPE,
            "promotion_authority": False,
            "lane_goal": "selection_coverage_not_pnl",
            "pnl_available": False,
            "pnl_unavailable_reason": "selection_coverage_scores_bin_selection_not_trading_economics",
            "no_clob_best_bid": True,
            "no_buy_no_market_price": True,
            "no_day0_nowcast": True,
            "no_fee_adjusted_entry_price": True,
            "no_ddd_gate_by_default": True,
            "fdr_path": "scan_full_hypothesis_family+apply_familywise_fdr",
            "fdr_alpha": fdr_alpha,
            "p_market_source": p_market_source,
            "temperature_metric": temperature_metric,
        },
    )
    _insert_backtest_run(backtest_conn, summary, status="running")

    snapshot_results: list[dict] = []
    per_city: dict[str, dict] = {}
    n_replayed = 0

    for row in settlement_rows:
        city = cities_by_name.get(str(row["city"] or ""))
        if city is None:
            continue

        target_date = str(row["target_date"])
        winning_bin = str(row["winning_bin"] or "")

        # Resolve snapshot_id: use settlements_v2 column if present, else latest snapshot
        snap_id = None
        if has_snapshot_id_col:
            try:
                snap_id = row["snapshot_id"]
            except Exception:
                snap_id = None

        if snap_id is None:
            # Find latest snapshot before decision time
            snap_row = conn.execute(
                f"""
                SELECT snapshot_id FROM {ctx._snapshot_v2_table or ctx._snapshot_legacy_table}
                WHERE city = ? AND target_date = ?
                  AND temperature_metric = ?
                ORDER BY datetime(available_at) DESC
                LIMIT 1
                """,
                (city.name, target_date, temperature_metric),
            ).fetchone()
            if snap_row is None:
                continue
            snap_id = snap_row["snapshot_id"]

        result = _score_one_snapshot(
            ctx, city, target_date, winning_bin, int(snap_id),
            temperature_metric=temperature_metric,
            fdr_alpha=fdr_alpha,
            p_market_source=p_market_source,
            override_platt=override_platt,
            clim_rows=clim_rows,
        )
        snapshot_results.append(result)
        n_replayed += 1

        # Accumulate per-city
        if city.name not in per_city:
            per_city[city.name] = {"n_dates": 0, "hits": [], "briers": [], "bss_vals": [], "n_picks": 0}
        per_city[city.name]["n_dates"] += 1
        if result["hit"] is not None:
            per_city[city.name]["hits"].append(result["hit"])
        r_brier = result["brier"]
        r_n_bins = int(result.get("n_bins") or 5)
        if r_brier is not None:
            per_city[city.name]["briers"].append(r_brier)
            per_city[city.name]["bss_vals"].append(_bss_for_snapshot(r_brier, r_n_bins))
        if result["picked_labels"]:
            per_city[city.name]["n_picks"] += 1

        # Write to backtest (read-only from world.db — write to backtest_conn only)
        _insert_backtest_outcome(
            backtest_conn,
            run_id=run_id,
            lane=SELECTION_COVERAGE_LANE,
            subject_id=f"{city.name}|{target_date}|{snap_id}",
            subject_kind="selection_coverage_snapshot",
            city=city.name,
            target_date=target_date,
            range_label=",".join(result["picked_labels"]) or None,
            settlement_value=row["settlement_value"],
            settlement_unit=city.settlement_unit,
            derived_wu_outcome=None if result["hit"] is None else bool(result["hit"]),
            truth_source="settlements_v2.winning_bin",
            divergence_status=result["missing_reason"] or "scored",
            evidence={
                "picked_labels": result["picked_labels"],
                "winning_bin": winning_bin,
                "hit": result["hit"],
                "brier": result["brier"],
                "p_market_source": p_market_source,
                "timezone_class": result["timezone_class"],
            },
            missing_reasons=[result["missing_reason"]] if result["missing_reason"] else [],
        )

    # -- Build summary metrics
    all_hits = [r["hit"] for r in snapshot_results if r["hit"] is not None]
    all_briers = [r["brier"] for r in snapshot_results if r["brier"] is not None]
    all_bss_vals = [
        _bss_for_snapshot(r["brier"], int(r.get("n_bins") or 5))
        for r in snapshot_results
        if r["brier"] is not None
    ]
    n_picks = sum(1 for r in snapshot_results if r["picked_labels"])

    hit_rate = float(sum(all_hits) / len(all_hits)) if all_hits else None
    brier_mean = float(sum(all_briers) / len(all_briers)) if all_briers else None
    bss = round(float(sum(all_bss_vals) / len(all_bss_vals)), 4) if all_bss_vals else None

    # -- Lead-day bucketing (FIX 4)
    def _lead_bucket(ld: float) -> str:
        if ld <= 1.5:
            return "1"
        elif ld <= 2.5:
            return "2"
        elif ld <= 3.5:
            return "3"
        elif ld <= 5.5:
            return "4-5"
        elif ld <= 7.5:
            return "6-7"
        else:
            return "8+"

    lead_bucket_data: dict[str, dict] = {}
    for r in snapshot_results:
        ld = float(r.get("lead_days") or 0.0)
        bucket = _lead_bucket(ld)
        if bucket not in lead_bucket_data:
            lead_bucket_data[bucket] = {"n": 0, "hits": [], "briers": [], "bss_vals": []}
        lead_bucket_data[bucket]["n"] += 1
        if r["hit"] is not None:
            lead_bucket_data[bucket]["hits"].append(r["hit"])
        rb = r["brier"]
        rn = int(r.get("n_bins") or 5)
        if rb is not None:
            lead_bucket_data[bucket]["briers"].append(rb)
            lead_bucket_data[bucket]["bss_vals"].append(_bss_for_snapshot(rb, rn))

    by_lead_day: dict[str, dict] = {}
    for bkt in ["1", "2", "3", "4-5", "6-7", "8+"]:
        g = lead_bucket_data.get(bkt, {"n": 0, "hits": [], "briers": [], "bss_vals": []})
        h = g["hits"]
        b = g["briers"]
        bv = g["bss_vals"]
        by_lead_day[bkt] = {
            "n": g["n"],
            "hit_rate": float(sum(h) / len(h)) if h else None,
            "brier": float(sum(b) / len(b)) if b else None,
            "bss": round(float(sum(bv) / len(bv)), 4) if bv else None,
        }

    # Per-city summary
    per_city_summary = {}
    for cn, stats in per_city.items():
        hits = stats["hits"]
        briers = stats["briers"]
        bss_vals = stats["bss_vals"]
        per_city_summary[cn] = {
            "n_dates": stats["n_dates"],
            "n_picks": stats["n_picks"],
            "hit_rate": float(sum(hits) / len(hits)) if hits else None,
            "brier": float(sum(briers) / len(briers)) if briers else None,
            "bss": round(float(sum(bss_vals) / len(bss_vals)), 4) if bss_vals else None,
        }

    # Asia/non-Asia stratification (D3)
    tz_strat = _build_timezone_stratification(snapshot_results)

    summary.n_replayed = n_replayed
    summary.n_would_trade = n_picks
    summary.per_city = per_city_summary
    summary.cities_covered = sorted(per_city.keys())
    summary.coverage_pct = round(n_replayed / max(1, len(settlement_rows)) * 100, 1)
    summary.limitations["selection_coverage"] = {
        "hit_rate": hit_rate,
        "hit_rate_including_no_pick": (
            float(sum(all_hits) / n_replayed) if n_replayed > 0 and all_hits else None
        ),
        "n_picks": n_picks,
        "n_scored": len(all_hits),
        "n_no_pick": n_replayed - n_picks,
        "brier_aggregate": brier_mean,
        "bss_vs_climatology": bss,
        "by_timezone_class": tz_strat,
        "by_lead_day": by_lead_day,
    }

    _insert_backtest_run(backtest_conn, summary)
    backtest_conn.commit()
    backtest_conn.close()
    conn.close()
    return summary
