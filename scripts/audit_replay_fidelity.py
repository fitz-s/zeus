#!/usr/bin/env python3
"""Audit replay fidelity relative to the live decision path."""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.replay import ReplayContext
from src.state.db import get_trade_connection_with_world as get_connection, init_schema
from src.data.market_scanner import _parse_temp_range


def run_audit() -> dict:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    total_settlements = conn.execute(
        "SELECT COUNT(*) FROM world.settlements WHERE settlement_value IS NOT NULL"
    ).fetchone()[0]
    snapshot_pairs = conn.execute(
        "SELECT COUNT(DISTINCT city || '|' || target_date) FROM world.ensemble_snapshots WHERE p_raw_json IS NOT NULL"
    ).fetchone()[0]
    settlement_snapshot_overlap = conn.execute(
        """
        SELECT COUNT(DISTINCT s.city || '|' || s.target_date)
        FROM world.settlements s
        JOIN world.ensemble_snapshots es
          ON s.city = es.city AND s.target_date = es.target_date
        WHERE s.settlement_value IS NOT NULL
          AND es.p_raw_json IS NOT NULL
        """
    ).fetchone()[0]
    covered_settlements = conn.execute(
        """
        SELECT COUNT(DISTINCT es.city || '|' || es.target_date)
        FROM trade_decisions td
        JOIN world.ensemble_snapshots es ON es.snapshot_id = td.forecast_snapshot_id
        WHERE td.forecast_snapshot_id IS NOT NULL
          AND datetime(es.available_at) <= datetime(td.timestamp)
        """
    ).fetchone()[0]

    invalid_temporal_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM trade_decisions td
        JOIN world.ensemble_snapshots es ON es.snapshot_id = td.forecast_snapshot_id
        WHERE td.forecast_snapshot_id IS NOT NULL
          AND datetime(es.available_at) > datetime(td.timestamp)
        """
    ).fetchone()[0]

    replay_path = (PROJECT_ROOT / "src" / "engine" / "replay.py").read_text(encoding="utf-8")
    ctx = ReplayContext(conn)
    snapshot_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)

    snapshot_vector_compatible = 0
    snapshot_parseable_bins = 0
    strict_decision_refs = 0
    snapshot_only_refs = 0
    overlap_rows = conn.execute(
        """
        SELECT DISTINCT s.city, s.target_date, es.p_raw_json
        FROM world.settlements s
        JOIN world.ensemble_snapshots es
          ON s.city = es.city AND s.target_date = es.target_date
        WHERE s.settlement_value IS NOT NULL
          AND es.p_raw_json IS NOT NULL
        """
    ).fetchall()
    for row in overlap_rows:
        strict_ref = ctx.get_decision_reference_for(row["city"], row["target_date"])
        if strict_ref is not None:
            strict_decision_refs += 1
        snapshot_ref = snapshot_ctx.get_decision_reference_for(row["city"], row["target_date"])
        if snapshot_ref is not None:
            snapshot_only_refs += 1
        p_raw = json.loads(row["p_raw_json"]) if row["p_raw_json"] else []
        bin_rows = conn.execute(
            """
            SELECT DISTINCT range_label
            FROM (
              SELECT range_label FROM world.calibration_pairs WHERE city = ? AND target_date = ?
              UNION
              SELECT range_label FROM world.market_events WHERE city = ? AND target_date = ? AND range_label IS NOT NULL AND range_label != ''
            )
            ORDER BY range_label
            """,
            (row["city"], row["target_date"], row["city"], row["target_date"]),
        ).fetchall()
        bin_labels = [r["range_label"] for r in bin_rows if _parse_temp_range(r["range_label"]) != (None, None)]
        if bin_labels:
            snapshot_parseable_bins += 1
        if len(p_raw) == len(bin_labels) and len(p_raw) > 0:
            snapshot_vector_compatible += 1

    sample_refs = []
    rows = conn.execute(
        """
        SELECT city, target_date
        FROM world.settlements
        WHERE settlement_value IS NOT NULL
        ORDER BY target_date DESC, city
        LIMIT 10
        """
    ).fetchall()
    snapshot_only_covered = 0
    all_rows = conn.execute(
        """
        SELECT city, target_date
        FROM world.settlements
        WHERE settlement_value IS NOT NULL
        ORDER BY target_date DESC, city
        """
    ).fetchall()
    for row in all_rows:
        ref = snapshot_ctx.get_decision_reference_for(row["city"], row["target_date"])
        if ref is not None:
            snapshot_only_covered += 1
    for row in rows:
        ref = ctx.get_decision_reference_for(row["city"], row["target_date"])
        sample_refs.append(
            {
                "city": row["city"],
                "target_date": row["target_date"],
                "has_decision_reference": ref is not None,
                "decision_time": ref["decision_time"] if ref else None,
                "snapshot_id": ref["snapshot_id"] if ref else None,
                "source": ref["source"] if ref else None,
            }
        )

    decision_log_rows = conn.execute("SELECT artifact_json FROM decision_log").fetchall()
    shadow_signal_rows = conn.execute("SELECT COUNT(*) FROM world.shadow_signals").fetchone()[0]
    trade_cases = 0
    trade_cases_with_vectors = 0
    no_trade_cases = 0
    no_trade_cases_with_vectors = 0
    for row in decision_log_rows:
        try:
            artifact = json.loads(row["artifact_json"])
        except Exception:
            continue
        for case in artifact.get("trade_cases", []) or []:
            trade_cases += 1
            if (
                case.get("decision_snapshot_id")
                and case.get("bin_labels")
                and len(case.get("p_raw_vector") or []) > 0
                and len(case.get("p_cal_vector") or []) > 0
            ):
                trade_cases_with_vectors += 1
        for case in artifact.get("no_trade_cases", []) or []:
            no_trade_cases += 1
            if (
                case.get("decision_snapshot_id")
                and case.get("bin_labels")
                and len(case.get("p_raw_vector") or []) > 0
                and len(case.get("p_cal_vector") or []) > 0
            ):
                no_trade_cases_with_vectors += 1
    conn.close()

    return {
        "total_settlements": total_settlements,
        "snapshot_pairs": snapshot_pairs,
        "settlement_snapshot_overlap": settlement_snapshot_overlap,
        "settlement_snapshot_overlap_pct": round(settlement_snapshot_overlap / max(1, total_settlements) * 100, 1),
        "covered_settlements": covered_settlements,
        "coverage_pct": round(covered_settlements / max(1, total_settlements) * 100, 1),
        "snapshot_only_covered_settlements": snapshot_only_covered,
        "snapshot_only_coverage_pct": round(snapshot_only_covered / max(1, total_settlements) * 100, 1),
        "snapshot_vector_compatible_settlements": snapshot_vector_compatible,
        "snapshot_vector_compatible_pct": round(snapshot_vector_compatible / max(1, total_settlements) * 100, 1),
        "snapshot_parseable_bins_settlements": snapshot_parseable_bins,
        "snapshot_parseable_bins_pct": round(snapshot_parseable_bins / max(1, total_settlements) * 100, 1),
        "invalid_temporal_rows": invalid_temporal_rows,
        "uses_uniform_market_prior": "p_market = 1.0 / len(bin_probs_cal)" in replay_path,
        "uses_flat_edge_threshold": "edge_min = 0.03" in replay_path,
        "uses_market_analysis_fdr": "MarketAnalysis(" in replay_path and "fdr_filter(edges)" in replay_path,
        "uses_kelly_sizing": "kelly_size(" in replay_path and "dynamic_kelly_mult(" in replay_path,
        "decision_log_future_capture": {
            "trade_cases": trade_cases,
            "trade_cases_with_vectors": trade_cases_with_vectors,
            "no_trade_cases": no_trade_cases,
            "no_trade_cases_with_vectors": no_trade_cases_with_vectors,
            "future_ready_capture_present": (trade_cases_with_vectors + no_trade_cases_with_vectors) > 0,
            "shadow_signals": shadow_signal_rows,
        },
        "historical_failure_buckets": {
            "overlap_rows": settlement_snapshot_overlap,
            "strict_decision_refs": strict_decision_refs,
            "snapshot_only_refs": snapshot_only_refs,
            "parseable_bins": snapshot_parseable_bins,
            "vector_compatible": snapshot_vector_compatible,
        },
        "decision_reference_samples": sample_refs,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
