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
    forecast_pairs = conn.execute(
        """
        SELECT COUNT(DISTINCT city || '|' || target_date)
        FROM world.forecasts
        WHERE forecast_high IS NOT NULL
        """
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
    trade_history_subjects = conn.execute(
        """
        SELECT COUNT(DISTINCT position_id) FROM (
            SELECT position_id FROM outcome_fact
            UNION
            SELECT position_id FROM position_current
            UNION
            SELECT position_id FROM position_events
            UNION
            SELECT position_id FROM execution_fact
            UNION
            SELECT runtime_trade_id AS position_id
            FROM trade_decisions
            WHERE runtime_trade_id IS NOT NULL AND runtime_trade_id != ''
        )
        WHERE position_id IS NOT NULL AND position_id != ''
        """
    ).fetchone()[0]
    comparable_trade_subjects = conn.execute(
        """
        SELECT COUNT(DISTINCT pc.position_id)
        FROM position_current pc
        JOIN world.settlements s
          ON s.city = pc.city AND s.target_date = pc.target_date
        WHERE s.settlement_value IS NOT NULL
          AND pc.bin_label IS NOT NULL
          AND pc.bin_label != ''
        """
    ).fetchone()[0]

    replay_path = (PROJECT_ROOT / "src" / "engine" / "replay.py").read_text(encoding="utf-8")
    ctx = ReplayContext(conn)
    snapshot_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)

    snapshot_vector_compatible_subjects: set[tuple[str, str]] = set()
    snapshot_parseable_bin_subjects: set[tuple[str, str]] = set()
    strict_decision_ref_subjects: set[tuple[str, str]] = set()
    snapshot_only_ref_subjects: set[tuple[str, str]] = set()
    overlap_rows = conn.execute(
        """
        SELECT DISTINCT s.city, s.target_date
        FROM world.settlements s
        JOIN world.ensemble_snapshots es
          ON s.city = es.city AND s.target_date = es.target_date
        WHERE s.settlement_value IS NOT NULL
          AND es.p_raw_json IS NOT NULL
        """
    ).fetchall()
    for row in overlap_rows:
        subject = (row["city"], row["target_date"])
        strict_ref = ctx.get_decision_reference_for(row["city"], row["target_date"])
        if strict_ref is not None:
            strict_decision_ref_subjects.add(subject)
        snapshot_ref = snapshot_ctx.get_decision_reference_for(row["city"], row["target_date"])
        if snapshot_ref is not None:
            snapshot_only_ref_subjects.add(subject)
        p_raw_rows = conn.execute(
            """
            SELECT p_raw_json
            FROM world.ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND p_raw_json IS NOT NULL
              AND p_raw_json != ''
            """,
            subject,
        ).fetchall()
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
            snapshot_parseable_bin_subjects.add(subject)
        for p_raw_row in p_raw_rows:
            p_raw = json.loads(p_raw_row["p_raw_json"]) if p_raw_row["p_raw_json"] else []
            if len(p_raw) == len(bin_labels) and len(p_raw) > 0:
                snapshot_vector_compatible_subjects.add(subject)
                break

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
        "forecast_pairs": forecast_pairs,
        "settlement_snapshot_overlap": settlement_snapshot_overlap,
        "settlement_snapshot_overlap_pct": round(settlement_snapshot_overlap / max(1, total_settlements) * 100, 1),
        "covered_settlements": covered_settlements,
        "coverage_pct": round(covered_settlements / max(1, total_settlements) * 100, 1),
        "snapshot_only_covered_settlements": snapshot_only_covered,
        "snapshot_only_coverage_pct": round(snapshot_only_covered / max(1, total_settlements) * 100, 1),
        "snapshot_vector_compatible_settlements": len(snapshot_vector_compatible_subjects),
        "snapshot_vector_compatible_pct": round(len(snapshot_vector_compatible_subjects) / max(1, total_settlements) * 100, 1),
        "snapshot_parseable_bins_settlements": len(snapshot_parseable_bin_subjects),
        "snapshot_parseable_bins_pct": round(len(snapshot_parseable_bin_subjects) / max(1, total_settlements) * 100, 1),
        "invalid_temporal_rows": invalid_temporal_rows,
        "lane_readiness": {
            "wu_settlement_sweep": {
                "settlement_value_rows": total_settlements,
                "forecast_row_subjects": forecast_pairs,
                "parseable_bin_subjects": len(snapshot_parseable_bin_subjects),
                "snapshot_vector_compatible_subjects": len(snapshot_vector_compatible_subjects),
                "strict_decision_reference_subjects": len(strict_decision_ref_subjects),
                "diagnostic_snapshot_reference_subjects": len(snapshot_only_ref_subjects),
            },
            "trade_history_audit": {
                "trade_history_subjects": trade_history_subjects,
                "wu_trade_comparable_subjects": comparable_trade_subjects,
                "strict_decision_reference_subjects": covered_settlements,
            },
        },
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
            "strict_decision_refs": len(strict_decision_ref_subjects),
            "snapshot_only_refs": len(snapshot_only_ref_subjects),
            "parseable_bins": len(snapshot_parseable_bin_subjects),
            "vector_compatible": len(snapshot_vector_compatible_subjects),
        },
        "decision_reference_samples": sample_refs,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
