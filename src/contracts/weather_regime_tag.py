# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md §"Required object model" + docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T1

"""WeatherRegimeTag — 6-member StrEnum classifying weather regime context for shoulder strategy.

Design intent (§2 T1, dossier §7.5):
  - HEAT_DOME / COLD_SNAP / NORMAL / SHOULDER_SEASON / SOURCE_ANOMALY / UNKNOWN
  - 6-member minimal taxonomy; operator-extensible via PROMOTION_PLAYBOOK post-T1.
  - regime_tag_for(): fail-open to UNKNOWN when observation history is insufficient
    for HEAT_DOME/COLD_SNAP classification (G2: no silent default).

Usage:
    tag = regime_tag_for(city, target_date, decision_time, conn)
    # Returns WeatherRegimeTag.UNKNOWN when evidence is thin; never silently
    # returns a strong regime label without sufficient observation history.
"""

from __future__ import annotations

import sqlite3
import statistics
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime


# Minimum number of historical observation days required for non-UNKNOWN classification.
# Below this threshold: fail-open to UNKNOWN (G2 invariant).
# Derivation: 7 days is the minimum rolling window that captures a full weather pattern
# cycle; fewer days cannot reliably distinguish a sustained regime from transient noise.
_MIN_HISTORY_DAYS = 7

# Standard-deviation threshold for HEAT_DOME / COLD_SNAP classification.
# +2σ from the seasonal rolling mean → HEAT_DOME (persistent anomalous warmth).
# −2σ from the seasonal rolling mean → COLD_SNAP (persistent anomalous cold).
# Derivation: 2σ corresponds to ~5% exceedance frequency under a normal distribution,
# capturing statistically rare persistent-temperature regimes that drive correlated
# shoulder-market risk. Chosen to be wide enough to suppress false positives on
# single-day spikes while still triggering on 3+ day sustained events.
_SIGMA_THRESHOLD = 2.0

# Cross-source z-score threshold for SOURCE_ANOMALY classification.
# When two or more sources report observations and their spread (max - min) / pooled_std
# exceeds this threshold, the observation is flagged as a potential sensor anomaly.
# Derivation: ~3σ separation between two co-located sources is implausible climatologically
# and is consistent with the Paris 2026 sensor-spike case cited in dossier §0.7.
_SOURCE_ANOMALY_Z_THRESHOLD = 3.0

# Calendar months considered "shoulder season" (transitional spring/autumn).
# March–May (spring) and September–November (autumn) in the Northern Hemisphere.
# Southern Hemisphere cities use inverted months; for simplicity we apply the same
# window globally (the strategy is primarily NH-weighted per cities.json).
_SHOULDER_MONTHS: frozenset[int] = frozenset({3, 4, 5, 9, 10, 11})


class WeatherRegimeTag(StrEnum):
    """Six-member weather-regime taxonomy for shoulder strategy context.

    Verbatim from authority 04_PHASE_3_SHOULDER.md §"Required object model":
        tail_regime_tag: WeatherRegimeTag  # heat_dome / cold_snap / normal /
                                           # shoulder_season / anomaly / unknown

    Members (6 per plan §2 T1 + P-3-2 verifier probe):
        HEAT_DOME        — persistent high-pressure blocking event; correlated upper-shoulder risk
        COLD_SNAP        — sustained cold-air outbreak; correlated lower-shoulder risk
        NORMAL           — observations within climatological norms; standard Kelly applies
        SHOULDER_SEASON  — transitional period (spring/autumn); intermediate regime uncertainty
        SOURCE_ANOMALY   — sensor spike or station reporting anomaly detected (Paris 2026 class)
        UNKNOWN          — insufficient observation history to classify; classifier fail-open value
    """

    HEAT_DOME = auto()
    COLD_SNAP = auto()
    NORMAL = auto()
    SHOULDER_SEASON = auto()
    SOURCE_ANOMALY = auto()
    UNKNOWN = auto()


def regime_tag_for(
    city: str,
    target_date: "date",
    decision_time: "datetime",
    conn: "sqlite3.Connection",
) -> WeatherRegimeTag:
    """Rule-based classifier: read observation history + forecast median → WeatherRegimeTag.

    Returns WeatherRegimeTag.UNKNOWN when observation history is insufficient
    for HEAT_DOME or COLD_SNAP classification (G2 invariant — fail-open, never
    silently default to NORMAL or any strong regime label).

    Classification priority (checked in order; first match wins):
      1. UNKNOWN      — observation count < _MIN_HISTORY_DAYS (G2 fail-open)
      2. SOURCE_ANOMALY — cross-source spread > _SOURCE_ANOMALY_Z_THRESHOLD σ on most recent obs
      3. HEAT_DOME    — rolling mean of recent high_temp > seasonal_mean + 2σ
      4. COLD_SNAP    — rolling mean of recent high_temp < seasonal_mean − 2σ
      5. SHOULDER_SEASON — target_date month in _SHOULDER_MONTHS
      6. NORMAL       — default when history is sufficient but no anomaly detected

    Threshold derivation:
      - _MIN_HISTORY_DAYS = 7: minimum window to distinguish sustained regime from
        transient noise; captures a full synoptic weather pattern cycle.
      - _SIGMA_THRESHOLD = 2.0: ~5% exceedance under normal distribution; rare but
        sustained temperature regimes that drive correlated shoulder-market risk.
      - _SOURCE_ANOMALY_Z_THRESHOLD = 3.0: separation implausible climatologically;
        consistent with Paris 2026 sensor spike (dossier §0.7).
      - _SHOULDER_MONTHS = {3,4,5,9,10,11}: spring (Mar-May) + autumn (Sep-Nov).

    K1 DB split: observations live on zeus-forecasts.db, accessible via
    forecasts.observations when world conn has forecasts ATTACHed. Missing
    tables (e.g., in-memory test connections) produce sqlite3.OperationalError
    which is caught and mapped to UNKNOWN.

    Args:
        city:          City name (canonical Zeus city string).
        target_date:   The settlement date of the shoulder market.
        decision_time: Wall-clock time of the decision (Chicago local).
        conn:          sqlite3 connection; forecasts schema may be ATTACHed.

    Returns:
        WeatherRegimeTag member appropriate to the observation + forecast context.
    """
    import datetime as _dt

    # Determine lookback window: last _MIN_HISTORY_DAYS days before target_date.
    lookback_start = target_date - _dt.timedelta(days=_MIN_HISTORY_DAYS)

    # Query via forecasts.observations (K1 ATTACH pattern).
    # Fall back to bare observations if forecasts schema is not attached.
    # On OperationalError (missing tables — e.g., :memory: test conn), return UNKNOWN.
    rows: list[tuple[float, str]] = []
    for table_ref in ("forecasts.observations", "observations"):
        try:
            cur = conn.execute(
                f"""
                SELECT high_temp, source
                FROM {table_ref}
                WHERE city = ?
                  AND target_date >= ?
                  AND target_date < ?
                  AND high_temp IS NOT NULL
                ORDER BY target_date
                """,
                (city, lookback_start.isoformat(), target_date.isoformat()),
            )
            rows = cur.fetchall()
            break  # succeeded on this table ref
        except sqlite3.OperationalError:
            continue

    # G2 invariant: fail-open to UNKNOWN when observation history is insufficient.
    if len(rows) < _MIN_HISTORY_DAYS:
        return WeatherRegimeTag.UNKNOWN

    high_temps = [r[0] for r in rows]
    sources = [r[1] for r in rows]

    # --- SOURCE_ANOMALY: cross-source spread check ---
    unique_sources = set(sources)
    if len(unique_sources) >= 2:
        # Group temps by source for the most recent available date slice.
        source_temps: dict[str, list[float]] = {}
        for temp, src in zip(high_temps, sources):
            source_temps.setdefault(src, []).append(temp)
        per_source_means = [statistics.mean(v) for v in source_temps.values() if v]
        if len(per_source_means) >= 2:
            spread = max(per_source_means) - min(per_source_means)
            # Use pooled std from all observations as denominator.
            try:
                pooled_std = statistics.stdev(high_temps)
            except statistics.StatisticsError:
                pooled_std = None
            if pooled_std and pooled_std > 0:
                z = spread / pooled_std
                if z > _SOURCE_ANOMALY_Z_THRESHOLD:
                    return WeatherRegimeTag.SOURCE_ANOMALY

    # --- HEAT_DOME / COLD_SNAP: rolling mean vs seasonal baseline ---
    try:
        mean_temp = statistics.mean(high_temps)
        std_temp = statistics.stdev(high_temps) if len(high_temps) > 1 else 0.0
    except statistics.StatisticsError:
        return WeatherRegimeTag.UNKNOWN

    # Compute seasonal baseline from a wider historical window if available.
    # Use the same observations table; look back 30 days for seasonal mean/std.
    seasonal_rows: list[float] = []
    seasonal_start = target_date - _dt.timedelta(days=30)
    for table_ref in ("forecasts.observations", "observations"):
        try:
            cur = conn.execute(
                f"""
                SELECT high_temp
                FROM {table_ref}
                WHERE city = ?
                  AND target_date >= ?
                  AND target_date < ?
                  AND high_temp IS NOT NULL
                ORDER BY target_date
                """,
                (city, seasonal_start.isoformat(), target_date.isoformat()),
            )
            seasonal_rows = [r[0] for r in cur.fetchall()]
            break
        except sqlite3.OperationalError:
            continue

    if len(seasonal_rows) >= _MIN_HISTORY_DAYS:
        try:
            seasonal_mean = statistics.mean(seasonal_rows)
            seasonal_std = statistics.stdev(seasonal_rows) if len(seasonal_rows) > 1 else 0.0
        except statistics.StatisticsError:
            seasonal_mean = mean_temp
            seasonal_std = std_temp
    else:
        # Insufficient seasonal history; use rolling window as self-reference.
        seasonal_mean = mean_temp
        seasonal_std = std_temp

    if seasonal_std > 0:
        z_score = (mean_temp - seasonal_mean) / seasonal_std
        if z_score >= _SIGMA_THRESHOLD:
            return WeatherRegimeTag.HEAT_DOME
        if z_score <= -_SIGMA_THRESHOLD:
            return WeatherRegimeTag.COLD_SNAP
    else:
        # Zero variance: all temps identical. Cannot classify as extreme regime.
        pass

    # --- SHOULDER_SEASON: transitional month check ---
    if target_date.month in _SHOULDER_MONTHS:
        return WeatherRegimeTag.SHOULDER_SEASON

    # --- NORMAL: sufficient history, no anomaly detected ---
    return WeatherRegimeTag.NORMAL
