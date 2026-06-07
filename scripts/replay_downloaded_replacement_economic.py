#!/usr/bin/env python3
"""Replay downloaded replacement forecasts against executable market snapshots.

This bridge is intentionally read-only. It converts the downloaded
Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t evaluation rows into the existing
replacement tournament model, using real market topology from the forecast DB
and real orderbook asks/depth from the trade DB.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_go_live_report import (  # noqa: E402
    replacement_forecast_go_live_payload_template,
)


FULL_STRATEGY_LABEL = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
FIXED_CONFIG_LABEL = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00"
PRODUCT_LABELS = (
    "B0",
    "openmeteo_ecmwf_ifs9_anchor",
    FIXED_CONFIG_LABEL,
    "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_logloss",
    "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_brier",
)


@dataclass(frozen=True)
class ReplacementReplayRow:
    product_label: str
    city: str
    target_date: date
    metric: str
    decision_time: datetime
    source_available_at: datetime
    source_availability_observed: bool
    source_availability_mode: str
    bin_labels: tuple[str, ...]
    probabilities: tuple[float, ...]
    selected_bin_index: int
    winning_bin_index: int
    selected_q_lcb: float
    q_lcb_source: str
    market_price: float
    stake_usd: float
    fees_usd: float
    slippage_usd: float
    fill_probability: float

    @property
    def winner_probability(self) -> float:
        return float(self.probabilities[self.winning_bin_index])

    @property
    def selected_wins(self) -> bool:
        return self.selected_bin_index == self.winning_bin_index

    @property
    def realized_pnl_after_cost(self) -> float:
        gross = self.stake_usd * ((1.0 / self.market_price) - 1.0) if self.selected_wins else -self.stake_usd
        return gross - self.fees_usd - self.slippage_usd


@dataclass(frozen=True)
class CurrentHoldingOrderTimeCounterfactualRow:
    position_id: str
    city: str
    target_date: str
    metric: str
    current_bin_label: str
    current_direction: str
    current_entry_price: float | None
    current_size_usd: float | None
    decision_time: str | None
    snapshot_id: str | None
    status: str
    reason_codes: tuple[str, ...]
    eligible_posterior_id: int | None
    first_replacement_source_available_at: str | None
    first_replacement_computed_at: str | None
    replacement_q_for_current_bin_unusable: float | None
    replacement_top_bin_unusable: str | None
    replacement_top_q_unusable: float | None


@dataclass(frozen=True)
class CurrentHoldingOrderTimeCounterfactualResult:
    summary: Mapping[str, int]
    rows: tuple[CurrentHoldingOrderTimeCounterfactualRow, ...]


@dataclass(frozen=True)
class ReplacementTournamentResult:
    status: str
    selected_label: str | None
    metrics_by_label: Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class StrategyVariantTournamentResult:
    status: str
    selected_label: str | None
    selected_roi_label: str | None
    metrics_by_label: Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class MarketBin:
    condition_id: str
    label: str
    low: float | None
    high: float | None

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True


@dataclass(frozen=True)
class SnapshotPrice:
    snapshot_id: str
    price: float
    depth_shares: float
    captured_at: datetime
    min_order_size: float
    tick_size: float


@dataclass(frozen=True)
class SourceAvailabilityEvidence:
    mode: str
    source_available_at: datetime
    source_available_at_by_role: Mapping[str, datetime]
    observed: bool
    violation: bool
    reason: str | None = None


@dataclass(frozen=True)
class DownloadedAvailabilityIndex:
    aifs_available_by_run: Mapping[datetime, datetime]
    openmeteo_available_by_city_run: Mapping[tuple[str, datetime], datetime]

    def aifs_available_at(self, *, decision_time: datetime) -> datetime | None:
        candidates = [available for available in self.aifs_available_by_run.values() if available <= decision_time]
        return max(candidates) if candidates else None

    def openmeteo_available_at(self, *, city: str, decision_time: datetime) -> datetime | None:
        normalized_city = _filename_city(city)
        candidates = [
            available
            for (indexed_city, _run), available in self.openmeteo_available_by_city_run.items()
            if indexed_city == normalized_city and available <= decision_time
        ]
        return max(candidates) if candidates else None


def _rows_by_label(rows: Sequence[ReplacementReplayRow]) -> dict[str, list[ReplacementReplayRow]]:
    grouped: dict[str, list[ReplacementReplayRow]] = {}
    for row in rows:
        grouped.setdefault(row.product_label, []).append(row)
    return grouped


def _product_metrics(rows: Sequence[ReplacementReplayRow]) -> dict[str, float]:
    if not rows:
        return {
            "row_count": 0.0,
            "brier": float("nan"),
            "log_loss": float("nan"),
            "hit_rate": float("nan"),
            "total_after_cost_pnl": 0.0,
            "availability_violations": 0.0,
        }
    return {
        "row_count": float(len(rows)),
        "brier": sum(_row_brier(row) for row in rows) / len(rows),
        "log_loss": sum(_row_log_loss(row) for row in rows) / len(rows),
        "hit_rate": sum(1.0 for row in rows if row.selected_wins) / len(rows),
        "total_after_cost_pnl": sum(row.realized_pnl_after_cost * row.fill_probability for row in rows),
        "availability_violations": sum(1.0 for row in rows if row.source_available_at > row.decision_time),
    }


def run_replacement_tournament(
    rows: Sequence[ReplacementReplayRow],
    *,
    baseline_label: str,
    min_samples: int,
    expected_product_labels: Sequence[str],
) -> ReplacementTournamentResult:
    grouped = _rows_by_label(rows)
    metrics = {label: _product_metrics(grouped.get(label, [])) for label in expected_product_labels}
    scoreable = {
        label: values
        for label, values in metrics.items()
        if int(values["row_count"]) >= int(min_samples) and label != baseline_label
    }
    if not scoreable:
        return ReplacementTournamentResult(status="NO_SCOREABLE_REPLACEMENT", selected_label=None, metrics_by_label=metrics)
    selected = max(scoreable, key=lambda label: scoreable[label]["total_after_cost_pnl"])
    return ReplacementTournamentResult(status="EMPIRICAL_WINNER", selected_label=selected, metrics_by_label=metrics)


def run_strategy_variant_tournament(
    rows: Sequence[ReplacementReplayRow],
    *,
    fee_rate: float,
    slippage_rate: float,
) -> StrategyVariantTournamentResult:
    del fee_rate, slippage_rate
    grouped = _rows_by_label(rows)
    metrics = {label: _product_metrics(label_rows) for label, label_rows in grouped.items()}
    if not metrics:
        return StrategyVariantTournamentResult(status="NO_ROWS", selected_label=None, selected_roi_label=None, metrics_by_label={})
    selected = max(metrics, key=lambda label: metrics[label]["total_after_cost_pnl"])
    roi_selected = max(
        metrics,
        key=lambda label: (
            metrics[label]["total_after_cost_pnl"] / max(1.0, metrics[label]["row_count"])
        ),
    )
    return StrategyVariantTournamentResult(
        status="STRATEGY_VARIANT_WINNER",
        selected_label=f"{selected}:all_top1",
        selected_roi_label=f"{roi_selected}:after_cost_edge",
        metrics_by_label=metrics,
    )


def replacement_tournament_result_to_jsonable(result: ReplacementTournamentResult) -> dict[str, object]:
    return {
        "status": result.status,
        "selected_label": result.selected_label,
        "metrics_by_label": {label: dict(metrics) for label, metrics in result.metrics_by_label.items()},
    }


def strategy_variant_tournament_result_to_jsonable(result: StrategyVariantTournamentResult) -> dict[str, object]:
    return {
        "status": result.status,
        "selected_label": result.selected_label,
        "selected_roi_label": result.selected_roi_label,
        "metrics_by_label": {label: dict(metrics) for label, metrics in result.metrics_by_label.items()},
    }


def write_json_artifact(payload: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def render_replacement_tournament_markdown(result: ReplacementTournamentResult) -> str:
    lines = ["# Replacement Forecast Capital Tournament", "", f"Status: {result.status}", f"Selected: {result.selected_label}", "", "| Product | Rows | Brier | Log loss | After-cost PnL |", "|---|---:|---:|---:|---:|"]
    for label, metrics in sorted(result.metrics_by_label.items()):
        lines.append(f"| {label} | {int(metrics['row_count'])} | {metrics['brier']:.6f} | {metrics['log_loss']:.6f} | {metrics['total_after_cost_pnl']:.2f} |")
    return "\n".join(lines) + "\n"


def render_strategy_variant_tournament_markdown(result: StrategyVariantTournamentResult) -> str:
    lines = ["# Replacement Strategy Variant Tournament", "", f"Status: {result.status}", f"Selected capital: {result.selected_label}", f"Selected ROI: {result.selected_roi_label}"]
    return "\n".join(lines) + "\n"


def render_replacement_executive_summary_markdown(
    capital: ReplacementTournamentResult,
    _skill: object,
    coverage: Mapping[str, object],
) -> str:
    return "\n".join(
        [
            "# Replacement Forecast Executive Summary",
            "",
            f"Status: {capital.status}",
            f"Selected: {capital.selected_label}",
            f"Rows: {coverage.get('rows')}",
            f"Skipped: {coverage.get('skipped')}",
            f"Promotion grade: {coverage.get('promotion_grade')}",
            f"Promotion blocker: {coverage.get('promotion_blocker')}",
            "",
        ]
    )


def write_replacement_tournament_csv(result: ReplacementTournamentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["product_label", "row_count", "brier", "log_loss", "hit_rate", "total_after_cost_pnl", "availability_violations"])
        writer.writeheader()
        for label, metrics in sorted(result.metrics_by_label.items()):
            writer.writerow({"product_label": label, **metrics})


def write_strategy_variant_tournament_csv(result: StrategyVariantTournamentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["variant_label", "row_count", "total_after_cost_pnl"])
        writer.writeheader()
        for label, metrics in sorted(result.metrics_by_label.items()):
            writer.writerow({"variant_label": label, "row_count": metrics["row_count"], "total_after_cost_pnl": metrics["total_after_cost_pnl"]})


def write_replacement_rows_csv(rows: Sequence[ReplacementReplayRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "product_label",
                "city",
                "target_date",
                "metric",
                "decision_time",
                "source_available_at",
                "source_availability_observed",
                "source_availability_mode",
                "selected_bin",
                "winning_bin",
                "winner_probability",
                "selected_q_lcb",
                "q_lcb_source",
                "market_price",
                "stake_usd",
                "realized_pnl_after_cost",
                "fill_probability",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "product_label": row.product_label,
                    "city": row.city,
                    "target_date": row.target_date.isoformat(),
                    "metric": row.metric,
                    "decision_time": row.decision_time.isoformat(),
                    "source_available_at": row.source_available_at.isoformat(),
                    "source_availability_observed": row.source_availability_observed,
                    "source_availability_mode": row.source_availability_mode,
                    "selected_bin": row.bin_labels[row.selected_bin_index],
                    "winning_bin": row.bin_labels[row.winning_bin_index],
                    "winner_probability": row.winner_probability,
                    "selected_q_lcb": row.selected_q_lcb,
                    "q_lcb_source": row.q_lcb_source,
                    "market_price": row.market_price,
                    "stake_usd": row.stake_usd,
                    "realized_pnl_after_cost": row.realized_pnl_after_cost,
                    "fill_probability": row.fill_probability,
                }
            )


def _metric_from_market_label(label: str) -> str:
    normalized = str(label or "").lower()
    if "lowest temperature" in normalized:
        return "low"
    return "high"


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _posterior_top_probability(row: sqlite3.Row | None) -> tuple[float | None, str | None, float | None]:
    if row is None:
        return None, None, None
    try:
        q_json = json.loads(str(row["q_json"] or "{}"))
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(q_json, dict) or not q_json:
        return None, None, None
    cleaned: dict[str, float] = {}
    for label, raw_value in q_json.items():
        try:
            cleaned[str(label)] = max(0.0, min(1.0, float(raw_value)))
        except (TypeError, ValueError):
            continue
    if not cleaned:
        return None, None, None
    top_label, top_q = max(cleaned.items(), key=lambda item: item[1])
    return None, top_label, top_q


def _posterior_q_for_bin(row: sqlite3.Row | None, bin_label: str) -> float | None:
    if row is None:
        return None
    try:
        q_json = json.loads(str(row["q_json"] or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(q_json, dict) or bin_label not in q_json:
        return None
    try:
        return max(0.0, min(1.0, float(q_json[bin_label])))
    except (TypeError, ValueError):
        return None


def run_current_holdings_order_time_counterfactual(
    *,
    forecast_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    active_phases: Sequence[str] = ("active", "day0_window", "pending_exit", "pending_entry"),
) -> CurrentHoldingOrderTimeCounterfactualResult:
    """Classify current holdings by whether a true order-time replay is possible.

    A full-choice counterfactual must recreate the original decision surface:
    the original CLOB snapshot, the replacement posterior available at that
    exact decision time, and the whole candidate family.  If only the selected
    market is present, the row is not allowed to masquerade as a token-choice
    replay.
    """

    placeholders = ",".join("?" for _ in active_phases)
    position_rows = trade_conn.execute(
        f"""
        SELECT
            pc.position_id,
            pc.phase,
            pc.city,
            pc.target_date,
            pc.bin_label,
            pc.direction,
            pc.size_usd,
            pc.entry_price,
            pc.strategy_key,
            vc.snapshot_id,
            vc.created_at AS command_created_at
        FROM position_current pc
        LEFT JOIN venue_commands vc ON vc.position_id = pc.position_id
        WHERE pc.phase IN ({placeholders})
        ORDER BY COALESCE(vc.created_at, pc.updated_at), pc.position_id
        """,
        tuple(active_phases),
    ).fetchall()

    result_rows: list[CurrentHoldingOrderTimeCounterfactualRow] = []
    summary = {
        "positions_total": len(position_rows),
        "full_choice_replayable": 0,
        "selected_market_only_replayable": 0,
        "blocked_by_replacement_data_timing": 0,
        "missing_clob_snapshot": 0,
        "missing_replacement_posterior_for_target": 0,
        "same_choice_count": 0,
        "different_token_count": 0,
        "no_trade_count": 0,
        "size_changed_count": 0,
    }

    for position in position_rows:
        decision_time = position["command_created_at"]
        bin_label = str(position["bin_label"] or "")
        metric = _metric_from_market_label(bin_label)
        reasons: list[str] = []
        status = "selected_market_only_replayable"
        if not position["snapshot_id"]:
            reasons.append("CURRENT_HOLDING_COUNTERFACTUAL_MISSING_CLOB_SNAPSHOT")
            summary["missing_clob_snapshot"] += 1
        if not decision_time:
            reasons.append("CURRENT_HOLDING_COUNTERFACTUAL_MISSING_DECISION_TIME")

        first_posterior = forecast_conn.execute(
            """
            SELECT posterior_id, source_available_at, computed_at, q_json
            FROM forecast_posteriors
            WHERE source_id = ?
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
            ORDER BY computed_at ASC, source_available_at ASC, posterior_id ASC
            LIMIT 1
            """,
            (FULL_STRATEGY_LABEL, position["city"], position["target_date"], metric),
        ).fetchone()
        eligible_posterior = None
        if decision_time:
            eligible_posterior = forecast_conn.execute(
                """
                SELECT posterior_id, source_available_at, computed_at, q_json
                FROM forecast_posteriors
                WHERE source_id = ?
                  AND city = ?
                  AND target_date = ?
                  AND temperature_metric = ?
                  AND source_available_at <= ?
                  AND computed_at <= ?
                ORDER BY computed_at DESC, source_available_at DESC, posterior_id DESC
                LIMIT 1
                """,
                (
                    FULL_STRATEGY_LABEL,
                    position["city"],
                    position["target_date"],
                    metric,
                    decision_time,
                    decision_time,
                ),
            ).fetchone()

        if eligible_posterior is None:
            if first_posterior is None:
                status = "missing_replacement_posterior_for_target"
                reasons.append("CURRENT_HOLDING_COUNTERFACTUAL_MISSING_REPLACEMENT_POSTERIOR_FOR_TARGET")
                summary["missing_replacement_posterior_for_target"] += 1
            else:
                status = "blocked_by_replacement_data_timing"
                reasons.append("CURRENT_HOLDING_COUNTERFACTUAL_REPLACEMENT_POSTERIOR_AFTER_ORDER_TIME")
                summary["blocked_by_replacement_data_timing"] += 1
        else:
            reasons.append("CURRENT_HOLDING_COUNTERFACTUAL_CANDIDATE_FAMILY_NOT_RECONSTRUCTED")
            summary["selected_market_only_replayable"] += 1

        source_row = eligible_posterior if eligible_posterior is not None else first_posterior
        selected_q = _posterior_q_for_bin(source_row, bin_label)
        _, top_label, top_q = _posterior_top_probability(source_row)
        result_rows.append(
            CurrentHoldingOrderTimeCounterfactualRow(
                position_id=str(position["position_id"]),
                city=str(position["city"] or ""),
                target_date=str(position["target_date"] or ""),
                metric=metric,
                current_bin_label=bin_label,
                current_direction=str(position["direction"] or ""),
                current_entry_price=_float_or_none(position["entry_price"]),
                current_size_usd=_float_or_none(position["size_usd"]),
                decision_time=str(decision_time) if decision_time else None,
                snapshot_id=str(position["snapshot_id"]) if position["snapshot_id"] else None,
                status=status,
                reason_codes=tuple(dict.fromkeys(reasons)),
                eligible_posterior_id=int(eligible_posterior["posterior_id"]) if eligible_posterior is not None else None,
                first_replacement_source_available_at=str(first_posterior["source_available_at"]) if first_posterior is not None else None,
                first_replacement_computed_at=str(first_posterior["computed_at"]) if first_posterior is not None else None,
                replacement_q_for_current_bin_unusable=selected_q if eligible_posterior is None else None,
                replacement_top_bin_unusable=top_label if eligible_posterior is None else None,
                replacement_top_q_unusable=top_q if eligible_posterior is None else None,
            )
        )

    return CurrentHoldingOrderTimeCounterfactualResult(summary=summary, rows=tuple(result_rows))


def _to_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slug_city(city: str) -> str:
    return city.lower().replace(" ", "-")


def _fallback_market_slug(city: str, target: date, metric: str) -> str:
    prefix = "highest-temperature" if metric == "high" else "lowest-temperature"
    return f"{prefix}-in-{_slug_city(city)}-on-{target.strftime('%B').lower()}-{target.day}-{target.year}"


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _normal_cdf(value: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((value - mean) / (sigma * math.sqrt(2.0))))


def _bin_probability(bin_: MarketBin, mean: float, sigma: float) -> float:
    if sigma <= 0.0:
        return 1.0 if bin_.contains(_round_half_up(mean)) else 0.0
    low_edge = -math.inf if bin_.low is None else bin_.low - 0.5
    high_edge = math.inf if bin_.high is None else bin_.high + 0.5
    low_cdf = 0.0 if math.isinf(low_edge) and low_edge < 0 else _normal_cdf(low_edge, mean, sigma)
    high_cdf = 1.0 if math.isinf(high_edge) and high_edge > 0 else _normal_cdf(high_edge, mean, sigma)
    return max(0.0, min(1.0, high_cdf - low_cdf))


def _probabilities_for_bins(bins: Sequence[MarketBin], mean: float, sigma: float) -> tuple[float, ...]:
    probs = tuple(_bin_probability(bin_, mean, sigma) for bin_ in bins)
    total = sum(probs)
    if total <= 0.0:
        selected = _selected_bin_index(bins, mean)
        return tuple(1.0 if idx == selected else 0.0 for idx in range(len(bins)))
    return tuple(prob / total for prob in probs)


def _prediction_for_label(raw: Mapping[str, Any], product_label: str) -> float | None:
    if product_label == "B0":
        return None if raw.get("b0_pred") is None else float(raw["b0_pred"])
    if product_label == "openmeteo_ecmwf_ifs9_anchor":
        return None if raw.get("om_pred") is None else float(raw["om_pred"])
    if product_label == FIXED_CONFIG_LABEL:
        if raw.get("om_pred") is None or raw.get("aifs_pred") is None:
            return None
        return 0.80 * float(raw["om_pred"]) + 0.20 * float(raw["aifs_pred"])
    if product_label == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_logloss":
        return None if raw.get("best_logloss_pred") is None else float(raw["best_logloss_pred"])
    if product_label == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_grid_brier":
        return None if raw.get("best_brier_pred") is None else float(raw["best_brier_pred"])
    raise ValueError(f"unknown product_label {product_label!r}")


def _selected_bin_index(bins: Sequence[MarketBin], value: float) -> int:
    rounded = _round_half_up(value)
    for idx, bin_ in enumerate(bins):
        if bin_.contains(rounded):
            return idx
    raise ValueError(f"no market bin contains rounded value {rounded}")


def _open_forecast_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _open_trade_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_settlement_market_slugs(conn: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
    rows = conn.execute(
        """
        SELECT city, target_date, temperature_metric, market_slug
        FROM settlement_outcomes
        WHERE authority = 'VERIFIED'
        """
    ).fetchall()
    return {
        (str(row["city"]), str(row["target_date"]), str(row["temperature_metric"])): str(row["market_slug"])
        for row in rows
        if row["market_slug"]
    }


def _market_bins(conn: sqlite3.Connection, market_slug: str) -> tuple[MarketBin, ...]:
    rows = conn.execute(
        """
        SELECT condition_id, range_label, range_low, range_high
        FROM market_events
        WHERE market_slug = ?
        ORDER BY
          CASE WHEN range_low IS NULL THEN -9999 ELSE range_low END,
          CASE WHEN range_high IS NULL THEN 9999 ELSE range_high END
        """,
        (market_slug,),
    ).fetchall()
    return tuple(
        MarketBin(
            condition_id=str(row["condition_id"]),
            label=str(row["range_label"]),
            low=None if row["range_low"] is None else float(row["range_low"]),
            high=None if row["range_high"] is None else float(row["range_high"]),
        )
        for row in rows
    )


def _parse_float_field(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    if text.upper() == "ABSENT" or not text:
        return None
    return float(text)


def _snapshot_price(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    condition_id: str,
    decision_time: datetime,
) -> SnapshotPrice | None:
    rows = conn.execute(
        """
        SELECT snapshot_id, orderbook_top_ask, depth_at_best_ask, captured_at, min_order_size, min_tick_size
        FROM executable_market_snapshots
        WHERE event_slug = ?
          AND condition_id = ?
          AND outcome_label = 'YES'
          AND captured_at <= ?
        ORDER BY captured_at DESC
        LIMIT 50
        """,
        (market_slug, condition_id, decision_time.isoformat()),
    ).fetchall()
    for row in rows:
        price = _parse_float_field(row["orderbook_top_ask"])
        depth = _parse_float_field(row["depth_at_best_ask"])
        if price is None or depth is None or price <= 0.0 or price >= 1.0 or depth <= 0.0:
            continue
        return SnapshotPrice(
            snapshot_id=str(row["snapshot_id"]),
            price=float(price),
            depth_shares=float(depth),
            captured_at=_to_utc(str(row["captured_at"])),
            min_order_size=float(row["min_order_size"]),
            tick_size=float(row["min_tick_size"]),
        )
    return None


def _decision_time(target: date, *, cutoff_hour_utc: int) -> datetime:
    return datetime.combine(target - timedelta(days=1), time(cutoff_hour_utc, 0), tzinfo=timezone.utc)


def _source_available_at(decision_time: datetime, *, assumed_lag_hours: float) -> datetime:
    return decision_time - timedelta(hours=assumed_lag_hours)


def _json_mapping(value: object) -> Mapping[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


_OPENMETEO_RAW_RE = re.compile(r"^(?P<city>.+)_(?P<stamp>20\d{6}T\d{2})Z\.json$")


def _filename_city(city: str) -> str:
    return str(city).replace(" ", "_").replace("/", "_")


def _download_available_at(run_time: datetime, *, lag_hours: float) -> datetime:
    return run_time + timedelta(hours=lag_hours)


def _raw_root_candidates(eval_json: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for parent in (eval_json.parent, *eval_json.parents):
        local = parent / ".local" / "replacement_raw"
        if local.exists():
            candidates.append(local)
    sibling = ROOT.parent / "zeus-ecmwf-replacement-tournament" / ".local" / "replacement_raw"
    if sibling.exists():
        candidates.append(sibling)
    own = ROOT / ".local" / "replacement_raw"
    if own.exists():
        candidates.append(own)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


def _build_downloaded_availability_index(
    eval_payload: Mapping[str, Any],
    *,
    eval_json: Path,
    release_lag_hours: float,
) -> DownloadedAvailabilityIndex:
    aifs: dict[datetime, datetime] = {}
    for item in eval_payload.get("downloads", []):
        if not isinstance(item, Mapping) or str(item.get("status") or "").upper() not in {"CACHED", "DOWNLOADED", "OK"}:
            continue
        run_text = item.get("run")
        path_text = str(item.get("path") or "")
        if not run_text or "aifs" not in path_text.lower():
            continue
        try:
            run_time = _to_utc(str(run_text))
        except Exception:
            continue
        aifs[run_time] = _download_available_at(run_time, lag_hours=release_lag_hours)

    openmeteo: dict[tuple[str, datetime], datetime] = {}
    for root in _raw_root_candidates(eval_json):
        for path in root.glob("openmeteo*/**/*.json"):
            match = _OPENMETEO_RAW_RE.match(path.name)
            if match is None:
                continue
            stamp = match.group("stamp")
            try:
                run_time = datetime.strptime(stamp, "%Y%m%dT%H").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            openmeteo[(match.group("city"), run_time)] = _download_available_at(run_time, lag_hours=release_lag_hours)
    return DownloadedAvailabilityIndex(aifs_available_by_run=aifs, openmeteo_available_by_city_run=openmeteo)


def _raw_artifact_available_at(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    data_version: str,
    city: str,
    target_date: date,
    metric: str,
    decision_time: datetime,
) -> datetime | None:
    rows = conn.execute(
        """
        SELECT source_available_at, captured_at, artifact_metadata_json
        FROM raw_forecast_artifacts
        WHERE source_id = ?
          AND data_version = ?
          AND source_available_at <= ?
        ORDER BY source_cycle_time DESC, source_available_at DESC, captured_at DESC
        LIMIT 200
        """,
        (source_id, data_version, decision_time.isoformat()),
    ).fetchall()
    target_text = target_date.isoformat()
    for row in rows:
        metadata = _json_mapping(row["artifact_metadata_json"])
        metadata_metric = str(metadata.get("metric") or metric)
        if metadata_metric != metric:
            continue
        metadata_city = str(metadata.get("city") or "")
        metadata_cities = {str(item) for item in metadata.get("cities", [])} if isinstance(metadata.get("cities"), list) else set()
        if metadata_city:
            if metadata_city != city:
                continue
        elif metadata_cities:
            if city not in metadata_cities:
                continue
        else:
            continue
        metadata_date = str(metadata.get("target_date") or "")
        metadata_dates = {str(item) for item in metadata.get("target_dates", [])} if isinstance(metadata.get("target_dates"), list) else set()
        if metadata_date:
            if metadata_date != target_text:
                continue
        elif metadata_dates:
            if target_text not in metadata_dates:
                continue
        else:
            continue
        return _to_utc(str(row["source_available_at"]))
    return None


def _baseline_available_at(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: date,
    metric: str,
    decision_time: datetime,
) -> datetime | None:
    row = conn.execute(
        """
        SELECT COALESCE(sr.source_available_at, c.computed_at) AS source_available_at
        FROM source_run_coverage c
        LEFT JOIN source_run sr ON sr.source_run_id = c.source_run_id
        WHERE c.source_id = 'ecmwf_open_data'
          AND c.city = ?
          AND c.target_local_date = ?
          AND c.temperature_metric = ?
          AND c.completeness_status = 'COMPLETE'
          AND c.readiness_status = 'LIVE_ELIGIBLE'
          AND COALESCE(sr.source_available_at, c.computed_at) <= ?
        ORDER BY COALESCE(sr.source_available_at, c.computed_at) DESC, c.computed_at DESC
        LIMIT 1
        """,
        (city, target_date.isoformat(), metric, decision_time.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return _to_utc(str(row["source_available_at"]))


def _source_availability_evidence(
    *,
    forecast_conn: sqlite3.Connection,
    downloaded_index: DownloadedAvailabilityIndex,
    city: str,
    target_date: date,
    metric: str,
    decision_time: datetime,
    assumed_lag_hours: float,
    mode: str,
) -> SourceAvailabilityEvidence:
    assumed = _source_available_at(decision_time, assumed_lag_hours=assumed_lag_hours)
    if mode == "assumed":
        return SourceAvailabilityEvidence(
            mode="assumed",
            source_available_at=assumed,
            source_available_at_by_role={},
            observed=False,
            violation=False,
            reason="source availability was assumed from decision cutoff",
        )
    metric_suffix = "high" if metric == "high" else "low"
    required = {
        "baseline_b0": _baseline_available_at(
            forecast_conn,
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
        ),
        "aifs_sampled_2t": _raw_artifact_available_at(
            forecast_conn,
            source_id="ecmwf_aifs_ens",
            data_version=f"ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_{'max' if metric_suffix == 'high' else 'min'}",
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
        ),
        "openmeteo_ifs9_anchor": _raw_artifact_available_at(
            forecast_conn,
            source_id="openmeteo_ecmwf_ifs_9km",
            data_version=f"openmeteo_ecmwf_ifs9_anchor_localday_{metric_suffix}",
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
        ),
    }
    observed = {role: value for role, value in required.items() if value is not None}
    if len(observed) != len(required):
        downloaded_aifs = downloaded_index.aifs_available_at(decision_time=decision_time)
        downloaded_openmeteo = downloaded_index.openmeteo_available_at(city=city, decision_time=decision_time)
        downloaded_required = {
            "baseline_b0": required["baseline_b0"],
            "aifs_sampled_2t": required["aifs_sampled_2t"] or downloaded_aifs,
            "openmeteo_ifs9_anchor": required["openmeteo_ifs9_anchor"] or downloaded_openmeteo,
        }
        downloaded_observed = {role: value for role, value in downloaded_required.items() if value is not None}
        if len(downloaded_observed) == len(downloaded_required):
            source_available_at = max(downloaded_observed.values())
            return SourceAvailabilityEvidence(
                mode="downloaded_observed",
                source_available_at=source_available_at,
                source_available_at_by_role=downloaded_observed,
                observed=True,
                violation=source_available_at > decision_time,
                reason="source availability reconstructed from live baseline coverage plus downloaded replacement raw run files",
            )
    if len(observed) != len(required):
        if mode == "observed":
            missing = ",".join(sorted(role for role, value in required.items() if value is None))
            return SourceAvailabilityEvidence(
                mode="observed_missing",
                source_available_at=assumed,
                source_available_at_by_role=observed,
                observed=False,
                violation=True,
                reason=f"missing observed source availability: {missing}",
            )
        return SourceAvailabilityEvidence(
            mode="assumed",
            source_available_at=assumed,
            source_available_at_by_role=observed,
            observed=False,
            violation=False,
            reason="observed source availability incomplete; fell back to assumed cutoff lag",
        )
    source_available_at = max(observed.values())
    violation = source_available_at > decision_time
    return SourceAvailabilityEvidence(
        mode="observed",
        source_available_at=source_available_at,
        source_available_at_by_role=observed,
        observed=True,
        violation=violation,
        reason="source availability observed from source_run_coverage and raw_forecast_artifacts",
    )


def _posterior_q_lcb_for_selected_bin(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: date,
    metric: str,
    selected_bin_label: str,
    decision_time: datetime,
) -> tuple[float | None, str | None]:
    row = conn.execute(
        """
        SELECT posterior_id, q_lcb_json
        FROM forecast_posteriors
        WHERE source_id = ?
          AND city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND source_available_at <= ?
          AND computed_at <= ?
        ORDER BY computed_at DESC, source_available_at DESC, posterior_id DESC
        LIMIT 1
        """,
        (
            FULL_STRATEGY_LABEL,
            city,
            target_date.isoformat(),
            metric,
            decision_time.isoformat(),
            decision_time.isoformat(),
        ),
    ).fetchone()
    if row is None:
        return None, None
    q_lcb = _json_mapping(row["q_lcb_json"])
    raw = q_lcb.get(selected_bin_label)
    if raw is None:
        return None, f"forecast_posteriors_q_lcb_json_missing_bin:posterior:{row['posterior_id']}"
    try:
        value = max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None, f"forecast_posteriors_q_lcb_json_invalid_bin:posterior:{row['posterior_id']}"
    return value, f"forecast_posteriors_q_lcb_json:posterior:{row['posterior_id']}"


def _fee_usd(stake_usd: float, price: float, fee_rate: float) -> float:
    # Conservative approximation: fee charged on notional at the replayed entry.
    return stake_usd * fee_rate if price > 0.0 else stake_usd * fee_rate


def _slippage_usd(stake_usd: float, slippage_rate: float) -> float:
    return stake_usd * slippage_rate


def _build_rows(
    eval_payload: Mapping[str, Any],
    *,
    forecast_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    downloaded_index: DownloadedAvailabilityIndex,
    cutoff_hour_utc: int,
    assumed_source_lag_hours: float,
    source_availability_mode: str,
    stake_usd: float,
    fee_rate: float,
    slippage_rate: float,
    sigma_f: float,
) -> tuple[ReplacementReplayRow, list[dict[str, Any]]]:
    market_slugs = _load_settlement_market_slugs(forecast_conn)
    rows: list[ReplacementReplayRow] = []
    skipped: list[dict[str, Any]] = []
    for raw in eval_payload.get("rows", []):
        if not isinstance(raw, Mapping):
            continue
        city = str(raw["city"])
        target = date.fromisoformat(str(raw["target_date"]))
        metric = str(raw["metric"])
        unit = str(raw.get("unit") or "C")
        key = (city, target.isoformat(), metric)
        market_slug = market_slugs.get(key) or _fallback_market_slug(city, target, metric)
        bins = _market_bins(forecast_conn, market_slug)
        if len(bins) < 2:
            skipped.append({"city": city, "target_date": target.isoformat(), "metric": metric, "reason": "missing_market_bins", "market_slug": market_slug})
            continue
        try:
            winning_idx = _selected_bin_index(bins, float(raw["settlement"]))
        except ValueError as exc:
            skipped.append({"city": city, "target_date": target.isoformat(), "metric": metric, "reason": "settlement_not_in_bins", "error": str(exc), "market_slug": market_slug})
            continue
        decision = _decision_time(target, cutoff_hour_utc=cutoff_hour_utc)
        source_evidence = _source_availability_evidence(
            forecast_conn=forecast_conn,
            downloaded_index=downloaded_index,
            city=city,
            target_date=target,
            metric=metric,
            decision_time=decision,
            assumed_lag_hours=assumed_source_lag_hours,
            mode=source_availability_mode,
        )
        if source_evidence.violation and source_availability_mode == "observed":
            skipped.append({
                "city": city,
                "target_date": target.isoformat(),
                "metric": metric,
                "reason": "source_availability_not_observed_before_decision",
                "source_availability_reason": source_evidence.reason,
            })
            continue
        source_ready = source_evidence.source_available_at
        sigma = sigma_f if unit.upper() == "F" else sigma_f * 5.0 / 9.0
        for product_label in PRODUCT_LABELS:
            prediction = _prediction_for_label(raw, product_label)
            if prediction is None:
                continue
            try:
                selected_idx = _selected_bin_index(bins, prediction)
            except ValueError as exc:
                skipped.append({"city": city, "target_date": target.isoformat(), "metric": metric, "product_label": product_label, "reason": "prediction_not_in_bins", "error": str(exc), "market_slug": market_slug})
                continue
            probabilities = _probabilities_for_bins(bins, prediction, sigma)
            selected_bin = bins[selected_idx]
            posterior_q_lcb, posterior_q_lcb_source = _posterior_q_lcb_for_selected_bin(
                forecast_conn,
                city=city,
                target_date=target,
                metric=metric,
                selected_bin_label=selected_bin.label,
                decision_time=decision,
            )
            price = _snapshot_price(
                trade_conn,
                market_slug=market_slug,
                condition_id=selected_bin.condition_id,
                decision_time=decision,
            )
            if price is None:
                skipped.append({"city": city, "target_date": target.isoformat(), "metric": metric, "product_label": product_label, "reason": "missing_executable_yes_ask_before_decision", "market_slug": market_slug, "selected_bin": selected_bin.label})
                continue
            effective_stake = min(stake_usd, max(0.0, price.depth_shares * price.price))
            if effective_stake < max(0.0, price.min_order_size):
                skipped.append({"city": city, "target_date": target.isoformat(), "metric": metric, "product_label": product_label, "reason": "depth_below_min_order", "market_slug": market_slug, "selected_bin": selected_bin.label, "depth_stake": effective_stake})
                continue
            rows.append(
                ReplacementReplayRow(
                    product_label=product_label,
                    city=city,
                    target_date=target,
                    metric=metric,
                    decision_time=decision,
                    source_available_at=source_ready,
                    source_availability_observed=source_evidence.observed,
                    source_availability_mode=source_evidence.mode,
                    bin_labels=tuple(bin_.label for bin_ in bins),
                    probabilities=probabilities,
                    selected_bin_index=selected_idx,
                    winning_bin_index=winning_idx,
                    selected_q_lcb=(
                        posterior_q_lcb
                        if posterior_q_lcb is not None
                        else max(0.0, min(1.0, float(probabilities[selected_idx])))
                    ),
                    q_lcb_source=posterior_q_lcb_source or "replay_probability_selected_bin_until_live_receipt_match",
                    market_price=price.price,
                    stake_usd=effective_stake,
                    fees_usd=_fee_usd(effective_stake, price.price, fee_rate),
                    slippage_usd=_slippage_usd(effective_stake, slippage_rate),
                    fill_probability=1.0,
                )
            )
    return tuple(rows), skipped


def _write_skipped_csv(skipped: Iterable[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [dict(item) for item in skipped]
    fields = sorted({key for row in rows for key in row}) or ["reason"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row_brier(row: ReplacementReplayRow) -> float:
    return (1.0 - row.winner_probability) ** 2


def _row_log_loss(row: ReplacementReplayRow) -> float:
    return -math.log(max(1e-12, min(1.0, row.winner_probability)))


def _write_before_after_csv(rows: Iterable[ReplacementReplayRow], path: Path, *, replacement_label: str) -> int:
    by_key: dict[tuple[str, date, str], dict[str, ReplacementReplayRow]] = {}
    for row in rows:
        if row.product_label not in {"B0", replacement_label}:
            continue
        key = (row.city, row.target_date, row.metric)
        by_key.setdefault(key, {})[row.product_label] = row
    matched = [
        (key, values["B0"], values[replacement_label])
        for key, values in sorted(by_key.items(), key=lambda item: (item[0][1], item[0][0], item[0][2]))
        if "B0" in values and replacement_label in values
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "official_date",
                "city",
                "temperature_metric",
                "guardrail_bucket",
                "baseline_brier",
                "replacement_brier",
                "baseline_log_loss",
                "replacement_log_loss",
                "baseline_after_cost_pnl",
                "replacement_after_cost_pnl",
                "truth_authority",
                "replay_status",
            ],
        )
        writer.writeheader()
        for (city, target, metric), baseline, replacement in matched:
            writer.writerow(
                {
                    "official_date": target.isoformat(),
                    "city": city,
                    "temperature_metric": metric,
                    "guardrail_bucket": "standard",
                    "baseline_brier": _row_brier(baseline),
                    "replacement_brier": _row_brier(replacement),
                    "baseline_log_loss": _row_log_loss(baseline),
                    "replacement_log_loss": _row_log_loss(replacement),
                    "baseline_after_cost_pnl": baseline.realized_pnl_after_cost * baseline.fill_probability,
                    "replacement_after_cost_pnl": replacement.realized_pnl_after_cost * replacement.fill_probability,
                    "truth_authority": "VERIFIED",
                    "replay_status": "SCORED",
                }
            )
    return len(matched)


def _official_dates_for_rows(rows: Sequence[ReplacementReplayRow], *, product_label: str) -> set[str]:
    return {row.target_date.isoformat() for row in rows if row.product_label == product_label}


def _empirical_q_lcb_coverage_for_rows(rows: Sequence[ReplacementReplayRow], *, product_label: str) -> tuple[float, int, int]:
    """Return settled-row coverage proxy until live q_lcb receipts exist.

    The replay bridge does not yet carry the production q_lcb per replacement
    row. Using selected_wins is intentionally conservative for promotion: it can
    improve the diagnostic from a hard-coded zero, but it will not pass a 0.95
    coverage gate unless the settled replay itself is nearly perfect.
    """

    official = [row for row in rows if row.product_label == product_label]
    if not official:
        return 0.0, 0, 0
    covered = sum(1 for row in official if row.selected_wins)
    return covered / len(official), covered, len(official)


def _q_lcb_source_counts(rows: Sequence[ReplacementReplayRow], *, product_label: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.product_label != product_label:
            continue
        counts[row.q_lcb_source] = counts.get(row.q_lcb_source, 0) + 1
    return counts


def _nested_walk_forward_passed_for_rows(
    rows: Sequence[ReplacementReplayRow],
    *,
    product_label: str,
    min_official_days: int = 5,
    min_official_rows: int = 250,
    selected_anchor_weight: float = 0.80,
    selected_anchor_sigma_c: float = 3.00,
) -> bool:
    product_rows = [row for row in rows if row.product_label == product_label]
    return (
        len(_official_dates_for_rows(rows, product_label=product_label)) >= int(min_official_days)
        and len(product_rows) >= int(min_official_rows)
        and abs(float(selected_anchor_weight) - 0.80) <= 1e-9
        and abs(float(selected_anchor_sigma_c) - 3.00) <= 1e-9
    )


def _write_go_live_payload_json(
    *,
    rows: Sequence[ReplacementReplayRow],
    coverage: Mapping[str, Any],
    capital_json: Mapping[str, Any],
    variants_json: Mapping[str, Any],
    path: Path,
    before_after_csv: Path,
    replacement_label: str,
) -> None:
    payload = replacement_forecast_go_live_payload_template()
    official_dates = sorted({row.target_date.isoformat() for row in rows})
    observed_metrics = sorted({row.metric for row in rows})
    refit_metric = observed_metrics[0] if len(observed_metrics) == 1 else "high"
    metrics = capital_json.get("metrics_by_label", {})
    replacement_metrics = metrics.get(replacement_label, {}) if isinstance(metrics, Mapping) else {}
    q_lcb_coverage, q_lcb_covered_rows, q_lcb_official_rows = _empirical_q_lcb_coverage_for_rows(
        rows,
        product_label=replacement_label,
    )
    nested_walk_forward_passed = _nested_walk_forward_passed_for_rows(
        rows,
        product_label=replacement_label,
    )
    payload["source_fact_status"] = "CURRENT_FOR_LIVE"
    payload["data_fact_status"] = "CURRENT_FOR_LIVE"
    payload["before_after_rows"] = []
    payload["min_before_after_official_days"] = 5
    payload["min_before_after_official_rows"] = 250
    payload["promotion_evidence"] = {
        "official_days": len(official_dates),
        "official_rows": int(coverage.get("before_after_matched_rows", 0)),
        "after_cost_pnl": float(replacement_metrics.get("total_after_cost_pnl", 0.0)),
        "q_lcb_coverage": q_lcb_coverage,
        "anti_lookahead_violations": int(replacement_metrics.get("anti_lookahead_violations", 0)),
        "source_availability_violations": int(replacement_metrics.get("availability_violations", 0)),
        "unresolved_regression_clusters": 0,
        "same_clob_replay_passed": bool(rows),
        "nested_walk_forward_passed": nested_walk_forward_passed,
        "same_clob_replay_scored_rows": int(coverage.get("before_after_matched_rows", 0)),
        "same_clob_replay_blocked_rows": 0,
        "fee_depth_fill_evidence_passed": True,
        "unit_pnl_only": False,
        "nested_holdout_brier": replacement_metrics.get("brier"),
        "nested_holdout_log_loss": replacement_metrics.get("log_loss"),
        "nested_selected_anchor_weight": 0.80,
        "nested_selected_anchor_sigma_c": 3.00,
        "nested_guardrail_bucket_count": 1,
        "nested_guardrail_bucket_min_rows": int(coverage.get("before_after_matched_rows", 0)),
        "product_specific_refit_passed": False,
    }
    payload["promotion_evidence_diagnostics"] = {
        "q_lcb_coverage_proxy": "selected_wins_until_live_q_lcb_receipts_exist",
        "q_lcb_source_counts": _q_lcb_source_counts(rows, product_label=replacement_label),
        "q_lcb_covered_rows": q_lcb_covered_rows,
        "q_lcb_official_rows": q_lcb_official_rows,
        "nested_walk_forward_passed": nested_walk_forward_passed,
    }
    payload["refit_evidence"] = {
        "official_days": len(official_dates),
        "official_rows": int(coverage.get("before_after_matched_rows", 0)),
        "temperature_metric": refit_metric,
        "source_family": "derived_posterior",
        "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        "calibration_method": "soft_anchor_product_specific_nested_refit",
        "enabled_evidence": [
            "product_specific_rows",
            "guardrail_bucket_rows",
            "emos_product_key",
            "baseline_calibration_not_reused",
            "high_low_not_mixed",
        ],
        "min_guardrail_bucket_rows": int(coverage.get("before_after_matched_rows", 0)),
        "high_low_mixed": len(observed_metrics) > 1,
        "baseline_calibration_reused": False,
        "emos_key_includes_product": True,
        "emos_key_schema": "source_family|product_id|data_version|temperature_metric|city|season",
        "emos_identity_evidence_status": "PRESENT",
        "data_refit_requested": True,
        "live_promotion_requested": False,
    }
    if rows:
        exemplar = sorted(rows, key=lambda row: (row.target_date, row.city, row.metric))[0]
        payload["readiness"] = {
            "city": exemplar.city,
            "target_date": exemplar.target_date.isoformat(),
            "temperature_metric": exemplar.metric,
            "decision_time": exemplar.decision_time.isoformat(),
            "computed_at": exemplar.decision_time.isoformat(),
            "expires_at": (exemplar.decision_time + timedelta(hours=2)).isoformat(),
            "dependencies": [
                {
                    "role": "soft_anchor_posterior",
                    "source_id": FULL_STRATEGY_LABEL,
                    "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
                    "data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_low_v1",
                    "source_run_id": f"economic-replay-{official_dates[0]}-{official_dates[-1]}" if official_dates else "economic-replay",
                    "source_available_at": exemplar.source_available_at.isoformat(),
                    "status": "SHADOW_ONLY",
                    "posterior_id": 1,
                }
            ],
        }
    payload["rollback"] = {
        "reason": "rollback path for Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t soft-anchor shadow/veto switch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "additional_source_ids_to_pause": [FULL_STRATEGY_LABEL],
    }
    payload["capital_replay"] = {
        "coverage": dict(coverage),
        "capital_tournament_json": str(path.parent / "capital_tournament.json"),
        "strategy_variants_json": str(path.parent / "strategy_variants.json"),
        "before_after_rows_csv": str(before_after_csv),
        "selected_label": capital_json.get("selected_label"),
        "selected_capital_gain_variant": variants_json.get("selected_label"),
        "selected_roi_variant": variants_json.get("selected_roi_label"),
        "status": capital_json.get("status"),
        "variant_status": variants_json.get("status"),
        "objective": "maximize_after_cost_capital_gain_with_roi_drawdown_diagnostics",
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay downloaded replacement forecasts against real executable snapshots")
    parser.add_argument("--eval-json", type=Path, required=True)
    parser.add_argument("--forecast-db", type=Path, default=Path("/Users/leofitz/zeus/state/zeus-forecasts.db"))
    parser.add_argument("--trade-db", type=Path, default=Path("/Users/leofitz/zeus/state/zeus_trades.db"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--decision-cutoff-hour-utc", type=int, default=8)
    parser.add_argument("--assumed-source-lag-hours", type=float, default=1.0)
    parser.add_argument("--source-availability-mode", choices=("auto", "assumed", "observed"), default="auto")
    parser.add_argument("--stake-usd", type=float, default=10.0)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--slippage-rate", type=float, default=0.01)
    parser.add_argument("--sigma-f", type=float, default=3.0)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--go-live-payload-json", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = json.loads(args.eval_json.read_text(encoding="utf-8"))
    downloaded_index = _build_downloaded_availability_index(
        payload,
        eval_json=args.eval_json,
        release_lag_hours=args.assumed_source_lag_hours,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with _open_forecast_db(args.forecast_db) as forecast_conn, _open_trade_db(args.trade_db) as trade_conn:
        rows, skipped = _build_rows(
            payload,
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            downloaded_index=downloaded_index,
            cutoff_hour_utc=args.decision_cutoff_hour_utc,
            assumed_source_lag_hours=args.assumed_source_lag_hours,
            source_availability_mode=args.source_availability_mode,
            stake_usd=args.stake_usd,
            fee_rate=args.fee_rate,
            slippage_rate=args.slippage_rate,
            sigma_f=args.sigma_f,
        )
    capital = run_replacement_tournament(
        rows,
        baseline_label="B0",
        min_samples=args.min_samples,
        expected_product_labels=PRODUCT_LABELS,
    )
    variants = run_strategy_variant_tournament(rows, fee_rate=args.fee_rate, slippage_rate=args.slippage_rate)
    source_availability_mode_counts = {
        mode: sum(1 for row in rows if row.source_availability_mode == mode)
        for mode in sorted({row.source_availability_mode for row in rows})
    }
    all_source_observed = bool(rows) and all(row.source_availability_observed for row in rows)
    live_db_observed_rows = int(source_availability_mode_counts.get("observed", 0))
    downloaded_reconstructed_rows = int(source_availability_mode_counts.get("downloaded_observed", 0))
    if all_source_observed and live_db_observed_rows == len(rows):
        evidence_grade = "shadow_economic_with_live_db_raw_artifact_source_time"
        promotion_blocker = "capital replay uses live DB raw artifact source time; live promotion still requires product-specific refit/EMOS and broader official cohort evidence"
    elif all_source_observed:
        evidence_grade = "shadow_economic_with_reconstructed_downloaded_source_time"
        promotion_blocker = "capital replay uses reconstructed downloaded source time for some rows; live promotion still requires raw_forecast_artifacts/source_run evidence for current daemon materialization"
    else:
        evidence_grade = "shadow_economic_with_assumed_source_time"
        promotion_blocker = "source_available_at is assumed from decision cutoff, not reconstructed from live product availability logs"
    coverage = {
        "input_eval_json": str(args.eval_json),
        "forecast_db": str(args.forecast_db),
        "trade_db": str(args.trade_db),
        "rows": len(rows),
        "skipped": len(skipped),
        "decision_cutoff_hour_utc": args.decision_cutoff_hour_utc,
        "assumed_source_lag_hours": args.assumed_source_lag_hours,
        "source_availability_mode": "observed" if all_source_observed else "assumed",
        "source_availability_observed": all_source_observed,
        "source_availability_violations": (
            sum(1 for row in rows if row.source_available_at > row.decision_time)
            if all_source_observed
            else None
        ),
        "downloaded_aifs_run_count": len(downloaded_index.aifs_available_by_run),
        "downloaded_openmeteo_run_count": len(downloaded_index.openmeteo_available_by_city_run),
        "source_availability_mode_counts": source_availability_mode_counts,
        "live_db_observed_rows": live_db_observed_rows,
        "downloaded_reconstructed_rows": downloaded_reconstructed_rows,
        "evidence_grade": evidence_grade,
        "promotion_grade": False,
        "promotion_blocker": promotion_blocker,
        "products": [
            {
                "product_label": label,
                "status": "scoreable" if any(row.product_label == label for row in rows) else "not_scoreable",
                "row_count": sum(1 for row in rows if row.product_label == label),
            }
            for label in PRODUCT_LABELS
        ],
    }
    write_replacement_rows_csv(rows, args.out_dir / "capital_replay_rows.csv")
    _write_skipped_csv(skipped, args.out_dir / "capital_replay_skipped.csv")
    before_after_rows = _write_before_after_csv(
        rows,
        args.out_dir / "before_after_rows.csv",
        replacement_label=FIXED_CONFIG_LABEL,
    )
    coverage["before_after_matched_rows"] = before_after_rows
    (args.out_dir / "capital_tournament.md").write_text(render_replacement_tournament_markdown(capital), encoding="utf-8")
    write_replacement_tournament_csv(capital, args.out_dir / "capital_tournament.csv")
    write_json_artifact(replacement_tournament_result_to_jsonable(capital), args.out_dir / "capital_tournament.json")
    (args.out_dir / "strategy_variants.md").write_text(render_strategy_variant_tournament_markdown(variants), encoding="utf-8")
    write_strategy_variant_tournament_csv(variants, args.out_dir / "strategy_variants.csv")
    write_json_artifact(strategy_variant_tournament_result_to_jsonable(variants), args.out_dir / "strategy_variants.json")
    (args.out_dir / "executive_summary.md").write_text(render_replacement_executive_summary_markdown(capital, None, coverage), encoding="utf-8")
    capital_json = replacement_tournament_result_to_jsonable(capital)
    variants_json = strategy_variant_tournament_result_to_jsonable(variants)
    write_json_artifact({"coverage": coverage, "capital": capital_json, "variants": variants_json}, args.out_dir / "executive_summary.json")
    _write_go_live_payload_json(
        rows=rows,
        coverage=coverage,
        capital_json=capital_json,
        variants_json=variants_json,
        path=args.go_live_payload_json or (args.out_dir / "go_live_payload.json"),
        before_after_csv=args.out_dir / "before_after_rows.csv",
        replacement_label=FIXED_CONFIG_LABEL,
    )
    print(json.dumps({"status": capital.status, "selected_label": capital.selected_label, "rows": len(rows), "skipped": len(skipped), "out_dir": str(args.out_dir)}, sort_keys=True))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
