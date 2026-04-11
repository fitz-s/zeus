#!/usr/bin/env python3
"""Capture current evaluator decisions into decision_log without placing trades.

This is an audit-only path to seed replay-ready decision artifacts from the
current market state without mutating portfolio state or execution state.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_mode
from src.data.market_scanner import find_weather_markets
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine.cycle_runner import _classify_edge_source, _classify_strategy
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import MarketCandidate, evaluate_candidate
from src.riskguard.riskguard import get_current_level
from src.state.db import get_shared_connection as get_connection, log_shadow_signal
from src.state.decision_chain import CycleArtifact, NoTradeCase, store_artifact
from src.state.portfolio import load_portfolio
from src.strategy.risk_limits import RiskLimits


def run_capture(mode: DiscoveryMode, *, limit: int | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    portfolio = load_portfolio()
    clob = PolymarketClient(paper_mode=(get_mode() == "paper"))
    limits = RiskLimits()

    summary = {
        "mode": mode.value,
        "started_at": started_at,
        "capture_only": True,
        "risk_level": get_current_level().value,
        "candidates": 0,
        "trade_candidates": 0,
        "no_trade_cases": 0,
    }
    artifact = CycleArtifact(mode=mode.value, started_at=started_at, summary=summary)

    markets = find_weather_markets(min_hours_to_resolution=0)
    for market in markets:
        if limit is not None and summary["candidates"] >= limit:
            break
        city = market.get("city")
        if city is None:
            continue
        try:
            obs = get_current_observation(city) if mode == DiscoveryMode.DAY0_CAPTURE else None
        except Exception:
            obs = None
        candidate = MarketCandidate(
            city=city,
            target_date=market["target_date"],
            outcomes=market["outcomes"],
            hours_since_open=market["hours_since_open"],
            hours_to_resolution=market["hours_to_resolution"],
            event_id=market.get("event_id", ""),
            slug=market.get("slug", ""),
            observation=obs,
            discovery_mode=mode.value,
        )
        summary["candidates"] += 1
        try:
            decisions = evaluate_candidate(candidate, conn, portfolio, clob, limits, entry_bankroll=portfolio.effective_bankroll)
        except Exception:
            continue
        parseable_labels = [
            outcome["title"]
            for outcome in market.get("outcomes", [])
            if not (outcome.get("range_low") is None and outcome.get("range_high") is None)
        ]

        for d in decisions:
            if d.should_trade and d.edge and d.tokens:
                artifact.add_trade(
                    {
                        "decision_id": d.decision_id,
                        "trade_id": "",
                        "status": "capture_only",
                        "timestamp": started_at,
                        "city": city.name,
                        "target_date": candidate.target_date,
                        "range_label": d.edge.bin.label,
                        "direction": d.edge.direction,
                        "market_id": d.tokens["market_id"],
                        "token_id": d.tokens["token_id"],
                        "no_token_id": d.tokens["no_token_id"],
                        "size_usd": d.size_usd,
                        "entry_price": d.edge.entry_price,
                        "p_posterior": d.edge.p_posterior,
                        "edge": d.edge.edge,
                        "strategy": _classify_strategy(mode, d.edge, d.edge_source or _classify_edge_source(mode, d.edge)),
                        "edge_source": d.edge_source or _classify_edge_source(mode, d.edge),
                        "market_hours_open": candidate.hours_since_open,
                        "decision_snapshot_id": d.decision_snapshot_id,
                        "selected_method": d.selected_method,
                        "applied_validations": d.applied_validations,
                        "settlement_semantics_json": d.settlement_semantics_json,
                        "epistemic_context_json": d.epistemic_context_json,
                        "edge_context_json": d.edge_context_json,
                        "bin_labels": parseable_labels,
                        "p_raw_vector": d.p_raw.tolist() if d.p_raw is not None else [],
                        "p_cal_vector": d.p_cal.tolist() if d.p_cal is not None else [],
                        "p_market_vector": d.p_market.tolist() if d.p_market is not None else [],
                        "alpha": d.alpha,
                        "agreement": d.agreement,
                    }
                )
                summary["trade_candidates"] += 1
            else:
                artifact.add_no_trade(
                    NoTradeCase(
                        decision_id=d.decision_id,
                        city=city.name,
                        target_date=candidate.target_date,
                        range_label=d.edge.bin.label if d.edge else "",
                        direction=d.edge.direction if d.edge else "",
                        rejection_stage=d.rejection_stage,
                        rejection_reasons=list(d.rejection_reasons),
                        best_edge=d.edge.edge if d.edge else 0.0,
                        model_prob=d.edge.p_posterior if d.edge else 0.0,
                        market_price=d.edge.entry_price if d.edge else 0.0,
                        decision_snapshot_id=d.decision_snapshot_id,
                        selected_method=d.selected_method,
                        applied_validations=list(d.applied_validations),
                        bin_labels=parseable_labels,
                        p_raw_vector=d.p_raw.tolist() if d.p_raw is not None else [],
                        p_cal_vector=d.p_cal.tolist() if d.p_cal is not None else [],
                        p_market_vector=d.p_market.tolist() if d.p_market is not None else [],
                        alpha=d.alpha,
                        agreement=d.agreement,
                        timestamp=started_at,
                    )
                )
                summary["no_trade_cases"] += 1

        if decisions:
            first = decisions[0]
            edges_payload = [
                {
                    "decision_id": d.decision_id,
                    "should_trade": d.should_trade,
                    "direction": d.edge.direction if d.edge else "",
                    "bin_label": d.edge.bin.label if d.edge else "",
                    "edge": d.edge.edge if d.edge else 0.0,
                    "rejection_stage": d.rejection_stage,
                    "decision_snapshot_id": d.decision_snapshot_id,
                    "selected_method": d.selected_method,
                }
                for d in decisions
            ]
            try:
                from src.engine.time_context import lead_hours_to_target
                from datetime import date

                log_shadow_signal(
                    conn,
                    city=city.name,
                    target_date=candidate.target_date,
                    timestamp=started_at,
                    decision_snapshot_id=first.decision_snapshot_id,
                    p_raw_json=json.dumps(first.p_raw.tolist() if first.p_raw is not None else []),
                    p_cal_json=json.dumps(first.p_cal.tolist() if first.p_cal is not None else []),
                    edges_json=json.dumps(edges_payload),
                    lead_hours=float(lead_hours_to_target(date.fromisoformat(candidate.target_date), city.timezone, datetime.fromisoformat(started_at.replace("Z", "+00:00")))),
                )
            except Exception:
                pass

    artifact.completed_at = datetime.now(timezone.utc).isoformat()
    store_artifact(conn, artifact)
    conn.close()
    return summary


if __name__ == "__main__":
    mode = DiscoveryMode(sys.argv[1]) if len(sys.argv) > 1 else DiscoveryMode.OPENING_HUNT
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(run_capture(mode, limit=limit), ensure_ascii=False, indent=2))
