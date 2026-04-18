# REPLAY IS APPROXIMATE AUDIT ONLY. See docs/authority/zeus_live_backtest_shadow_boundary.md
"""Decision Replay Engine: replay historical facts without promoting them to truth.

Backtest has two separate proof chains:

1. Weather/forecast skill can be scored from point-in-time forecasts against WU
   settlement_value. This produces Brier/log-loss/accuracy metrics, not dollars.
2. Trading economics require a real market price vector at decision time or real
   trade history. No market price linkage means hypothetical PnL is unavailable.

Legacy audit/counterfactual/walk_forward remain compatibility surfaces. The
derived lanes write to zeus_backtest.db with diagnostic_non_promotion authority.
"""

import json
import logging
import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np

from src.config import City, cities_by_name, edge_n_bootstrap, settings
from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.state.db import (
    get_backtest_connection,
    get_trade_connection_with_world,
    init_backtest_schema,
)
from src.types import Bin
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)
BACKTEST_AUTHORITY_SCOPE = "diagnostic_non_promotion"
WU_SWEEP_LANE = "wu_settlement_sweep"
TRADE_HISTORY_LANE = "trade_history_audit"
PROBABILITY_EPS = 1e-12
DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({
    "shadow_signals",
    "ensemble_snapshots.available_at",
    "forecasts_table_synthetic",
})


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
    decision_reference_source: str = ""
    hours_since_open_source: str = ""
    hours_since_open_fallback: bool = False


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
    limitations: dict = field(default_factory=dict)


def _market_price_linkage_limitations(
    *,
    n_replayed: int,
    market_price_linked_subjects: int,
    market_price_unavailable_subjects: int,
) -> dict:
    full_linkage = n_replayed > 0 and market_price_linked_subjects == n_replayed
    partial_linkage = 0 < market_price_linked_subjects < n_replayed
    if full_linkage:
        state = "full"
        pnl_scope = "all_replayed_subjects"
        unavailable_reason = ""
    elif partial_linkage:
        state = "partial"
        pnl_scope = "partial_market_price_linkage"
        unavailable_reason = "partial_market_price_linkage"
    else:
        state = "none"
        pnl_scope = "no_market_price_linkage"
        unavailable_reason = "market_price_unavailable"
    return {
        "market_price_linkage": full_linkage,
        "market_price_linkage_state": state,
        "market_price_linked_subjects": market_price_linked_subjects,
        "market_price_unavailable_subjects": market_price_unavailable_subjects,
        "pnl_requires_market_price_linkage": True,
        "pnl_available": full_linkage,
        "pnl_subject_scope": pnl_scope,
        "pnl_unavailable_reason": unavailable_reason,
    }


def _missing_parity_dimensions(full_linkage: bool) -> list[str]:
    """Return names of parity dimensions not satisfied by replay."""
    return [
        dim for dim, present in [
            ("market_price_linkage", full_linkage),
            ("active_sizing_parity", False),   # replay uses flat $5, not Kelly
            ("selection_family_parity", False), # replay has no bootstrap/FDR
        ] if not present
    ]


def _replay_provenance_limitations(outcomes: list[ReplayOutcome]) -> dict:
    decision_reference_source_counts = Counter(
        outcome.decision_reference_source or "UNKNOWN"
        for outcome in outcomes
    )
    hours_since_open_source_counts = Counter(
        outcome.hours_since_open_source or "UNKNOWN"
        for outcome in outcomes
    )
    fallback_subjects = sum(1 for outcome in outcomes if outcome.hours_since_open_fallback)
    diagnostic_subjects = sum(
        1
        for outcome in outcomes
        if outcome.decision_reference_source in DIAGNOSTIC_REPLAY_REFERENCE_SOURCES
    )
    total_subjects = len(outcomes)
    return {
        "decision_reference_source_counts": dict(sorted(decision_reference_source_counts.items())),
        "hours_since_open_source_counts": dict(sorted(hours_since_open_source_counts.items())),
        "diagnostic_replay_subjects": diagnostic_subjects,
        "diagnostic_replay_subject_rate": round(
            diagnostic_subjects / max(1, total_subjects),
            6,
        ),
        "hours_since_open_fallback_subjects": fallback_subjects,
        "hours_since_open_fallback_rate": round(
            fallback_subjects / max(1, total_subjects),
            6,
        ),
    }


@dataclass(frozen=True)
class TradeSubjectCandidate:
    position_id: str
    source: str
    rank: int
    sequence_no: int = 0


@dataclass(frozen=True)
class TradeHistorySubject:
    position_id: str | None
    status: str
    source: str
    missing_reason: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def subject_id(self) -> str:
        return self.position_id or self.missing_reason or "unresolved"


# ---------------------------------------------------------------------------
# ReplayContext: replaces live API calls with historical data
# ---------------------------------------------------------------------------

class ReplayContext:
    """Provides historical data to the evaluator, replacing live API calls.

    This is NOT a mock — it returns real stored data from the database.
    """

    def __init__(self, conn, overrides: Optional[dict] = None, *, allow_snapshot_only_reference: bool = False):
        self.conn = conn
        self.overrides = overrides or {}
        self.allow_snapshot_only_reference = allow_snapshot_only_reference
        self._snapshot_cache: dict[tuple[str, str, str, str], dict] = {}
        self._decision_ref_cache: dict[tuple[str, str, str], Optional[dict]] = {}
        import sqlite3
        try:
            self.conn.execute("SELECT 1 FROM world.ensemble_snapshots LIMIT 0")
            self._sp = "world."  # world DB attached
        except sqlite3.OperationalError as exc:
            try:
                self.conn.execute("SELECT 1 FROM ensemble_snapshots LIMIT 0")
                self._sp = ""  # monolithic DB (tests)
            except sqlite3.OperationalError:
                raise RuntimeError("Replay topology error: ensemble_snapshots neither in world attach nor local main schema.") from exc

    def _forecast_rows_for(self, city_name: str, target_date: str, temperature_metric: str = "high") -> list[dict]:
        """Load diagnostic historical forecast rows for a replay fallback.

        Phase 7: migrate from legacy 'forecasts' table to 'historical_forecasts_v2'
        (which carries a 'temperature_metric' column) and add 'AND temperature_metric = ?'
        to the WHERE clause at that point (B093 half-2).
        """
        try:
            rows = self.conn.execute(
                f"""
                SELECT source, forecast_basis_date, forecast_issue_time, lead_days,
                       forecast_high, forecast_low, temp_unit
                FROM {self._sp}forecasts
                WHERE city = ?
                  AND target_date = ?
                  AND forecast_high IS NOT NULL
                ORDER BY lead_days ASC, source ASC, forecast_basis_date ASC
                """,
                (city_name, target_date),
            ).fetchall()
        except Exception:
            return []
        return [dict(row) for row in rows]

    def _forecast_reference_for(self, city_name: str, target_date: str, temperature_metric: str = "high") -> Optional[dict]:
        rows = self._forecast_rows_for(city_name, target_date, temperature_metric=temperature_metric)
        if not rows:
            return None
        positive_leads = [float(row["lead_days"]) for row in rows if float(row["lead_days"] or 0) > 0]
        selected_lead = min(positive_leads) if positive_leads else min(float(row["lead_days"] or 0) for row in rows)
        selected = [row for row in rows if float(row["lead_days"] or 0) == selected_lead]

        city = cities_by_name.get(city_name)
        if city is None:
            return None
        bins = _typed_bins_for_city_date(self, city, target_date)
        if not bins:
            return None
        forecast_col = "forecast_low" if temperature_metric == "low" else "forecast_high"
        member_values = [float(row[forecast_col]) for row in selected if row.get(forecast_col) is not None]
        p_raw = _probability_vector_from_values(member_values, bins, city.settlement_unit)
        sources = sorted({str(row["source"]) for row in selected})
        return {
            "trade_id": "",
            "decision_time": None,
            "snapshot_id": f"forecast_rows:{city_name}:{target_date}:lead={selected_lead:g}",
            "source": "forecasts_table_synthetic",
            "decision_reference_source": "forecasts_table_synthetic",
            "decision_time_status": "SYNTHETIC_MIDDAY",
            "bin_labels": [bin.label for bin in bins],
            "p_raw_vector": p_raw.tolist(),
            "p_cal_vector": [],
            "p_market_vector": [],
            "agreement": "UNKNOWN",
            "forecast_rows": selected,
            "forecast_sources": sources,
            "lead_days": selected_lead,
        }

    def _forecast_snapshot_for(self, city_name: str, target_date: str, snapshot_id: str, temperature_metric: str = "high") -> Optional[dict]:
        ref = self._forecast_reference_for(city_name, target_date, temperature_metric=temperature_metric)
        if ref is None or ref["snapshot_id"] != snapshot_id:
            return None
        forecast_col = "forecast_low" if temperature_metric == "low" else "forecast_high"
        member_values = np.array(
            [float(row[forecast_col]) for row in ref["forecast_rows"] if row.get(forecast_col) is not None],
            dtype=np.float64,
        )
        if len(member_values) == 0:
            return None
        basis_dates = sorted({str(row.get("forecast_basis_date") or "") for row in ref["forecast_rows"] if row.get("forecast_basis_date")})
        issue_time = f"{basis_dates[-1]}T12:00:00+00:00" if basis_dates else ref["decision_time"]
        return {
            "snapshot_id": snapshot_id,
            "member_maxes": member_values,
            "p_raw_stored": ref["p_raw_vector"],
            "lead_hours": float(ref["lead_days"]) * 24.0,
            "spread": float(np.std(member_values)),
            "is_bimodal": False,
            "model": "forecast_rows",
            "issue_time": issue_time,
            "valid_time": f"{target_date}T00:00:00+00:00",
            "available_at": ref["decision_time"],
            "fetch_time": ref["decision_time"],
            "data_version": "diagnostic_forecast_rows.v1",
            "n_members": len(member_values),
        }

    def get_decision_reference_for(self, city_name: str, target_date: str, temperature_metric: str = "high") -> Optional[dict]:
        """Get an actual decision-time reference for replay.

        Replay must not invent a decision timestamp. If no actual decision exists
        for this settlement, replay returns no coverage rather than peeking at
        future-available data.
        """
        key = (city_name, target_date, temperature_metric)
        if key in self._decision_ref_cache:
            return self._decision_ref_cache[key]

        row = self.conn.execute(
            f"""
            SELECT td.trade_id, td.timestamp AS decision_time,
                   td.forecast_snapshot_id AS snapshot_id, td.market_hours_open
            FROM trade_decisions td
            JOIN {self._sp}ensemble_snapshots es ON es.snapshot_id = td.forecast_snapshot_id
            WHERE es.city = ? AND es.target_date = ?
              AND td.forecast_snapshot_id IS NOT NULL
            ORDER BY datetime(td.timestamp) ASC, td.trade_id ASC
            LIMIT 1
            """,
            (city_name, target_date),
        ).fetchone()

        if row is not None:
            result = {
                "trade_id": row["trade_id"],
                "decision_time": row["decision_time"],
                "snapshot_id": row["snapshot_id"],
                "market_hours_open": row["market_hours_open"],
                "source": "trade_decisions",
                "decision_reference_source": "historical_decision",
                "decision_time_status": "OK",
            }
            self._decision_ref_cache[key] = result
            return result

        log_rows = self.conn.execute(
            """
            SELECT started_at, artifact_json
            FROM decision_log
            ORDER BY datetime(started_at) ASC
            """
        ).fetchall()
        best = None
        for log_row in log_rows:
            try:
                artifact = json.loads(log_row["artifact_json"])
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError) as exc:
                # B094 [critic amendment]: narrow from bare Exception.
                # Persisted artifact_json is untrusted data, so include
                # UnicodeDecodeError (bad encoding) and RecursionError
                # (pathologically deep nesting) alongside the common
                # JSONDecodeError/TypeError. A corrupt artifact_json
                # must not kill the whole batch; log and continue so
                # the rest of decision_log can still replay.
                logger.warning(
                    "REPLAY_ARTIFACT_JSON_CORRUPT: started_at=%s error=%s",
                    log_row["started_at"],
                    exc,
                )
                continue

            for trade_case in artifact.get("trade_cases", []) or []:
                if trade_case.get("city") == city_name and trade_case.get("target_date") == target_date:
                    snapshot_id = trade_case.get("decision_snapshot_id")
                    if snapshot_id:
                        best = {
                            "trade_id": trade_case.get("trade_id", ""),
                            "decision_time": trade_case.get("timestamp") or log_row["started_at"],
                            "snapshot_id": int(snapshot_id) if str(snapshot_id).isdigit() else snapshot_id,
                            "source": "decision_log.trade_cases",
                            "bin_labels": list(trade_case.get("bin_labels") or []),
                            "p_raw_vector": list(trade_case.get("p_raw_vector") or []),
                            "p_cal_vector": list(trade_case.get("p_cal_vector") or []),
                            "p_market_vector": list(trade_case.get("p_market_vector") or []),
                            "alpha": float(trade_case.get("alpha", 0.0) or 0.0),
                            "market_hours_open": trade_case.get("market_hours_open"),
                            "agreement": trade_case.get("agreement", ""),
                            "should_trade": True,
                        }
                        break
            if best is not None:
                break

            for no_trade in artifact.get("no_trade_cases", []) or []:
                if no_trade.get("city") == city_name and no_trade.get("target_date") == target_date:
                    snapshot_id = no_trade.get("decision_snapshot_id")
                    if snapshot_id:
                        best = {
                            "trade_id": "",
                            "decision_time": no_trade.get("timestamp") or log_row["started_at"],
                            "snapshot_id": int(snapshot_id) if str(snapshot_id).isdigit() else snapshot_id,
                            "source": "decision_log.no_trade_cases",
                            "bin_labels": list(no_trade.get("bin_labels") or []),
                            "p_raw_vector": list(no_trade.get("p_raw_vector") or []),
                            "p_cal_vector": list(no_trade.get("p_cal_vector") or []),
                            "p_market_vector": list(no_trade.get("p_market_vector") or []),
                            "alpha": float(no_trade.get("alpha", 0.0) or 0.0),
                            "market_hours_open": no_trade.get("market_hours_open"),
                            "agreement": no_trade.get("agreement", ""),
                            "should_trade": False,
                            "rejection_stage": no_trade.get("rejection_stage", ""),
                        }
                        break
            if best is not None:
                break

        if best is None and self.allow_snapshot_only_reference:
            try:
                shadow = self.conn.execute(
                    f"""
                    SELECT timestamp, decision_snapshot_id, p_raw_json, p_cal_json, edges_json
                    FROM {self._sp}shadow_signals
                    WHERE city = ? AND target_date = ?
                    ORDER BY datetime(timestamp) ASC
                    LIMIT 1
                    """,
                    (city_name, target_date),
                ).fetchone()
            except Exception:
                shadow = None
            if shadow is not None and shadow["decision_snapshot_id"]:
                try:
                    edges_payload = json.loads(shadow["edges_json"]) if shadow["edges_json"] else []
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError) as exc:
                    logger.warning(
                        "REPLAY_SHADOW_EDGES_JSON_CORRUPT: timestamp=%s error=%s",
                        shadow["timestamp"],
                        exc,
                    )
                    edges_payload = []
                bin_labels = [edge.get("bin_label", "") for edge in edges_payload if edge.get("bin_label")]
                # B094 [critic amendment]: protect the two previously-
                # unwrapped json.loads calls below. Include UnicodeDecodeError
                # and RecursionError since persisted data is untrusted.
                try:
                    p_raw_vector = json.loads(shadow["p_raw_json"]) if shadow["p_raw_json"] else []
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError) as exc:
                    logger.warning(
                        "REPLAY_SHADOW_P_RAW_JSON_CORRUPT: timestamp=%s error=%s",
                        shadow["timestamp"],
                        exc,
                    )
                    p_raw_vector = []
                try:
                    p_cal_vector = json.loads(shadow["p_cal_json"]) if shadow["p_cal_json"] else []
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError) as exc:
                    logger.warning(
                        "REPLAY_SHADOW_P_CAL_JSON_CORRUPT: timestamp=%s error=%s",
                        shadow["timestamp"],
                        exc,
                    )
                    p_cal_vector = []
                best = {
                    "trade_id": "",
                    "decision_time": shadow["timestamp"],
                    "snapshot_id": int(shadow["decision_snapshot_id"]) if str(shadow["decision_snapshot_id"]).isdigit() else shadow["decision_snapshot_id"],
                    "source": "shadow_signals",
                    "bin_labels": bin_labels,
                    "p_raw_vector": p_raw_vector,
                    "p_cal_vector": p_cal_vector,
                    "p_market_vector": [],
                }

        if best is None and self.allow_snapshot_only_reference:
            row = self.conn.execute(
                f"""
                SELECT snapshot_id, available_at
                FROM {self._sp}ensemble_snapshots
                WHERE city = ? AND target_date = ? AND p_raw_json IS NOT NULL
                ORDER BY datetime(available_at) ASC
                LIMIT 1
                """,
                (city_name, target_date),
            ).fetchone()
            if row is not None:
                best = {
                    "trade_id": "",
                    "decision_time": row["available_at"],
                    "snapshot_id": row["snapshot_id"],
                    "source": "ensemble_snapshots.available_at",
                }

        if best is None and self.allow_snapshot_only_reference:
            best = self._forecast_reference_for(city_name, target_date, temperature_metric=temperature_metric)

        result = best
        self._decision_ref_cache[key] = result
        return result

    def get_snapshot_for(
        self,
        city_name: str,
        target_date: str,
        *,
        decision_time: str,
        snapshot_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Get the newest snapshot that was actually available at decision time."""
        key = (city_name, target_date, str(decision_time or ""), str(snapshot_id or ""))
        if key in self._snapshot_cache:
            return self._snapshot_cache[key]

        if snapshot_id is not None and str(snapshot_id).startswith("forecast_rows:"):
            result = self._forecast_snapshot_for(city_name, target_date, str(snapshot_id))
            self._snapshot_cache[key] = result
            return result

        if snapshot_id is not None:
            row = self.conn.execute(
                f"""
                SELECT snapshot_id, members_json, p_raw_json, lead_hours, spread, is_bimodal,
                       model_version, issue_time, valid_time, available_at, fetch_time, data_version
                FROM {self._sp}ensemble_snapshots
                WHERE snapshot_id = ?
                  AND city = ?
                  AND target_date = ?
                  AND datetime(available_at) <= datetime(?)
                LIMIT 1
                """,
                (snapshot_id, city_name, target_date, decision_time),
            ).fetchone()
        else:
            row = self.conn.execute(
                f"""
                SELECT snapshot_id, members_json, p_raw_json, lead_hours, spread, is_bimodal,
                       model_version, issue_time, valid_time, available_at, fetch_time, data_version
                FROM {self._sp}ensemble_snapshots
                WHERE city = ?
                  AND target_date = ?
                  AND datetime(available_at) <= datetime(?)
                ORDER BY datetime(available_at) DESC, datetime(fetch_time) DESC
                LIMIT 1
                """,
                (city_name, target_date, decision_time),
            ).fetchone()

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
            "valid_time": row["valid_time"],
            "available_at": row["available_at"],
            "fetch_time": row["fetch_time"],
            "data_version": row["data_version"],
            "n_members": len(members),
        }
        self._snapshot_cache[key] = result
        return result

    def get_market_price(self, city_name: str, target_date: str) -> float:
        """Get historical market mid-price from token_price_log.

        Returns np.nan if no data to explicitly convey historical omission.
        """
        import math
        return float('nan')

    def get_settlement(self, city_name: str, target_date: str) -> Optional[dict]:
        """Get settlement outcome for scoring."""
        row = self.conn.execute(
            f"SELECT settlement_value, winning_bin FROM {self._sp}settlements "
            "WHERE city = ? AND target_date = ?",
            (city_name, target_date),
        ).fetchone()
        if row:
            return {
                "settlement_value": row["settlement_value"],
                "winning_bin": row["winning_bin"],
            }
        return None


def bin_from_range_label(label: str, unit: str) -> Bin | None:
    """Parse a market range label into a typed Bin."""
    from src.data.market_scanner import _parse_temp_range

    try:
        low, high = _parse_temp_range(label)
        return Bin(low=low, high=high, label=label, unit=unit)
    except Exception:
        return None


def derive_outcome_from_settlement_value(
    settlement_value: float | None,
    bin: Bin,
    unit: str,
    round_fn: callable = None,
) -> bool:
    """Derive YES outcome from settlement value and a typed bin.

    Uses injected round_fn if provided (e.g., oracle_truncate for HKO),
    otherwise falls back to WMO asymmetric half-up rounding.
    """
    if settlement_value is None:
        raise ValueError("settlement_value is required")
    if bin.unit != unit:
        raise ValueError(f"settlement unit mismatch: bin={bin.unit} settlement={unit}")

    if round_fn is not None:
        value = float(round_fn(np.array([float(settlement_value)]))[0])
    else:
        value = round_wmo_half_up_value(float(settlement_value))
    if bin.low is None and bin.high is not None:
        return value <= bin.high
    if bin.high is None and bin.low is not None:
        return value >= bin.low
    if bin.low is not None and bin.high is not None:
        return bin.low <= value <= bin.high
    return False


def _probability_vector_from_values(values: list[float], bins: list[Bin], unit: str) -> np.ndarray:
    """Build a diagnostic probability vector from native-unit forecast highs."""
    if not values or not bins:
        return np.array([], dtype=float)
    probs = []
    for bin in bins:
        hits = sum(
            1 for value in values
            if derive_outcome_from_settlement_value(value, bin, unit)
        )
        probs.append(hits / len(values))
    total = sum(probs)
    if total > 0:
        probs = [p / total for p in probs]
    return np.array(probs, dtype=float)


def _clamp_probability(value: float) -> float:
    return min(1.0 - PROBABILITY_EPS, max(PROBABILITY_EPS, float(value)))


def _binary_brier(probability: float, outcome: bool) -> float:
    p = _clamp_probability(probability)
    y = 1.0 if outcome else 0.0
    return (p - y) ** 2


def _binary_log_loss(probability: float, outcome: bool) -> float:
    p = _clamp_probability(probability)
    return -math.log(p if outcome else 1.0 - p)


def _forecast_reference_id(row) -> str:
    group_id = row["decision_group_id"] if "decision_group_id" in row.keys() else None
    if group_id:
        return str(group_id)
    available_at = str(row["forecast_available_at"] or "UNKNOWN")
    lead_days = float(row["lead_days"] or 0.0)
    return f"calibration_pair:{available_at}:lead={lead_days:g}"


def _calibration_buckets(samples: list[dict]) -> list[dict]:
    buckets = []
    for idx in range(10):
        low = idx / 10
        high = (idx + 1) / 10
        label = f"{low:.1f}-{high:.1f}"
        bucket_samples = [
            s for s in samples
            if min(9, int(float(s["p_raw"]) * 10)) == idx
        ]
        if not bucket_samples:
            continue
        n = len(bucket_samples)
        buckets.append(
            {
                "bucket": label,
                "n": n,
                "mean_p": round(sum(float(s["p_raw"]) for s in bucket_samples) / n, 6),
                "actual_rate": round(sum(1 for s in bucket_samples if s["outcome"]) / n, 6),
                "brier": round(sum(float(s["brier"]) for s in bucket_samples) / n, 6),
            }
        )
    return buckets


def _skill_score(model_score: float, reference_score: float) -> float | None:
    if reference_score <= PROBABILITY_EPS:
        return None
    return round(1.0 - (model_score / reference_score), 6)


def _summarize_binary_samples(samples: list[dict]) -> dict:
    if not samples:
        return {
            "forecast_skill_rows": 0,
            "actual_yes_rows": 0,
            "actual_no_rows": 0,
            "positive_rate": None,
            "brier": None,
            "climatology_brier": None,
            "brier_skill_score_vs_climatology": None,
            "log_loss": None,
            "climatology_log_loss": None,
            "log_loss_skill_score_vs_climatology": None,
            "threshold_hits": 0,
            "threshold_total": 0,
            "accuracy_at_0_5": None,
            "majority_baseline_accuracy": None,
            "positive_predictions": 0,
            "positive_prediction_hits": 0,
            "positive_prediction_precision": None,
            "negative_predictions": 0,
            "negative_prediction_hits": 0,
            "negative_prediction_precision": None,
            "mean_p_raw": None,
            "mean_p_raw_on_actual_yes": None,
            "mean_p_raw_on_actual_no": None,
            "calibration_buckets": [],
        }
    n = len(samples)
    yes_rows = sum(1 for s in samples if s["outcome"])
    no_rows = n - yes_rows
    positive_rate = yes_rows / n
    brier = sum(float(s["brier"]) for s in samples) / n
    log_loss = sum(float(s["log_loss"]) for s in samples) / n
    climatology_brier = positive_rate * (1.0 - positive_rate)
    clipped_rate = _clamp_probability(positive_rate)
    climatology_log_loss = -(
        positive_rate * math.log(clipped_rate)
        + (1.0 - positive_rate) * math.log(1.0 - clipped_rate)
    )
    threshold_hits = sum(1 for s in samples if s["threshold_correct"])
    positive_predictions = [s for s in samples if s["p_raw"] >= 0.5]
    negative_predictions = [s for s in samples if s["p_raw"] < 0.5]
    positive_prediction_hits = sum(1 for s in positive_predictions if s["outcome"])
    negative_prediction_hits = sum(1 for s in negative_predictions if not s["outcome"])
    actual_yes_samples = [s for s in samples if s["outcome"]]
    actual_no_samples = [s for s in samples if not s["outcome"]]
    return {
        "forecast_skill_rows": n,
        "actual_yes_rows": yes_rows,
        "actual_no_rows": no_rows,
        "positive_rate": round(positive_rate, 6),
        "brier": round(brier, 6),
        "climatology_brier": round(climatology_brier, 6),
        "brier_skill_score_vs_climatology": _skill_score(brier, climatology_brier),
        "log_loss": round(log_loss, 6),
        "climatology_log_loss": round(climatology_log_loss, 6),
        "log_loss_skill_score_vs_climatology": _skill_score(log_loss, climatology_log_loss),
        "threshold_hits": threshold_hits,
        "threshold_total": n,
        "accuracy_at_0_5": round(threshold_hits / n, 4),
        "majority_baseline_accuracy": round(max(yes_rows, no_rows) / n, 4),
        "positive_predictions": len(positive_predictions),
        "positive_prediction_hits": positive_prediction_hits,
        "positive_prediction_precision": (
            round(positive_prediction_hits / len(positive_predictions), 4)
            if positive_predictions else None
        ),
        "negative_predictions": len(negative_predictions),
        "negative_prediction_hits": negative_prediction_hits,
        "negative_prediction_precision": (
            round(negative_prediction_hits / len(negative_predictions), 4)
            if negative_predictions else None
        ),
        "mean_p_raw": round(sum(float(s["p_raw"]) for s in samples) / n, 6),
        "mean_p_raw_on_actual_yes": (
            round(sum(float(s["p_raw"]) for s in actual_yes_samples) / len(actual_yes_samples), 6)
            if actual_yes_samples else None
        ),
        "mean_p_raw_on_actual_no": (
            round(sum(float(s["p_raw"]) for s in actual_no_samples) / len(actual_no_samples), 6)
            if actual_no_samples else None
        ),
        "calibration_buckets": _calibration_buckets(samples),
    }


def _group_integrity(
    group_candidates: dict[tuple[str, str, str], list[tuple[str, float, bool]]],
) -> dict:
    reason_counts: dict[str, int] = {}
    examples = []
    valid_keys = set()
    for key, candidates in group_candidates.items():
        labels = [label for label, _, _ in candidates]
        p_sum = sum(float(p) for _, p, _ in candidates)
        yes_count = sum(1 for _, _, hit in candidates if hit)
        reasons = []
        if len(labels) != len(set(labels)):
            reasons.append("duplicate_labels")
        if abs(p_sum - 1.0) > 0.02:
            reasons.append("p_sum_not_one")
        if yes_count != 1:
            reasons.append("yes_count_not_one")
        if reasons:
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if len(examples) < 5:
                examples.append(
                    {
                        "city": key[0],
                        "target_date": key[1],
                        "forecast_reference_id": key[2],
                        "n_rows": len(candidates),
                        "p_sum": round(p_sum, 6),
                        "yes_count": yes_count,
                        "reasons": reasons,
                    }
                )
        else:
            valid_keys.add(key)
    total = len(group_candidates)
    valid = len(valid_keys)
    return {
        "total_probability_groups": total,
        "valid_probability_groups": valid,
        "invalid_probability_groups": total - valid,
        "invalid_probability_group_reasons": dict(sorted(reason_counts.items())),
        "invalid_probability_group_examples": examples,
        "valid_group_keys": valid_keys,
    }


def _top_hits(
    group_candidates: dict[tuple[str, str, str], list[tuple[str, float, bool]]],
    *,
    k: int,
    valid_group_keys: set[tuple[str, str, str]],
) -> list[bool]:
    hits = []
    for key, candidates in group_candidates.items():
        if key not in valid_group_keys:
            continue
        ranked = sorted(candidates, key=lambda item: item[1], reverse=True)
        hits.append(any(hit for _, _, hit in ranked[:k]))
    return hits


def _summarize_forecast_skill(
    samples: list[dict],
    group_candidates: dict[tuple[str, str, str], list[tuple[str, float, bool]]],
) -> dict:
    group_integrity = _group_integrity(group_candidates)
    if not samples:
        return {
            "forecast_skill_rows": 0,
            "actual_yes_rows": 0,
            "actual_no_rows": 0,
            "positive_rate": None,
            "brier": None,
            "climatology_brier": None,
            "brier_skill_score_vs_climatology": None,
            "log_loss": None,
            "climatology_log_loss": None,
            "log_loss_skill_score_vs_climatology": None,
            "threshold_hits": 0,
            "threshold_total": 0,
            "accuracy_at_0_5": None,
            "majority_baseline_accuracy": None,
            "positive_predictions": 0,
            "positive_prediction_hits": 0,
            "positive_prediction_precision": None,
            "negative_predictions": 0,
            "negative_prediction_hits": 0,
            "negative_prediction_precision": None,
            "top_bin_hits": 0,
            "top_bin_total": 0,
            "top_bin_accuracy": None,
            "top3_bin_hits": 0,
            "top3_bin_total": 0,
            "top3_bin_accuracy": None,
            "top_bin_groups": 0,
            "primary_multiclass_metrics_interpretable": False,
            "probability_group_integrity": {
                key: value
                for key, value in group_integrity.items()
                if key != "valid_group_keys"
            },
            "valid_group_forecast_skill": _summarize_binary_samples([]),
            "mean_p_raw": None,
            "mean_p_raw_on_actual_yes": None,
            "mean_p_raw_on_actual_no": None,
            "calibration_buckets": [],
        }
    valid_group_keys = group_integrity["valid_group_keys"]
    valid_samples = [s for s in samples if s.get("group_key") in valid_group_keys]
    top1_hits = _top_hits(group_candidates, k=1, valid_group_keys=valid_group_keys)
    top3_hits = _top_hits(group_candidates, k=3, valid_group_keys=valid_group_keys)
    binary_summary = _summarize_binary_samples(samples)
    return {
        **binary_summary,
        "top_bin_hits": sum(1 for hit in top1_hits if hit),
        "top_bin_total": len(top1_hits),
        "top_bin_accuracy": (
            round(sum(1 for hit in top1_hits if hit) / len(top1_hits), 4)
            if top1_hits else None
        ),
        "top3_bin_hits": sum(1 for hit in top3_hits if hit),
        "top3_bin_total": len(top3_hits),
        "top3_bin_accuracy": (
            round(sum(1 for hit in top3_hits if hit) / len(top3_hits), 4)
            if top3_hits else None
        ),
        "top_bin_groups": len(top1_hits),
        "primary_multiclass_metrics_interpretable": group_integrity["invalid_probability_groups"] == 0,
        "probability_group_integrity": {
            key: value
            for key, value in group_integrity.items()
            if key != "valid_group_keys"
        },
        "valid_group_forecast_skill": _summarize_binary_samples(valid_samples),
    }


def classify_outcome_divergence(
    wu_outcome: bool | None,
    actual_trade_outcome: int | bool | None,
) -> str:
    """Classify comparison between WU-derived and actual trade outcomes."""
    if wu_outcome is None:
        return "wu_missing"
    if actual_trade_outcome is None:
        return "trade_unresolved"
    actual = bool(actual_trade_outcome)
    if wu_outcome == actual:
        return "match"
    return "wu_win_trade_loss" if wu_outcome else "wu_loss_trade_win"


def select_canonical_trade_subject(
    candidates: list[TradeSubjectCandidate],
) -> TradeHistorySubject:
    """Resolve a canonical trade subject without guessing on ambiguity."""
    if not candidates:
        return TradeHistorySubject(
            position_id=None,
            status="unresolved",
            source="none",
            missing_reason="orphan_trade_decision",
        )
    ranked = sorted(candidates, key=lambda c: (c.rank, -c.sequence_no, c.source))
    best_rank = ranked[0].rank
    best_sequence = ranked[0].sequence_no
    best = [
        c for c in ranked
        if c.rank == best_rank and c.sequence_no == best_sequence
    ]
    position_ids = {c.position_id for c in best}
    if len(position_ids) != 1:
        return TradeHistorySubject(
            position_id=None,
            status="unresolved",
            source="multiple",
            missing_reason="ambiguous_trade_subject",
            aliases=tuple(sorted(position_ids)),
        )
    winner = best[0]
    return TradeHistorySubject(
        position_id=winner.position_id,
        status="resolved",
        source=winner.source,
        aliases=tuple(sorted({c.position_id for c in candidates})),
    )


def resolve_trade_history_subject(conn, subject_ref: str) -> TradeHistorySubject:
    """Resolve a subject reference to canonical position_id.

    `trade_decisions.trade_id` is a decision row id and is never accepted as the
    canonical backtest subject without a runtime_trade_id alias.
    """
    candidates: list[TradeSubjectCandidate] = []

    rows = conn.execute(
        """
        SELECT position_id
        FROM outcome_fact
        WHERE position_id = ? AND (settled_at IS NOT NULL OR outcome IS NOT NULL)
        """,
        (subject_ref,),
    ).fetchall()
    candidates.extend(
        TradeSubjectCandidate(row["position_id"], "outcome_fact", 1)
        for row in rows
    )

    rows = conn.execute(
        "SELECT position_id FROM position_current WHERE position_id = ?",
        (subject_ref,),
    ).fetchall()
    candidates.extend(
        TradeSubjectCandidate(row["position_id"], "position_current", 2)
        for row in rows
    )

    rows = conn.execute(
        """
        SELECT position_id, MAX(sequence_no) AS sequence_no
        FROM position_events
        WHERE position_id = ?
        GROUP BY position_id
        """,
        (subject_ref,),
    ).fetchall()
    candidates.extend(
        TradeSubjectCandidate(
            row["position_id"],
            "position_events",
            3,
            int(row["sequence_no"] or 0),
        )
        for row in rows
    )

    rows = conn.execute(
        """
        SELECT position_id
        FROM execution_fact
        WHERE position_id = ?
          AND terminal_exec_status IS NOT NULL
          AND terminal_exec_status != ''
        """,
        (subject_ref,),
    ).fetchall()
    candidates.extend(
        TradeSubjectCandidate(row["position_id"], "execution_fact", 4)
        for row in rows
    )

    alias_rows = conn.execute(
        """
        SELECT DISTINCT runtime_trade_id, trade_id AS decision_row_id
        FROM trade_decisions
        WHERE runtime_trade_id = ?
        """,
        (subject_ref,),
    ).fetchall()
    if alias_rows and not candidates:
        return TradeHistorySubject(
            position_id=None,
            status="unresolved",
            source="trade_decisions.runtime_trade_id",
            missing_reason="orphan_trade_decision",
            aliases=tuple(str(row["decision_row_id"]) for row in alias_rows),
        )

    decision_row = conn.execute(
        """
        SELECT trade_id, runtime_trade_id
        FROM trade_decisions
        WHERE CAST(trade_id AS TEXT) = ?
        """,
        (str(subject_ref),),
    ).fetchone()
    if decision_row is not None and not candidates:
        return TradeHistorySubject(
            position_id=None,
            status="unresolved",
            source="trade_decisions.trade_id",
            missing_reason="decision_row_id_not_subject",
            aliases=(str(decision_row["trade_id"]),),
        )

    return select_canonical_trade_subject(candidates)


# ---------------------------------------------------------------------------
# Core replay engine
# ---------------------------------------------------------------------------

def _replay_one_settlement(
    ctx: ReplayContext,
    city: City,
    target_date: str,
    settlement: dict,
    temperature_metric: str = "high",
) -> Optional[ReplayOutcome]:
    """Replay the evaluator pipeline for one city × target_date.

    Uses stored member_maxes from ensemble_snapshots to recompute p_raw,
    then runs through calibration, alpha fusion, edge detection, and sizing.
    """
    if False:
        _ = None.selected_method
    from src.calibration.manager import get_calibrator
    from src.strategy.fdr_filter import fdr_filter
    from src.strategy.kelly import dynamic_kelly_mult, kelly_size
    from src.strategy.market_analysis import MarketAnalysis
    from src.strategy.market_fusion import compute_alpha
    from src.calibration.manager import season_from_month
    from src.data.market_scanner import _parse_temp_range
    from src.types import Bin
    from src.contracts.settlement_semantics import SettlementSemantics

    _sem = SettlementSemantics.for_city(city)
    _round_fn = _sem.round_values

    decision_ref = ctx.get_decision_reference_for(city.name, target_date, temperature_metric=temperature_metric)
    if decision_ref is None:
        return None
    decision_reference_source = str(decision_ref.get("source") or "UNKNOWN")
    selected_method = "ens_member_counting"
    if decision_ref.get("source") == "decision_log.trade_cases":
        selected_method = "ens_member_counting"
    elif decision_ref.get("source") == "decision_log.no_trade_cases":
        selected_method = "ens_member_counting"

    snapshot = ctx.get_snapshot_for(
        city.name,
        target_date,
        decision_time=decision_ref["decision_time"],
        snapshot_id=decision_ref["snapshot_id"],
    )
    if snapshot is None:
        return None  # No ENS data for this date

    member_maxes = snapshot["member_maxes"]
    lead_days = snapshot["lead_hours"] / 24.0
    target_d = date.fromisoformat(target_date)
    season = season_from_month(target_d.month, lat=city.lat)

    # Use the stored p_raw directly (it was computed at decision time)
    p_raw_stored = snapshot["p_raw_stored"] or decision_ref.get("p_raw_vector")
    if p_raw_stored is None or len(p_raw_stored) == 0:
        return None

    def _fetch_labels(query: str, params: tuple) -> list[str]:
        rows = ctx.conn.execute(query, params).fetchall()
        labels = []
        for row in rows:
            label = row["range_label"]
            low, high = _parse_temp_range(label)
            if low is None and high is None:
                continue
            labels.append(label)
        return labels

    cp_labels = _fetch_labels(
        f"SELECT DISTINCT range_label FROM {ctx._sp}calibration_pairs WHERE city = ? AND target_date = ? ORDER BY range_label",
        (city.name, target_date),
    )
    me_labels = _fetch_labels(
        f"SELECT DISTINCT range_label FROM {ctx._sp}market_events WHERE city = ? AND target_date = ? AND range_label IS NOT NULL AND range_label != '' ORDER BY range_label",
        (city.name, target_date),
    )
    ref_labels = [label for label in (decision_ref.get("bin_labels") or []) if _parse_temp_range(label) != (None, None)]

    p_raw_len = len(p_raw_stored)
    if len(cp_labels) == p_raw_len:
        bin_labels = cp_labels
    elif len(me_labels) == p_raw_len:
        bin_labels = me_labels
    elif len(ref_labels) == p_raw_len:
        bin_labels = ref_labels
    else:
        return None

    bins = [Bin(low=_parse_temp_range(label)[0], high=_parse_temp_range(label)[1], label=label, unit=city.settlement_unit) for label in bin_labels]

    # Get calibrator
    cal, cal_level = get_calibrator(ctx.conn, city, target_date)

    # Compute alpha (with overrides if any)
    override_alpha = ctx.overrides.get("alpha", {}).get(city.name, {}).get(season)
    hours_since_open_source = "market_hours_open"
    hours_since_open_fallback = False
    if override_alpha is not None:
        alpha = float(override_alpha)
        hours_since_open_source = "override_alpha"
    elif decision_ref.get("alpha", 0.0):
        alpha = float(decision_ref["alpha"])
        hours_since_open_source = "decision_ref_alpha"
    else:
        hours_since_open = decision_ref.get("market_hours_open")
        try:
            hours_since_open = float(hours_since_open)
            hours_since_open_source = "market_hours_open"
        except (TypeError, ValueError):
            hours_since_open = 48.0
            hours_since_open_source = "fallback_48.0"
            hours_since_open_fallback = True
        alpha = compute_alpha(
            calibration_level=cal_level,
            ensemble_spread=TemperatureDelta(float(snapshot["spread"] or 3.0), city.settlement_unit),
            model_agreement=decision_ref.get("agreement", "UNKNOWN") or "UNKNOWN",
            lead_days=lead_days,
            hours_since_open=hours_since_open,
            city_name=city.name,
            season=season,
            authority_verified=True,
        ).value

    # Calibrate — S6: use calibrate_and_normalize (same path as entry + monitor)
    bin_probs_raw = np.array(p_raw_stored, dtype=float)
    p_cal_vector_ref = decision_ref.get("p_cal_vector") or []
    if len(p_cal_vector_ref) == len(bin_probs_raw):
        bin_probs_cal = np.array([float(v) for v in p_cal_vector_ref], dtype=float)
    elif cal is not None:
        from src.calibration.platt import calibrate_and_normalize
        bin_widths = [b.width for b in bins]
        bin_probs_cal = calibrate_and_normalize(
            bin_probs_raw, cal, float(lead_days), bin_widths=bin_widths,
        )
    else:
        bin_probs_cal = bin_probs_raw

    p_market_vector = decision_ref.get("p_market_vector") or []
    market_price_linked = False
    if len(p_market_vector) == len(bin_probs_cal):
        candidate_market_prices = np.array([float(v) for v in p_market_vector], dtype=float)
        if np.all(np.isfinite(candidate_market_prices)):
            market_prices = candidate_market_prices
            market_price_linked = True
        else:
            market_prices = np.full(len(bin_probs_cal), 1.0 / len(bin_probs_cal), dtype=float)
    else:
        market_prices = np.full(len(bin_probs_cal), 1.0 / len(bin_probs_cal), dtype=float)

    analysis = MarketAnalysis(
        p_raw=bin_probs_raw,
        p_cal=bin_probs_cal,
        p_market=market_prices,
        alpha=alpha,
        bins=bins,
        member_maxes=member_maxes,
        calibrator=cal,
        lead_days=lead_days,
        unit=city.settlement_unit,
        round_fn=_round_fn,
    )
    edges = analysis.find_edges(n_bootstrap=edge_n_bootstrap())
    filtered = fdr_filter(edges)

    winning_bin = settlement.get("winning_bin", "")
    settlement_value = settlement.get("settlement_value")
    # Defensive: round to integer per settlement precision contract
    if settlement_value is not None:
        settlement_value = float(_round_fn(np.array([float(settlement_value)]))[0])

    decisions = []
    best_edge = 0.0
    would_trade = False
    replay_pnl = 0.0
    provenance_validations = [
        f"decision_reference_source:{decision_reference_source}",
        f"hours_since_open_source:{hours_since_open_source}",
    ]
    if decision_reference_source in DIAGNOSTIC_REPLAY_REFERENCE_SOURCES:
        provenance_validations.append("diagnostic_reference")
    if hours_since_open_fallback:
        provenance_validations.append("hours_since_open_fallback=48.0")

    if filtered:
        for edge in filtered:
            ci_width = max(0.0, edge.ci_upper - edge.ci_lower)
            k_mult = dynamic_kelly_mult(
                base=settings["sizing"]["kelly_multiplier"],
                ci_width=ci_width,
                lead_days=lead_days,
                rolling_win_rate_20=0.50,
                portfolio_heat=0.0,
                drawdown_pct=0.0,
            )
            size_usd = kelly_size(
                edge.p_posterior,
                edge.entry_price,
                bankroll=settings.capital_base_usd,
                kelly_mult=k_mult,
            )
            size_usd = max(0.0, size_usd)
            if not market_price_linked:
                should_trade = False
                rejection_stage = "MARKET_PRICE_UNAVAILABLE"
            elif edge.entry_price <= 0:
                should_trade = False
                rejection_stage = "INVALID_MARKET_PRICE"
            elif size_usd < settings["sizing"]["min_order_usd"]:
                should_trade = False
                rejection_stage = "SIZING_TOO_SMALL"
            else:
                should_trade = True
                rejection_stage = ""

            dec = ReplayDecision(
                city=city.name,
                target_date=target_date,
                range_label=edge.bin.label,
                direction=edge.direction,
                should_trade=should_trade,
                rejection_stage=rejection_stage,
                edge=round(edge.edge, 4),
                p_posterior=round(edge.p_posterior, 4),
                p_raw=round(float(bin_probs_raw[bins.index(edge.bin)]), 4),
                size_usd=round(size_usd, 4),
                entry_price=round(edge.entry_price, 4),
                edge_source="replay_audit",
                applied_validations=[
                    selected_method,
                    "bootstrap_ci",
                    "fdr_filter",
                    *provenance_validations,
                    "kelly_sizing",
                    "market_price_linked" if market_price_linked else "market_price_unavailable",
                ],
            )
            decisions.append(dec)
            if abs(edge.edge) > abs(best_edge):
                best_edge = edge.edge

            if should_trade and edge.entry_price > 0:
                would_trade = True
                shares = size_usd / edge.entry_price if edge.entry_price > 0 else 0.0
                won = derive_outcome_from_settlement_value(
                    settlement_value,
                    edge.bin,
                    city.settlement_unit,
                )
                if edge.direction == "buy_yes":
                    exit_price = 1.0 if won else 0.0
                else:
                    exit_price = 1.0 if not won else 0.0
                replay_pnl += shares * exit_price - size_usd
    else:
        decisions.append(
            ReplayDecision(
                city=city.name,
                target_date=target_date,
                range_label="",
                direction="",
                should_trade=False,
                rejection_stage=decision_ref.get("rejection_stage", "FDR_FILTERED"),
                edge=0.0,
                p_posterior=0.0,
                p_raw=0.0,
                size_usd=0.0,
                entry_price=0.0,
                edge_source="replay_audit",
                applied_validations=[
                    selected_method,
                    "bootstrap_ci",
                    "fdr_filter",
                    *provenance_validations,
                    "market_price_linked" if market_price_linked else "market_price_unavailable",
                ],
            )
        )

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
        decision_reference_source=decision_reference_source,
        hours_since_open_source=hours_since_open_source,
        hours_since_open_fallback=hours_since_open_fallback,
    )


def _bin_matches_settlement(label: str, settlement_value: Optional[float], unit: str = "F") -> bool:
    """Compatibility wrapper for older tests/callers."""
    bin = bin_from_range_label(label, unit)
    if bin is None or settlement_value is None:
        return False
    return derive_outcome_from_settlement_value(settlement_value, bin, unit)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _insert_backtest_run(conn, summary: ReplaySummary, *, status: str = "complete") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_runs
        (run_id, lane, started_at, completed_at, status, authority_scope, config_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            summary.run_id,
            summary.mode,
            now,
            now,
            status,
            BACKTEST_AUTHORITY_SCOPE,
            _json(summary.overrides),
            _json(
                {
                    "n_settlements": summary.n_settlements,
                    "n_replayed": summary.n_replayed,
                    "n_would_trade": summary.n_would_trade,
                    "coverage_pct": summary.coverage_pct,
                    "cities_covered": summary.cities_covered,
                    "limitations": summary.limitations,
                }
            ),
        ),
    )


def _insert_backtest_outcome(
    conn,
    *,
    run_id: str,
    lane: str,
    subject_id: str,
    subject_kind: str,
    city: str | None,
    target_date: str | None,
    range_label: str | None = None,
    direction: str | None = None,
    settlement_value: float | None = None,
    settlement_unit: str | None = None,
    derived_wu_outcome: bool | None = None,
    actual_trade_outcome: int | bool | None = None,
    actual_pnl: float | None = None,
    truth_source: str,
    divergence_status: str,
    decision_reference_source: str | None = None,
    forecast_reference_id: str | None = None,
    evidence: dict | None = None,
    missing_reasons: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO backtest_outcome_comparison
        (run_id, lane, subject_id, subject_kind, city, target_date, range_label,
         direction, settlement_value, settlement_unit, derived_wu_outcome,
         actual_trade_outcome, actual_pnl, truth_source, divergence_status,
         decision_reference_source, forecast_reference_id, evidence_json,
         missing_reason_json, authority_scope, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            lane,
            subject_id,
            subject_kind,
            city,
            target_date,
            range_label,
            direction,
            settlement_value,
            settlement_unit,
            None if derived_wu_outcome is None else int(bool(derived_wu_outcome)),
            None if actual_trade_outcome is None else int(bool(actual_trade_outcome)),
            actual_pnl,
            truth_source,
            divergence_status,
            decision_reference_source,
            forecast_reference_id,
            _json(evidence or {}),
            _json(missing_reasons or []),
            BACKTEST_AUTHORITY_SCOPE,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _typed_bins_for_city_date(ctx: ReplayContext, city: City, target_date: str) -> list[Bin]:
    rows = ctx.conn.execute(
        f"""
        SELECT DISTINCT range_label FROM (
            SELECT range_label
            FROM {ctx._sp}calibration_pairs
            WHERE city = ? AND target_date = ?
            UNION
            SELECT range_label
            FROM {ctx._sp}market_events
            WHERE city = ? AND target_date = ?
              AND range_label IS NOT NULL
              AND range_label != ''
        )
        ORDER BY range_label
        """,
        (city.name, target_date, city.name, target_date),
    ).fetchall()
    bins: list[Bin] = []
    for row in rows:
        bin = bin_from_range_label(row["range_label"], city.settlement_unit)
        if bin is not None:
            bins.append(bin)
    return bins


def run_wu_settlement_sweep(
    start_date: str,
    end_date: str,
    *,
    allow_snapshot_only_reference: bool = False,
) -> ReplaySummary:
    """Run a WU settlement-value sweep into the derived backtest DB."""
    run_id = str(uuid.uuid4())[:12]
    conn = get_trade_connection_with_world()
    ctx = ReplayContext(
        conn,
        allow_snapshot_only_reference=allow_snapshot_only_reference,
    )
    backtest_conn = get_backtest_connection()
    init_backtest_schema(backtest_conn)

    rows = conn.execute(
        f"""
        SELECT city, target_date, settlement_value, winning_bin
        FROM {ctx._sp}settlements
        WHERE target_date >= ? AND target_date <= ?
          AND settlement_value IS NOT NULL
        ORDER BY target_date, city
        """,
        (start_date, end_date),
    ).fetchall()
    summary = ReplaySummary(
        run_id=run_id,
        mode=WU_SWEEP_LANE,
        date_range=(start_date, end_date),
        n_settlements=len(rows),
        limitations={
            "storage": "zeus_backtest.db",
            "authority_scope": BACKTEST_AUTHORITY_SCOPE,
            "promotion_authority": False,
            "lane_goal": "forecast_skill_not_pnl",
            "pnl_available": False,
            "pnl_unavailable_reason": "wu_settlement_sweep_scores_forecast_quality_not_trading_economics",
            "uses_stored_winning_bin_as_truth": False,
            "snapshot_only_reference": allow_snapshot_only_reference,
        },
    )
    _insert_backtest_run(backtest_conn, summary, status="running")

    per_city_dates: dict[str, set[str]] = {}
    per_city_skill_samples: dict[str, list[dict]] = {}
    per_city_top_candidates: dict[str, dict[tuple[str, str, str], list[tuple[str, float, bool]]]] = {}
    covered_subjects: set[str] = set()
    skill_samples: list[dict] = []
    top_candidates: dict[tuple[str, str, str], list[tuple[str, float, bool]]] = {}
    comparison_rows = 0
    for row in rows:
        city = cities_by_name.get(row["city"])
        if city is None:
            continue
        forecast_rows = ctx.conn.execute(
            f"""
            SELECT range_label, p_raw, outcome AS stored_outcome, lead_days,
                   season, cluster, forecast_available_at, decision_group_id,
                   bias_corrected
            FROM {ctx._sp}calibration_pairs
            WHERE city = ?
              AND target_date = ?
            ORDER BY datetime(forecast_available_at), lead_days, range_label
            """,
            (city.name, row["target_date"]),
        ).fetchall()
        if not forecast_rows:
            bins = _typed_bins_for_city_date(ctx, city, row["target_date"])
            if not bins:
                _insert_backtest_outcome(
                    backtest_conn,
                    run_id=run_id,
                    lane=WU_SWEEP_LANE,
                    subject_id=f"{city.name}|{row['target_date']}|no_bin",
                    subject_kind="settlement_value",
                    city=city.name,
                    target_date=row["target_date"],
                    settlement_value=row["settlement_value"],
                    settlement_unit=city.settlement_unit,
                    truth_source="wu_settlement_value",
                    divergence_status="bin_unparseable",
                    evidence={"stored_winning_bin": row["winning_bin"]},
                    missing_reasons=["no_parseable_bins", "no_forecast_probability_rows"],
                )
                continue
            settlement_subject = f"{city.name}|{row['target_date']}"
            covered_subjects.add(settlement_subject)
            per_city_dates.setdefault(city.name, set()).add(row["target_date"])
            per_city_skill_samples.setdefault(city.name, [])
            per_city_top_candidates.setdefault(city.name, {})
            for bin in bins:
                outcome = derive_outcome_from_settlement_value(
                    row["settlement_value"],
                    bin,
                    city.settlement_unit,
                )
                _insert_backtest_outcome(
                    backtest_conn,
                    run_id=run_id,
                    lane=WU_SWEEP_LANE,
                    subject_id=f"{city.name}|{row['target_date']}|{bin.label}|no_forecast_probability",
                    subject_kind="settlement_value",
                    city=city.name,
                    target_date=row["target_date"],
                    range_label=bin.label,
                    settlement_value=row["settlement_value"],
                    settlement_unit=city.settlement_unit,
                    derived_wu_outcome=outcome,
                    truth_source="wu_settlement_value",
                    divergence_status="not_applicable",
                    evidence={"stored_winning_bin": row["winning_bin"]},
                    missing_reasons=["no_forecast_probability_rows"],
                )
                comparison_rows += 1
            continue

        settlement_subject = f"{city.name}|{row['target_date']}"
        covered_subjects.add(settlement_subject)
        per_city_dates.setdefault(city.name, set()).add(row["target_date"])
        per_city_skill_samples.setdefault(city.name, [])
        per_city_top_candidates.setdefault(city.name, {})
        for forecast_row in forecast_rows:
            bin = bin_from_range_label(forecast_row["range_label"], city.settlement_unit)
            forecast_reference_id = _forecast_reference_id(forecast_row)
            if bin is None:
                _insert_backtest_outcome(
                    backtest_conn,
                    run_id=run_id,
                    lane=WU_SWEEP_LANE,
                    subject_id=f"{city.name}|{row['target_date']}|{forecast_row['range_label']}|{forecast_reference_id}",
                    subject_kind="forecast_bin",
                    city=city.name,
                    target_date=row["target_date"],
                    range_label=forecast_row["range_label"],
                    settlement_value=row["settlement_value"],
                    settlement_unit=city.settlement_unit,
                    truth_source="wu_settlement_value",
                    divergence_status="bin_unparseable",
                    forecast_reference_id=forecast_reference_id,
                    evidence={"stored_winning_bin": row["winning_bin"]},
                    missing_reasons=["bin_unparseable"],
                )
                continue
            outcome = derive_outcome_from_settlement_value(
                row["settlement_value"],
                bin,
                city.settlement_unit,
            )
            p_raw = _clamp_probability(forecast_row["p_raw"])
            brier = _binary_brier(p_raw, outcome)
            log_loss = _binary_log_loss(p_raw, outcome)
            threshold_correct = (p_raw >= 0.5) == outcome
            group_key = (city.name, row["target_date"], forecast_reference_id)
            sample = {
                "p_raw": p_raw,
                "outcome": outcome,
                "brier": brier,
                "log_loss": log_loss,
                "threshold_correct": threshold_correct,
                "group_key": group_key,
            }
            skill_samples.append(sample)
            per_city_skill_samples[city.name].append(sample)
            candidate = (bin.label, p_raw, outcome)
            top_candidates.setdefault(group_key, []).append(candidate)
            per_city_top_candidates[city.name].setdefault(group_key, []).append(candidate)

            stored_outcome = forecast_row["stored_outcome"]
            stored_matches = (
                int(stored_outcome) == int(outcome)
                if stored_outcome is not None
                else None
            )
            _insert_backtest_outcome(
                backtest_conn,
                run_id=run_id,
                lane=WU_SWEEP_LANE,
                subject_id=f"{city.name}|{row['target_date']}|{bin.label}|{forecast_reference_id}",
                subject_kind="forecast_bin",
                city=city.name,
                target_date=row["target_date"],
                range_label=bin.label,
                settlement_value=row["settlement_value"],
                settlement_unit=city.settlement_unit,
                derived_wu_outcome=outcome,
                truth_source="wu_settlement_value",
                divergence_status="not_applicable",
                decision_reference_source="calibration_pairs.forecast_available_at",
                forecast_reference_id=forecast_reference_id,
                evidence={
                    "p_raw": round(p_raw, 12),
                    "brier": round(brier, 12),
                    "log_loss": round(log_loss, 12),
                    "accuracy_at_0_5": threshold_correct,
                    "lead_days": float(forecast_row["lead_days"] or 0.0),
                    "season": forecast_row["season"],
                    "cluster": forecast_row["cluster"],
                    "forecast_available_at": forecast_row["forecast_available_at"],
                    "decision_group_id": forecast_row["decision_group_id"],
                    "bias_corrected": int(forecast_row["bias_corrected"] or 0),
                    "stored_outcome": stored_outcome,
                    "stored_outcome_matches_settlement_value": stored_matches,
                    "stored_winning_bin": row["winning_bin"],
                },
            )
            comparison_rows += 1

    summary.n_replayed = len(covered_subjects)
    summary.limitations["comparison_rows"] = comparison_rows
    summary.limitations["forecast_skill"] = _summarize_forecast_skill(
        skill_samples,
        top_candidates,
    )
    summary.cities_covered = sorted(per_city_dates)
    summary.coverage_pct = round(summary.n_replayed / max(1, summary.n_settlements) * 100, 1)
    for city_name, dates in per_city_dates.items():
        city_skill = _summarize_forecast_skill(
            per_city_skill_samples.get(city_name, []),
            per_city_top_candidates.get(city_name, {}),
        )
        summary.per_city[city_name] = {
            "n_dates": len(dates),
            "n_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            **city_skill,
        }
    _insert_backtest_run(backtest_conn, summary)
    backtest_conn.commit()
    backtest_conn.close()
    conn.close()
    return summary


def _trade_subject_rows(conn) -> list[str]:
    rows = conn.execute(
        """
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
        """
    ).fetchall()
    return sorted({str(row["position_id"]) for row in rows if row["position_id"]})


def run_trade_history_audit(start_date: str, end_date: str) -> ReplaySummary:
    """Compare actual trade-history outcomes with WU-derived outcomes."""
    run_id = str(uuid.uuid4())[:12]
    conn = get_trade_connection_with_world()
    backtest_conn = get_backtest_connection()
    init_backtest_schema(backtest_conn)
    summary = ReplaySummary(
        run_id=run_id,
        mode=TRADE_HISTORY_LANE,
        date_range=(start_date, end_date),
        limitations={
            "storage": "zeus_backtest.db",
            "authority_scope": BACKTEST_AUTHORITY_SCOPE,
            "promotion_authority": False,
            "lane_goal": "real_trade_outcome_divergence_not_hypothetical_pnl",
            "pnl_available": False,
            "pnl_unavailable_reason": "trade_history_audit_reports_actual_trade_pnl_rows_not_simulated_strategy_pnl",
        },
    )
    _insert_backtest_run(backtest_conn, summary, status="running")

    subject_refs = _trade_subject_rows(conn)
    for subject_ref in subject_refs:
        subject = resolve_trade_history_subject(conn, subject_ref)
        if subject.position_id is None:
            divergence_status = (
                "ambiguous_subject"
                if subject.missing_reason == "ambiguous_trade_subject"
                else "orphan_trade_decision"
            )
            _insert_backtest_outcome(
                backtest_conn,
                run_id=run_id,
                lane=TRADE_HISTORY_LANE,
                subject_id=subject.subject_id,
                subject_kind="position",
                city=None,
                target_date=None,
                truth_source="trade_history",
                divergence_status=divergence_status,
                evidence={"aliases": list(subject.aliases), "source": subject.source},
                missing_reasons=[subject.missing_reason],
            )
            continue

        current = conn.execute(
            """
            SELECT position_id, city, target_date, bin_label, direction, unit
            FROM position_current
            WHERE position_id = ?
            """,
            (subject.position_id,),
        ).fetchone()
        outcome = conn.execute(
            """
            SELECT outcome, pnl, settled_at
            FROM outcome_fact
            WHERE position_id = ?
            """,
            (subject.position_id,),
        ).fetchone()
        if current is None:
            _insert_backtest_outcome(
                backtest_conn,
                run_id=run_id,
                lane=TRADE_HISTORY_LANE,
                subject_id=subject.position_id,
                subject_kind="position",
                city=None,
                target_date=None,
                truth_source="trade_history",
                divergence_status="trade_unresolved",
                evidence={"resolved_source": subject.source},
                missing_reasons=["missing_position_current"],
            )
            continue

        city_name = current["city"]
        target_date = current["target_date"]
        if target_date < start_date or target_date > end_date:
            continue
        summary.n_settlements += 1
        city = cities_by_name.get(city_name)
        unit = current["unit"] or (city.settlement_unit if city else "")
        bin = bin_from_range_label(current["bin_label"], unit) if unit else None
        settlement = conn.execute(
            """
            SELECT settlement_value
            FROM world.settlements
            WHERE city = ? AND target_date = ?
            """,
            (city_name, target_date),
        ).fetchone()

        missing = []
        wu_outcome = None
        if settlement is None or settlement["settlement_value"] is None:
            missing.append("wu_missing")
            divergence = "wu_missing"
        elif bin is None:
            missing.append("bin_unparseable")
            divergence = "bin_unparseable"
        else:
            wu_outcome = derive_outcome_from_settlement_value(
                settlement["settlement_value"],
                bin,
                unit,
            )
            divergence = classify_outcome_divergence(
                wu_outcome,
                outcome["outcome"] if outcome else None,
            )

        _insert_backtest_outcome(
            backtest_conn,
            run_id=run_id,
            lane=TRADE_HISTORY_LANE,
            subject_id=subject.position_id,
            subject_kind="position",
            city=city_name,
            target_date=target_date,
            range_label=current["bin_label"],
            direction=current["direction"],
            settlement_value=settlement["settlement_value"] if settlement else None,
            settlement_unit=unit,
            derived_wu_outcome=wu_outcome,
            actual_trade_outcome=outcome["outcome"] if outcome else None,
            actual_pnl=outcome["pnl"] if outcome else None,
            truth_source="trade_history",
            divergence_status=divergence,
            evidence={"resolved_source": subject.source},
            missing_reasons=missing,
        )
        summary.n_replayed += 1
        if outcome is not None:
            summary.n_actual_traded += 1

    summary.coverage_pct = round(summary.n_replayed / max(1, summary.n_settlements) * 100, 1)
    _insert_backtest_run(backtest_conn, summary)
    backtest_conn.commit()
    backtest_conn.close()
    conn.close()
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_replay(
    start_date: str,
    end_date: str,
    mode: str = "audit",
    overrides: Optional[dict] = None,
    allow_snapshot_only_reference: bool = False,
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
    if mode == WU_SWEEP_LANE:
        return run_wu_settlement_sweep(
            start_date,
            end_date,
            allow_snapshot_only_reference=allow_snapshot_only_reference,
        )
    if mode == TRADE_HISTORY_LANE:
        return run_trade_history_audit(start_date, end_date)

    run_id = str(uuid.uuid4())[:12]
    conn = get_trade_connection_with_world()
    ctx = ReplayContext(
        conn,
        overrides=overrides,
        allow_snapshot_only_reference=(allow_snapshot_only_reference or mode != "audit"),
    )

    # Get all settlements in date range
    settlements = conn.execute(f"""
        SELECT city, target_date, settlement_value, winning_bin
        FROM {ctx._sp}settlements
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
    per_city_trade_pnl: dict[str, list[float]] = {}
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
            per_city_trade_pnl[city_name] = []
            per_city_trades[city_name] = 0
        per_city_pnl[city_name].append(outcome.replay_pnl)
        if outcome.replay_would_trade:
            per_city_trades[city_name] += 1
            per_city_trade_pnl[city_name].append(outcome.replay_pnl)

    # Aggregate per-city stats
    for city_name in per_city_pnl:
        pnls = per_city_pnl[city_name]
        trade_pnls = per_city_trade_pnl[city_name]
        trades = per_city_trades[city_name]
        wins = sum(1 for p in trade_pnls if p > 0)
        summary.per_city[city_name] = {
            "n_dates": len(pnls),
            "n_trades": trades,
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(wins / len(trade_pnls), 3) if trade_pnls else 0.0,
        }

    summary.cities_covered = sorted(per_city_pnl.keys())
    summary.coverage_pct = round(
        summary.n_replayed / max(1, summary.n_settlements) * 100, 1
    )

    traded_outcomes = [o for o in summary.outcomes if o.replay_would_trade]
    if traded_outcomes:
        wins = sum(1 for o in traded_outcomes if o.replay_pnl > 0)
        summary.replay_win_rate = round(wins / len(traded_outcomes), 3)

    priced_subjects = sum(
        1
        for outcome in summary.outcomes
        if any("market_price_linked" in d.applied_validations for d in outcome.replay_decisions)
    )
    unpriced_subjects = sum(
        1
        for outcome in summary.outcomes
        if any("market_price_unavailable" in d.applied_validations for d in outcome.replay_decisions)
    )

    linkage_info = _market_price_linkage_limitations(
        n_replayed=summary.n_replayed,
        market_price_linked_subjects=priced_subjects,
        market_price_unavailable_subjects=unpriced_subjects,
    )
    full_linkage = linkage_info.get("market_price_linkage_state") == "full"

    # ZDM-03: explicitly report missing parity dimensions
    missing_parity = _missing_parity_dimensions(full_linkage)

    summary.limitations = {
        "uniform_prior": "diagnostic_edge_ranking_only_when_market_vector_missing",
        "flat_sizing": False,
        "no_bootstrap_fdr": False,
        **linkage_info,
        **_replay_provenance_limitations(summary.outcomes),
        "forecast_rows_fallback": allow_snapshot_only_reference,
        "promotion_authority": False,
        "missing_parity_dimensions": missing_parity,
    }

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
        if best_dec is None:
            for d in outcome.replay_decisions:
                if best_dec is None or abs(d.edge) > abs(best_dec.edge):
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
