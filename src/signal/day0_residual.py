"""Day0 residual learning substrate.

This module builds training facts for a future Day0 residual model without
changing the active runtime signal. It preserves the hard-floor contract:
final high must be at least the observed running max.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from src.signal.day0_residual_features import (
    EnsembleContext,
    daylight_progress,
    ensemble_remaining_quantiles,
    latest_ensemble_before,
    obs_age_minutes,
    parse_member_values,
    post_peak_confidence,
)


@dataclass(frozen=True)
class Day0ResidualFact:
    fact_id: str
    city: str
    target_date: str
    source: str
    local_timestamp: str
    utc_timestamp: str
    local_hour: float | None
    temp_current: float | None
    running_max: float | None
    delta_rate_per_h: float | None
    daylight_progress: float | None
    obs_age_minutes: float | None
    post_peak_confidence: float | None
    ens_q50_remaining: float | None
    ens_q90_remaining: float | None
    ens_spread: float | None
    settlement_value: float | None
    residual_upside: float | None
    has_upside: int | None
    fact_status: str
    missing_reasons: tuple[str, ...]


def residual_target(
    settlement_value: float | None,
    running_max: float | None,
) -> tuple[float | None, int | None]:
    """Return non-negative residual upside and binary upside target."""
    if settlement_value is None or running_max is None:
        return None, None
    residual = max(0.0, float(settlement_value) - float(running_max))
    return residual, int(float(settlement_value) > float(running_max))


def _fact_id(city: str, target_date: str, source: str, utc_timestamp: str) -> str:
    return f"{city}|{target_date}|{source}|{utc_timestamp}"


def build_day0_residual_facts(conn: sqlite3.Connection) -> list[Day0ResidualFact]:
    """Build residual facts from observation instants and final settlements."""
    ensemble_rows = conn.execute(
        """
        SELECT city, target_date, available_at, members_json, spread
        FROM ensemble_snapshots
        ORDER BY city, target_date, available_at
        """
    ).fetchall()
    ensembles_by_key: dict[tuple[str, str], list[EnsembleContext]] = {}
    for row in ensemble_rows:
        members = parse_member_values(row["members_json"])
        if not members:
            continue
        key = (str(row["city"]), str(row["target_date"]))
        ensembles_by_key.setdefault(key, []).append(
            EnsembleContext(
                available_at=str(row["available_at"]),
                members=members,
                spread=None if row["spread"] is None else float(row["spread"]),
            )
        )

    rows = conn.execute(
        """
        SELECT
            oi.city,
            oi.target_date,
            oi.source,
            oi.local_timestamp,
            oi.utc_timestamp,
            oi.local_hour,
            oi.temp_current,
            oi.running_max,
            oi.delta_rate_per_h,
            oi.imported_at,
            sd.sunrise_local,
            sd.sunset_local,
            dpp.p_high_set AS diurnal_p_high_set,
            s.settlement_value
        FROM observation_instants oi
        LEFT JOIN solar_daily sd
          ON sd.city = oi.city
         AND sd.target_date = oi.target_date
        LEFT JOIN diurnal_peak_prob dpp
          ON dpp.city = oi.city
         AND dpp.month = CAST(strftime('%m', oi.target_date) AS INTEGER)
         AND dpp.hour = CAST(oi.local_hour AS INTEGER)
        LEFT JOIN settlements s
          ON s.city = oi.city
         AND s.target_date = oi.target_date
        ORDER BY oi.city, oi.target_date, oi.local_timestamp
        """
    ).fetchall()

    facts: list[Day0ResidualFact] = []
    for row in rows:
        residual, has_upside = residual_target(row["settlement_value"], row["running_max"])
        missing_reasons = []
        if row["temp_current"] is None:
            missing_reasons.append("temp_current")
        if row["running_max"] is None:
            missing_reasons.append("running_max")
        if row["delta_rate_per_h"] is None:
            missing_reasons.append("delta_rate_per_h")
        if row["settlement_value"] is None:
            missing_reasons.append("settlement_value")
        daylight = daylight_progress(row["local_timestamp"], row["sunrise_local"], row["sunset_local"])
        if daylight is None:
            missing_reasons.append("daylight_progress")
        age_minutes = obs_age_minutes(row["utc_timestamp"], row["imported_at"])
        if age_minutes is None:
            missing_reasons.append("obs_age_minutes")
        peak_confidence = post_peak_confidence(daylight, row["diurnal_p_high_set"])
        if peak_confidence is None:
            missing_reasons.append("post_peak_confidence")
        ensemble = latest_ensemble_before(
            str(row["utc_timestamp"]),
            ensembles_by_key.get((str(row["city"]), str(row["target_date"])), []),
        )
        q50_remaining, q90_remaining, ensemble_spread = ensemble_remaining_quantiles(
            row["running_max"],
            None if ensemble is None else ensemble.members,
        )
        if q50_remaining is None:
            missing_reasons.append("ens_q50_remaining")
        if q90_remaining is None:
            missing_reasons.append("ens_q90_remaining")
        if ensemble_spread is None:
            missing_reasons.append("ens_spread")
        fact_status = "missing_inputs" if missing_reasons else "complete"
        city = str(row["city"])
        target_date = str(row["target_date"])
        source = str(row["source"])
        local_timestamp = str(row["local_timestamp"])
        utc_timestamp = str(row["utc_timestamp"])
        facts.append(
            Day0ResidualFact(
                fact_id=_fact_id(city, target_date, source, utc_timestamp),
                city=city,
                target_date=target_date,
                source=source,
                local_timestamp=local_timestamp,
                utc_timestamp=utc_timestamp,
                local_hour=None if row["local_hour"] is None else float(row["local_hour"]),
                temp_current=None if row["temp_current"] is None else float(row["temp_current"]),
                running_max=None if row["running_max"] is None else float(row["running_max"]),
                delta_rate_per_h=None if row["delta_rate_per_h"] is None else float(row["delta_rate_per_h"]),
                daylight_progress=daylight,
                obs_age_minutes=age_minutes,
                post_peak_confidence=peak_confidence,
                ens_q50_remaining=q50_remaining,
                ens_q90_remaining=q90_remaining,
                ens_spread=ensemble_spread,
                settlement_value=None if row["settlement_value"] is None else float(row["settlement_value"]),
                residual_upside=residual,
                has_upside=has_upside,
                fact_status=fact_status,
                missing_reasons=tuple(missing_reasons),
            )
        )
    return facts


def write_day0_residual_facts(
    conn: sqlite3.Connection,
    facts: list[Day0ResidualFact],
    *,
    recorded_at: str,
) -> int:
    """Materialize Day0 residual facts into the additive fact table."""
    if not facts:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO day0_residual_fact (
            fact_id,
            city,
            target_date,
            source,
            local_timestamp,
            utc_timestamp,
            local_hour,
            temp_current,
            running_max,
            delta_rate_per_h,
            daylight_progress,
            obs_age_minutes,
            post_peak_confidence,
            ens_q50_remaining,
            ens_q90_remaining,
            ens_spread,
            settlement_value,
            residual_upside,
            has_upside,
            fact_status,
            missing_reason_json,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                fact.fact_id,
                fact.city,
                fact.target_date,
                fact.source,
                fact.local_timestamp,
                fact.utc_timestamp,
                fact.local_hour,
                fact.temp_current,
                fact.running_max,
                fact.delta_rate_per_h,
                fact.daylight_progress,
                fact.obs_age_minutes,
                fact.post_peak_confidence,
                fact.ens_q50_remaining,
                fact.ens_q90_remaining,
                fact.ens_spread,
                fact.settlement_value,
                fact.residual_upside,
                fact.has_upside,
                fact.fact_status,
                json.dumps(list(fact.missing_reasons), ensure_ascii=False),
                recorded_at,
            )
            for fact in facts
        ],
    )
    return len(facts)
