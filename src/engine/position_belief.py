# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: settlement-losses incident 2026-06-12 (719/719 stale monitor
#   refreshes on the Karachi position; entry authority = forecast_posteriors,
#   exit authority = dead legacy day0/ens chain) + external consult
#   REQ-20260612-052802 K1 (single belief authority) + replacement chain
#   authority docs/authority/replacement_final_form_2026_06_09.md.
"""Replacement-chain belief authority for HELD positions (K1 single authority).

THE DISEASE THIS KILLS: the position-exit monitor's probability came from a
legacy chain (``day0_metric_fact`` / live-ens ``monitor_fallback``) that has
been dead since inception — ``last_monitor_prob_is_fresh`` was False for
719/719 monitor refreshes of the Karachi 2026-06-12 position while the ENTRY
authority (``forecast_posteriors``, the strategy of record) was alive and had
already moved the held bin to family top rank 18 hours before settlement.
Entry brain and exit brain read different data sources (twin-authority); the
exit organ was structurally blind while three positions settled at a loss.

THE CONTRACT:
- Held-position belief comes from the SAME table the entry decision used:
  ``forecast_posteriors``, freshest row per (city, target_date, metric).
  The bin is indexed by the position's ``bin_label`` — q_json keys are the
  venue range-label strings, the exact strings entry certified against.
- Held-side conversion happens here, exactly once:
  buy_yes -> q(bin), buy_no -> 1 - q(bin). Position space is always held-side.
- Freshness is an explicit age budget (settings key
  the replacement source-cycle staleness horizon when ``source_cycle_time`` is
  present, falling back to the legacy explicit age budget only for old schemas.
  A stale or missing row NEVER silently borrows freshness from another source —
  callers may still run legacy refreshers for telemetry, but probability-
  authority freshness stays False.
- Reads use a private short-lived read-only connection (URI mode=ro), never a
  shared live connection, and are never held across network I/O.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 9.0
BELIEF_SOURCE_TABLE = "forecast_posteriors"
SELECTED_METHOD_REPLACEMENT_POSTERIOR = "replacement_posterior"

_WS_RE = re.compile(r"\s+")


def _normalize_label(label: str) -> str:
    return _WS_RE.sub(" ", str(label or "").strip()).casefold()


@dataclass(frozen=True)
class ReplacementBelief:
    """One held-position belief read from the replacement posterior authority."""

    held_side_prob: float
    q_yes_bin: float
    posterior_id: str
    computed_at: str
    age_hours: float
    fresh: bool
    bin_key: str
    direction: str
    source_table: str = BELIEF_SOURCE_TABLE
    source_cycle_time: str | None = None
    source_cycle_age_hours: float | None = None
    freshness_basis: str = "computed_at"
    trade_authority_status: str = "LIVE_AUTHORITY"

    def freshness_validation(self) -> str:
        state = "fresh" if self.fresh else "stale"
        if self.source_cycle_age_hours is not None:
            return (
                f"belief_source={self.source_table};age_h={self.age_hours:.2f};"
                f"source_cycle_age_h={self.source_cycle_age_hours:.2f};"
                f"basis={self.freshness_basis};{state}"
            )
        return f"belief_source={self.source_table};age_h={self.age_hours:.2f};{state}"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _parse_computed_at(raw: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _match_bin(q: Mapping[str, object], bin_label: str) -> tuple[str, float] | None:
    """Exact key match first; whitespace/case-normalized fallback. Fail-closed."""
    if bin_label in q:
        try:
            return bin_label, float(q[bin_label])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    want = _normalize_label(bin_label)
    if not want:
        return None
    for key, value in q.items():
        if _normalize_label(key) == want:
            try:
                return key, float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
    return None


def load_replacement_belief(
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    bin_label: str,
    direction: str,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    db_path: str | None = None,
) -> ReplacementBelief | None:
    """Freshest replacement-chain belief for a held bin, or None (fail-closed).

    Returns None when: no posterior row exists for the (city, target_date,
    metric) family, q_json is not a mapping, the held bin label cannot be
    matched, q is non-finite/out of [0, 1], or computed_at is unparseable.
    A row that matches but is older than ``max_age_hours`` is RETURNED with
    ``fresh=False`` — staleness is information, absence is not.
    """
    # Direction arrives as the coerced Direction enum on live Position objects;
    # str(Direction.NO) is "Direction.NO" (not a str-mixin), which silently
    # failed this guard on every live monitor cycle (2026-06-12, caught in
    # post-restart verification). Normalize via .value first.
    direction = str(getattr(direction, "value", direction))
    if direction not in ("buy_yes", "buy_no"):
        return None
    if db_path is None:
        from src.state.db import ZEUS_FORECASTS_DB_PATH

        db_path = str(ZEUS_FORECASTS_DB_PATH)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        logger.warning("position_belief: read-only open failed: %s", exc)
        return None
    try:
        conn.row_factory = sqlite3.Row
        columns = _table_columns(conn, "forecast_posteriors")
        if "trade_authority_status" not in columns:
            logger.warning(
                "position_belief: forecast_posteriors missing trade_authority_status; "
                "no live-authority belief available"
            )
            return None
        source_cycle_expr = (
            "source_cycle_time"
            if "source_cycle_time" in columns
            else "NULL AS source_cycle_time"
        )
        row = conn.execute(
            f"""
            SELECT posterior_id, computed_at, q_json, {source_cycle_expr}, trade_authority_status
            FROM forecast_posteriors
            WHERE city = ? AND target_date = ? AND temperature_metric = ?
              AND trade_authority_status = 'LIVE_AUTHORITY'
            ORDER BY datetime(computed_at) DESC, posterior_id DESC
            LIMIT 1
            """,
            (city, target_date, temperature_metric),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("position_belief: posterior read failed: %s", exc)
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        q = json.loads(row["q_json"] or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(q, dict):
        return None
    matched = _match_bin(q, bin_label)
    if matched is None:
        return None
    bin_key, q_yes = matched
    if not (0.0 <= q_yes <= 1.0):
        return None
    computed_at = _parse_computed_at(row["computed_at"])
    if computed_at is None:
        # Unparseable timestamp must not be branded fresh (fail-closed; the
        # 2026-06-11 serving-freshness incident class).
        return None
    now_dt = now or datetime.now(timezone.utc)
    age_hours = (now_dt - computed_at).total_seconds() / 3600.0
    source_cycle_time = _parse_computed_at(row["source_cycle_time"])
    source_cycle_age_hours: float | None = None
    freshness_basis = "computed_at"
    fresh = 0.0 <= age_hours <= float(max_age_hours)
    if source_cycle_time is not None:
        try:
            from src.data.replacement_forecast_cycle_policy import (
                cycle_age_hours,
                cycle_age_exceeds_bound,
            )

            source_cycle_age_hours = cycle_age_hours(now_dt, source_cycle_time)
            fresh = (
                0.0 <= age_hours
                and 0.0 <= source_cycle_age_hours
                and not cycle_age_exceeds_bound(now_dt, source_cycle_time)
            )
            freshness_basis = "source_cycle_time"
        except Exception:  # noqa: BLE001 - keep the old explicit age gate as fallback
            source_cycle_age_hours = (now_dt - source_cycle_time).total_seconds() / 3600.0
            fresh = 0.0 <= age_hours <= float(max_age_hours)
    held = q_yes if direction == "buy_yes" else 1.0 - q_yes
    return ReplacementBelief(
        held_side_prob=held,
        q_yes_bin=q_yes,
        posterior_id=str(row["posterior_id"]),
        computed_at=str(row["computed_at"]),
        age_hours=age_hours,
        fresh=fresh,
        bin_key=bin_key,
        direction=direction,
        source_cycle_time=(
            source_cycle_time.isoformat() if source_cycle_time is not None else None
        ),
        source_cycle_age_hours=source_cycle_age_hours,
        freshness_basis=freshness_basis,
        trade_authority_status=str(row["trade_authority_status"]),
    )


def monitor_belief_max_age_hours() -> float:
    """Settings-resolved age budget for monitor belief freshness."""
    try:
        from src.config import settings

        raw = (settings.get("edli") or {}).get("monitor_belief_max_age_hours")
        if raw is not None:
            value = float(raw)
            if value > 0:
                return value
    except Exception:  # noqa: BLE001 — settings shape drift must not kill the monitor
        pass
    return DEFAULT_MAX_AGE_HOURS
