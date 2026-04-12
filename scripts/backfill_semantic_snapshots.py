#!/usr/bin/env python3
"""Backfill minimal semantic snapshots into trade_decisions and portfolio state.

This does not fabricate full original runtime objects. It reconstructs the
minimum semantically useful context from persisted facts and marks the payloads
as reconstructed so replay/audit can distinguish them from first-class runtime
captures.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name
from src.contracts import SettlementSemantics
from src.state.db import get_trade_connection_with_world as get_connection, init_schema

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _serialize_settlement_semantics(city_name: str) -> str | None:
    city = cities_by_name.get(city_name)
    if city is None:
        return None
    semantics = SettlementSemantics.for_city(city)
    payload = asdict(semantics)
    payload["reconstructed"] = True
    payload["reconstruction_source"] = "city_contract"
    return _json_dump(payload)


def _serialize_epistemic_context(*, decision_time: str | None, available_at: str | None, fetch_time: str | None, data_version: str | None) -> str:
    payload = {
        "decision_time_utc": decision_time,
        "data_cutoff_time": available_at or fetch_time or decision_time,
        "data_version": data_version or "unknown",
        "is_fallback": False,
        "reconstructed": True,
        "reconstruction_source": "trade_decisions_plus_ensemble_snapshot",
    }
    return _json_dump(payload)


def _serialize_edge_context(row) -> str:
    p_posterior = float(row["p_posterior"] or 0.0)
    forward_edge = float(row["edge"] or 0.0)
    p_market = max(0.0, min(1.0, p_posterior - forward_edge))
    ci_lower = float(row["ci_lower"] or 0.0)
    ci_upper = float(row["ci_upper"] or 0.0)
    payload = {
        "p_raw": [float(row["p_raw"] or p_posterior)],
        "p_cal": [p_posterior],
        "p_market": [p_market],
        "p_posterior": p_posterior,
        "forward_edge": forward_edge,
        "alpha": 0.0,
        "confidence_band_upper": ci_upper,
        "confidence_band_lower": ci_lower,
        "entry_provenance": row["selected_method"] or row["entry_method"] or "ens_member_counting",
        "decision_snapshot_id": str(row["forecast_snapshot_id"] or ""),
        "n_edges_found": 1,
        "n_edges_after_fdr": 1,
        "market_velocity_1h": 0.0,
        "divergence_score": 0.0,
        "reconstructed": True,
        "reconstruction_source": "trade_decisions_scalar_projection",
    }
    return _json_dump(payload)


def _distance_seconds(a: str | None, b: str | None) -> float:
    da = _parse_ts(a)
    db = _parse_ts(b)
    if da is None or db is None:
        return 1e18
    return abs((da - db).total_seconds())


def _best_trade_row(rows, target_ts: str | None):
    if not rows:
        return None
    return min(rows, key=lambda r: _distance_seconds(r["timestamp"], target_ts))


def run_backfill(positions_path: Path = POSITIONS_PATH) -> dict:
    conn = get_connection()
    init_schema(conn)

    try:
        conn.execute("SELECT 1 FROM world.ensemble_snapshots LIMIT 0")
        _sp = "shared."
    except Exception:
        _sp = ""

    rows = conn.execute(
        f"""
        SELECT td.trade_id, td.market_id, td.bin_label, td.direction, td.timestamp, td.status,
               td.forecast_snapshot_id, td.entry_method, td.selected_method,
               td.p_raw, td.p_posterior, td.edge, td.ci_lower, td.ci_upper,
               td.settlement_semantics_json, td.epistemic_context_json, td.edge_context_json,
               es.city, es.target_date, es.available_at, es.fetch_time, es.data_version
        FROM trade_decisions td
        LEFT JOIN {_sp}ensemble_snapshots es ON es.snapshot_id = td.forecast_snapshot_id
        """
    ).fetchall()

    updated_trade_rows = 0
    for row in rows:
        settlement_json = row["settlement_semantics_json"]
        epistemic_json = row["epistemic_context_json"]
        edge_json = row["edge_context_json"]
        if settlement_json and epistemic_json and edge_json:
            continue

        city_name = row["city"]
        if not city_name:
            continue

        if not settlement_json:
            settlement_json = _serialize_settlement_semantics(city_name)
        if not epistemic_json:
            epistemic_json = _serialize_epistemic_context(
                decision_time=row["timestamp"],
                available_at=row["available_at"],
                fetch_time=row["fetch_time"],
                data_version=row["data_version"],
            )
        if not edge_json:
            edge_json = _serialize_edge_context(row)

        conn.execute(
            """
            UPDATE trade_decisions
            SET settlement_semantics_json = COALESCE(settlement_semantics_json, ?),
                epistemic_context_json = COALESCE(epistemic_context_json, ?),
                edge_context_json = COALESCE(edge_context_json, ?)
            WHERE trade_id = ?
            """,
            (settlement_json, epistemic_json, edge_json, row["trade_id"]),
        )
        updated_trade_rows += 1

    conn.commit()

    updated_open_positions = 0
    updated_recent_exits = 0
    if positions_path.exists():
        state = json.loads(positions_path.read_text())
        indexed_rows: dict[tuple[str, str, str, str, str], list] = {}
        refreshed_rows = conn.execute(
            """
            SELECT trade_id, market_id, bin_label, direction, status, timestamp,
                   forecast_snapshot_id, settlement_semantics_json,
                   epistemic_context_json, edge_context_json
            FROM trade_decisions
            WHERE settlement_semantics_json IS NOT NULL
              AND epistemic_context_json IS NOT NULL
              AND edge_context_json IS NOT NULL
            """
        ).fetchall()

        for row in refreshed_rows:
            key = (
                row["market_id"],
                row["bin_label"],
                row["direction"],
                str(row["forecast_snapshot_id"] or ""),
                row["status"],
            )
            indexed_rows.setdefault(key, []).append(row)

        for pos in state.get("positions", []):
            if pos.get("settlement_semantics_json") and pos.get("epistemic_context_json") and pos.get("edge_context_json"):
                continue
            key = (
                pos.get("market_id", ""),
                pos.get("bin_label", ""),
                pos.get("direction", ""),
                str(pos.get("decision_snapshot_id", "") or ""),
                "entered",
            )
            row = _best_trade_row(indexed_rows.get(key, []), pos.get("entered_at"))
            if row is None:
                continue
            for field in ["settlement_semantics_json", "epistemic_context_json", "edge_context_json"]:
                if not pos.get(field) and row[field]:
                    pos[field] = row[field]
            updated_open_positions += 1

        for ex in state.get("recent_exits", []):
            if str(ex.get("market_id", "")).startswith("mock_"):
                continue
            if ex.get("settlement_semantics_json") and ex.get("epistemic_context_json") and ex.get("edge_context_json"):
                continue
            key = (
                ex.get("market_id", ""),
                ex.get("bin_label", ""),
                ex.get("direction", ""),
                str(ex.get("decision_snapshot_id", "") or ""),
                "exited",
            )
            row = _best_trade_row(indexed_rows.get(key, []), ex.get("exited_at"))
            if row is None:
                continue
            for field in ["settlement_semantics_json", "epistemic_context_json", "edge_context_json"]:
                if not ex.get(field) and row[field]:
                    ex[field] = row[field]
            updated_recent_exits += 1

        positions_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    remaining = conn.execute(
        """
        SELECT
          SUM(CASE WHEN settlement_semantics_json IS NULL OR settlement_semantics_json = '' THEN 1 ELSE 0 END) AS null_settlement,
          SUM(CASE WHEN epistemic_context_json IS NULL OR epistemic_context_json = '' THEN 1 ELSE 0 END) AS null_epistemic,
          SUM(CASE WHEN edge_context_json IS NULL OR edge_context_json = '' THEN 1 ELSE 0 END) AS null_edge
        FROM trade_decisions
        """
    ).fetchone()
    conn.close()

    return {
        "updated_trade_rows": updated_trade_rows,
        "updated_open_positions": updated_open_positions,
        "updated_recent_exits": updated_recent_exits,
        "remaining_null_settlement_semantics": int(remaining["null_settlement"] or 0),
        "remaining_null_epistemic_context": int(remaining["null_epistemic"] or 0),
        "remaining_null_edge_context": int(remaining["null_edge"] or 0),
    }


if __name__ == "__main__":
    print(json.dumps(run_backfill(), ensure_ascii=False, indent=2))
