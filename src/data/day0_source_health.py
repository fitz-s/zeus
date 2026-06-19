# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #4) — a single source_health predicate
#   over EXISTING facts. Do NOT build a new source system; this only CLASSIFIES the state of the
#   already-persisted obs facts. Live Day0 entry accepts only the state set declared by the current
#   stage (positions may still use hard-fact exit independently).
"""day0_source_health — pure predicate classifying the Day0 observation source state.

Inputs are FACTS the caller assembles from existing systems (no fetch here):
  * city settlement_source_type (wu_icao | hko | noaa | cwa) and whether a METAR fast-obs source
    exists for it (fast_obs_source_for_city) — HKO / NOAA cities are NOT WU-ICAO METAR-native.
  * publication clock: latest authorized observation_available_at + fast cache age.
  * pause flags: WU/METAR divergence pause + oracle-anomaly pause.
  * the Day0CoverageProof (this module's sibling).
  * whether a WU settlement-history reading is present.

States (operator-declared):
  OK_FAST_AND_WU | OK_FAST_ONLY | OK_WU_ONLY | DEGRADED_FAST_STALE | DIVERGENCE_PAUSED
  | WINDOW_INCOMPLETE | UNSUPPORTED_SOURCE | UNKNOWN
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data.day0_coverage_proof import Day0CoverageProof

# Source types whose Day0 live obs is the METAR fast lane. HKO/NOAA/CWA settle off their own native
# sources and are NOT WU-ICAO METAR-native (operator correction: HK=HKO; Istanbul/Moscow/Tel-Aviv=NOAA).
METAR_NATIVE_SOURCE_TYPES = frozenset({"wu_icao"})

# Coverage statuses that are NOT full-through-decision.
_INCOMPLETE_COVERAGE = frozenset({"WINDOW_INCOMPLETE", "LOW_COVERAGE", "GAP_INCOMPLETE"})

# Default stage-admissible state set (initial live = the strongest states only).
DEFAULT_LIVE_ADMISSIBLE_STATES = frozenset({"OK_FAST_AND_WU", "OK_FAST_ONLY"})


@dataclass(frozen=True, slots=True)
class Day0SourceFacts:
    settlement_source_type: str
    fast_obs_supported: bool          # fast_obs_source_for_city(city) is not None
    fast_obs_fresh: bool              # fast cache age within the per-city staleness budget
    fast_obs_present: bool            # a recent authorized DAY0_EXTREME_UPDATED exists
    wu_present: bool                  # a WU settlement-history reading is available
    divergence_paused: bool           # WU/METAR divergence pause (config/wu_metar_divergence.json)
    anomaly_paused: bool              # oracle-anomaly pause (day0_oracle_anomaly)
    coverage_proof: Day0CoverageProof | None
    has_publication_clock: bool       # observation_available_at present on the latest obs


def day0_source_health(facts: Day0SourceFacts) -> str:
    """Classify the Day0 source state. Pure; precedence is first-match-wins (most-blocking first)."""
    # 0) cannot reason without a coverage proof or publication clock -> UNKNOWN.
    if facts.coverage_proof is None or not facts.has_publication_clock:
        return "UNKNOWN"

    # 1) the market's settlement source is not the METAR fast lane (HKO/NOAA/CWA) AND no WU reading
    #    -> nothing this lane can authorize.
    if facts.settlement_source_type not in METAR_NATIVE_SOURCE_TYPES and not facts.wu_present:
        return "UNSUPPORTED_SOURCE"
    if facts.settlement_source_type in METAR_NATIVE_SOURCE_TYPES and not facts.fast_obs_supported and not facts.wu_present:
        return "UNSUPPORTED_SOURCE"

    # 2) explicit pauses dominate any freshness/coverage.
    if facts.divergence_paused or facts.anomaly_paused:
        return "DIVERGENCE_PAUSED"

    # 3) coverage not full-through-decision.
    if facts.coverage_proof.status in _INCOMPLETE_COVERAGE:
        return "WINDOW_INCOMPLETE"

    # 4) fast lane is the obs source but it is stale.
    if facts.fast_obs_present and not facts.fast_obs_fresh:
        # if WU still backs it, downgrade to WU-only; else flag the stale fast lane.
        return "OK_WU_ONLY" if facts.wu_present else "DEGRADED_FAST_STALE"

    # 5) healthy combinations (coverage full + not paused + fresh).
    fast_ok = facts.fast_obs_supported and facts.fast_obs_present and facts.fast_obs_fresh
    if fast_ok and facts.wu_present:
        return "OK_FAST_AND_WU"
    if fast_ok:
        return "OK_FAST_ONLY"
    if facts.wu_present:
        return "OK_WU_ONLY"
    return "UNKNOWN"


def is_live_admissible(state: str, allowed_states: frozenset[str] = DEFAULT_LIVE_ADMISSIBLE_STATES) -> bool:
    """Whether `state` is in the current stage's admissible set. Stage owns the set; default = strongest."""
    return state in allowed_states
