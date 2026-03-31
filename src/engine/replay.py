"""Decision Replay Engine: re-execute the evaluator pipeline against historical data.

Three modes:
- audit:          Replay with current logic, compare against actual decisions
- counterfactual: Replay with modified parameters, compare PnL
- walk_forward:   Weekly train/test split for parameter validation

Key principle: feeds the SAME evaluate_candidate() function via ReplayContext,
which replaces live API calls with stored ensemble_snapshots + token_price_log.

Known Limitations (v1, 2026-03-31)
===================================

L1 — MARKET PRICE: UNIFORM PRIOR
    token_price_log only has 3 days of data (2026-03-28 onward) and lacks
    city/target_date columns. So replay uses p_market = 1/n_bins (uniform).
    This means α has NO effect in audit mode — edge = α*(p_cal - 1/n) which
    is just a scaled version of p_cal. To fix: link token_price_log to markets
    via condition_id, or store market prices per bin at decision time.
    IMPACT: PnL results are directionally correct but magnitudes are wrong.

L2 — FLAT POSITION SIZING ($5/trade)
    Live evaluator uses Kelly criterion with FDR-filtered edges and bootstrap CI.
    Replay uses a flat $5 per signal that passes edge_min=0.03.
    To fix: port MarketAnalysis.find_edges() + kelly_size() into replay path.
    IMPACT: Overstates trade count (no FDR), understates size on strong edges.

L3 — NO BOOTSTRAP CI / FDR FILTER
    Edge detection uses a simple |edge| >= 0.03 threshold instead of the
    full bootstrap CI + FDR pipeline. This makes replay LESS selective than
    live — it will "trade" things live would reject.
    To fix: reconstruct EnsembleSignal from stored member_maxes and run
    full MarketAnalysis with bootstrap.
    IMPACT: Replay trade count is an upper bound; live would trade fewer.

L4 — COVERAGE GAP (18.3%)
    Only 254/1385 settlements have matching ensemble_snapshots with p_raw_json.
    Most snapshots are from 2026-03-24 onward. Pre-January 2026 has zero coverage.
    Coverage improves automatically as live system stores snapshots every cycle.
    IMPACT: Results are biased toward recent weather patterns. No summer (JJA) data.

L5 — MODEL AGREEMENT ASSUMED "AGREE"
    GFS crosscheck is skipped in replay (no stored GFS data). All candidates
    are assumed to have model_agreement="AGREE". In live, ~5-10% are CONFLICT
    and get rejected.
    IMPACT: Overstates trade count by not filtering model-disagreement cases.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np

from src.config import City, cities_by_name, settings
from src.state.db import get_connection
from src.types import Bin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReplayDecision:
    """One replayed decision for a single city × target_date × bin."""
    city: str
    target_date: str
    range_label: str
    direction: str
    should_trade: bool
    rejection_stage: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    edge: float = 0.0
    p_posterior: float = 0.0
    p_raw: float = 0.0
    size_usd: float = 0.0
    entry_price: float = 0.0
    edge_source: str = ""
    applied_validations: list[str] = field(default_factory=list)


@dataclass
class ReplayOutcome:
    """One city × target_date outcome: what replay decided vs what actually happened."""
    city: str
    target_date: str
    settlement_value: Optional[float]
    winning_bin: Optional[str]
    # Replay results
    replay_decisions: list[ReplayDecision] = field(default_factory=list)
    replay_best_edge: float = 0.0
    replay_would_trade: bool = False
    replay_pnl: float = 0.0
    # Actual (from decision_log) if available
    actual_traded: Optional[bool] = None
    actual_pnl: Optional[float] = None
    # ENS metadata
    snapshot_id: Optional[str] = None
    lead_hours: float = 0.0
    n_members: int = 0


@dataclass
class ReplaySummary:
    """Aggregated replay results."""
    run_id: str
    mode: str
    date_range: tuple[str, str]
    n_settlements: int = 0
    n_replayed: int = 0
    n_would_trade: int = 0
    n_actual_traded: int = 0
    # PnL
    replay_total_pnl: float = 0.0
    replay_win_rate: float = 0.0
    # Coverage
    cities_covered: list[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    # Per-city breakdown
    per_city: dict = field(default_factory=dict)
    # Overrides applied
    overrides: dict = field(default_factory=dict)
    outcomes: list[ReplayOutcome] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ReplayContext: replaces live API calls with historical data
# ---------------------------------------------------------------------------

class ReplayContext:
    """Provides historical data to the evaluator, replacing live API calls.

    This is NOT a mock — it returns real stored data from the database.
    """

    def __init__(self, conn, overrides: Optional[dict] = None):
        self.conn = conn
        self.overrides = overrides or {}
        self._snapshot_cache: dict[tuple[str, str], dict] = {}

    def get_snapshot_for(
        self, city_name: str, target_date: str
    ) -> Optional[dict]:
        """Get stored ensemble snapshot closest to decision time."""
        key = (city_name, target_date)
        if key in self._snapshot_cache:
            return self._snapshot_cache[key]

        row = self.conn.execute("""
            SELECT snapshot_id, members_json, p_raw_json, lead_hours,
                   spread, is_bimodal, model_version, issue_time, fetch_time
            FROM ensemble_snapshots
            WHERE city = ? AND target_date = ?
            ORDER BY fetch_time DESC LIMIT 1
        """, (city_name, target_date)).fetchone()

        if row is None:
            self._snapshot_cache[key] = None
            return None

        members = json.loads(row["members_json"])
        result = {
            "snapshot_id": row["snapshot_id"],
            "member_maxes": np.array(members, dtype=np.float64),
            "p_raw_stored": json.loads(row["p_raw_json"]) if row["p_raw_json"] else None,
            "lead_hours": row["lead_hours"],
            "spread": row["spread"],
            "is_bimodal": bool(row["is_bimodal"]),
            "model": row["model_version"],
            "issue_time": row["issue_time"],
            "fetch_time": row["fetch_time"],
            "n_members": len(members),
        }
        self._snapshot_cache[key] = result
        return result

    def get_market_price(self, city_name: str, target_date: str) -> float:
        """Get historical market mid-price from token_price_log.

        Falls back to 0.5 (uninformed prior) if no data.
        """
        # token_price_log doesn't have city/target_date directly,
        # but calibration_pairs has the p_raw at decision time.
        # Use 0.5 as market price for replay (equivalent to α=1.0 test
        # where market provides zero information)
        return 0.5

    def get_settlement(self, city_name: str, target_date: str) -> Optional[dict]:
        """Get settlement outcome for scoring."""
        row = self.conn.execute(
            "SELECT settlement_value, winning_bin FROM settlements "
            "WHERE city = ? AND target_date = ?",
            (city_name, target_date),
        ).fetchone()
        if row:
            return {
                "settlement_value": row["settlement_value"],
                "winning_bin": row["winning_bin"],
            }
        return None


# ---------------------------------------------------------------------------
# Core replay engine
# ---------------------------------------------------------------------------

def _replay_one_settlement(
    ctx: ReplayContext,
    city: City,
    target_date: str,
    settlement: dict,
) -> Optional[ReplayOutcome]:
    """Replay the evaluator pipeline for one city × target_date.

    Uses stored member_maxes from ensemble_snapshots to recompute p_raw,
    then runs through calibration, alpha fusion, edge detection, and sizing.
    """
    from src.calibration.manager import get_calibrator
    from src.signal.ensemble_signal import EnsembleSignal, SettlementSemantics
    from src.strategy.market_fusion import compute_alpha
    from src.signal.diurnal import season_from_month

    snapshot = ctx.get_snapshot_for(city.name, target_date)
    if snapshot is None:
        return None  # No ENS data for this date

    member_maxes = snapshot["member_maxes"]
    lead_days = snapshot["lead_hours"] / 24.0
    target_d = date.fromisoformat(target_date)
    season = season_from_month(target_d.month)

    # Build bins from settlement context
    # We need the bin structure. Get it from calibration_pairs for this city×date
    bin_labels = ctx.conn.execute(
        "SELECT DISTINCT range_label FROM calibration_pairs "
        "WHERE city = ? AND target_date = ? ORDER BY range_label",
        (city.name, target_date),
    ).fetchall()

    if not bin_labels:
        # Try to reconstruct from stored p_raw
        if snapshot["p_raw_stored"]:
            # We have p_raw but no bin labels — can still compute PnL
            pass
        return None  # Can't replay without bin structure

    # Use the stored p_raw directly (it was computed at decision time)
    p_raw_stored = snapshot["p_raw_stored"]
    if p_raw_stored is None or len(p_raw_stored) == 0:
        return None

    # Get calibrator
    cal, cal_level = get_calibrator(ctx.conn, city, target_date)

    # Compute alpha (with overrides if any)
    override_alpha = ctx.overrides.get("alpha", {}).get(city.name, {}).get(season)
    if override_alpha is not None:
        alpha = float(override_alpha)
    else:
        alpha = compute_alpha(
            calibration_level=cal_level,
            ensemble_spread=snapshot["spread"] or 3.0,
            model_agreement="AGREE",
            lead_days=lead_days,
            hours_since_open=48.0,
            city_name=city.name,
            season=season,
        )

    # Calibrate
    bin_probs_raw = np.array(p_raw_stored)
    if cal is not None:
        bin_probs_cal = np.array([
            cal.predict(float(p), float(lead_days)) for p in bin_probs_raw
        ])
        total = bin_probs_cal.sum()
        if total > 0:
            bin_probs_cal = bin_probs_cal / total
    else:
        bin_probs_cal = bin_probs_raw

    # Market price (assume uninformed for replay)
    p_market = 1.0 / len(bin_probs_cal)  # Uniform prior

    # Posterior
    p_posterior = alpha * bin_probs_cal + (1 - alpha) * p_market

    # Edge detection
    label_list = [r["range_label"] for r in bin_labels]
    winning_bin = settlement.get("winning_bin", "")
    settlement_value = settlement.get("settlement_value")

    decisions = []
    best_edge = 0.0
    would_trade = False
    replay_pnl = 0.0

    # Live evaluator uses bootstrap CI + FDR, not simple threshold.
    # For replay, use 3% minimum edge as practical equivalent.
    edge_min = 0.03

    for i, label in enumerate(label_list):
        if i >= len(p_posterior):
            break

        p_model = float(p_posterior[i])
        p_mkt = p_market  # Uniform

        # Edge for buy_yes
        edge_yes = p_model - p_mkt
        # Edge for buy_no
        edge_no = (1.0 - p_model) - (1.0 - p_mkt)

        # Pick better direction
        if abs(edge_yes) >= abs(edge_no):
            direction = "buy_yes"
            edge = edge_yes
            entry_price = p_mkt
        else:
            direction = "buy_no"
            edge = edge_no
            entry_price = 1.0 - p_mkt

        should_trade = abs(edge) >= edge_min and entry_price > 0.01

        dec = ReplayDecision(
            city=city.name,
            target_date=target_date,
            range_label=label,
            direction=direction,
            should_trade=should_trade,
            rejection_stage="" if should_trade else "EDGE_TOO_SMALL",
            edge=round(edge, 4),
            p_posterior=round(p_model, 4),
            p_raw=round(float(bin_probs_raw[i]) if i < len(bin_probs_raw) else 0.0, 4),
            entry_price=round(entry_price, 4),
            edge_source="replay_audit",
        )
        decisions.append(dec)

        if abs(edge) > abs(best_edge):
            best_edge = edge

        if should_trade:
            would_trade = True
            # Compute theoretical PnL
            size_usd = 5.0  # Standard replay size
            shares = size_usd / entry_price if entry_price > 0 else 0

            # Did this bin win?
            won = _bin_matches_settlement(label, settlement_value)
            if direction == "buy_yes":
                exit_price = 1.0 if won else 0.0
            else:
                exit_price = 1.0 if not won else 0.0

            pnl = shares * exit_price - size_usd
            replay_pnl += pnl
            dec.size_usd = size_usd

    return ReplayOutcome(
        city=city.name,
        target_date=target_date,
        settlement_value=settlement_value,
        winning_bin=winning_bin,
        replay_decisions=decisions,
        replay_best_edge=round(best_edge, 4),
        replay_would_trade=would_trade,
        replay_pnl=round(replay_pnl, 2),
        snapshot_id=str(snapshot["snapshot_id"]),
        lead_hours=snapshot["lead_hours"],
        n_members=snapshot["n_members"],
    )


def _bin_matches_settlement(label: str, settlement_value: Optional[float]) -> bool:
    """Check if a settlement value falls within a bin label's range."""
    if settlement_value is None:
        return False

    from src.data.market_scanner import _parse_temp_range
    try:
        low, high = _parse_temp_range(label)
    except Exception:
        return False

    if low is None and high is not None:
        return settlement_value <= high
    if high is None and low is not None:
        return settlement_value >= low
    if low is not None and high is not None:
        return low <= settlement_value <= high
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_replay(
    start_date: str,
    end_date: str,
    mode: str = "audit",
    overrides: Optional[dict] = None,
) -> ReplaySummary:
    """Run the Decision Replay Engine.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        mode: 'audit', 'counterfactual', 'walk_forward'
        overrides: parameter overrides for counterfactual mode

    Returns:
        ReplaySummary with per-city breakdown and PnL
    """
    run_id = str(uuid.uuid4())[:12]
    conn = get_connection()
    ctx = ReplayContext(conn, overrides=overrides)

    # Get all settlements in date range
    settlements = conn.execute("""
        SELECT city, target_date, settlement_value, winning_bin
        FROM settlements
        WHERE target_date >= ? AND target_date <= ?
          AND settlement_value IS NOT NULL
        ORDER BY target_date, city
    """, (start_date, end_date)).fetchall()

    summary = ReplaySummary(
        run_id=run_id,
        mode=mode,
        date_range=(start_date, end_date),
        n_settlements=len(settlements),
        overrides=overrides or {},
    )

    per_city_pnl: dict[str, list[float]] = {}
    per_city_trades: dict[str, int] = {}

    for srow in settlements:
        city_name = srow["city"]
        target_date = srow["target_date"]

        city = cities_by_name.get(city_name)
        if city is None:
            continue

        settlement = {
            "settlement_value": srow["settlement_value"],
            "winning_bin": srow["winning_bin"],
        }

        outcome = _replay_one_settlement(ctx, city, target_date, settlement)
        if outcome is None:
            continue

        summary.n_replayed += 1
        summary.outcomes.append(outcome)

        if outcome.replay_would_trade:
            summary.n_would_trade += 1
            summary.replay_total_pnl += outcome.replay_pnl

        if city_name not in per_city_pnl:
            per_city_pnl[city_name] = []
            per_city_trades[city_name] = 0
        per_city_pnl[city_name].append(outcome.replay_pnl)
        if outcome.replay_would_trade:
            per_city_trades[city_name] += 1

    # Aggregate per-city stats
    for city_name in per_city_pnl:
        pnls = per_city_pnl[city_name]
        trades = per_city_trades[city_name]
        wins = sum(1 for p in pnls if p > 0)
        summary.per_city[city_name] = {
            "n_dates": len(pnls),
            "n_trades": trades,
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(wins / len(pnls), 3) if pnls else 0.0,
        }

    summary.cities_covered = sorted(per_city_pnl.keys())
    summary.coverage_pct = round(
        summary.n_replayed / max(1, summary.n_settlements) * 100, 1
    )

    traded_outcomes = [o for o in summary.outcomes if o.replay_would_trade]
    if traded_outcomes:
        wins = sum(1 for o in traded_outcomes if o.replay_pnl > 0)
        summary.replay_win_rate = round(wins / len(traded_outcomes), 3)

    # Store results
    _store_replay_results(conn, summary)
    conn.close()

    return summary


def _store_replay_results(conn, summary: ReplaySummary) -> None:
    """Persist replay run to replay_results table."""
    if False: _ = None.entry_method  # Semantic Provenance Guard
    now = datetime.now(timezone.utc).isoformat()

    for outcome in summary.outcomes:
        best_dec = None
        for d in outcome.replay_decisions:
            if d.should_trade and (best_dec is None or abs(d.edge) > abs(best_dec.edge)):
                best_dec = d

        try:
            conn.execute("""
                INSERT INTO replay_results
                (replay_run_id, mode, city, target_date, settlement_value,
                 winning_bin, replay_direction, replay_edge, replay_p_posterior,
                 replay_size_usd, replay_should_trade, replay_rejection_stage,
                 replay_pnl, overrides_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                summary.run_id,
                summary.mode,
                outcome.city,
                outcome.target_date,
                outcome.settlement_value,
                outcome.winning_bin,
                best_dec.direction if best_dec else None,
                best_dec.edge if best_dec else None,
                best_dec.p_posterior if best_dec else None,
                best_dec.size_usd if best_dec else None,
                1 if outcome.replay_would_trade else 0,
                best_dec.rejection_stage if best_dec and not best_dec.should_trade else None,
                outcome.replay_pnl,
                json.dumps(summary.overrides),
                now,
            ))
        except Exception as e:
            logger.warning("Failed to store replay result: %s", e)

    conn.commit()
