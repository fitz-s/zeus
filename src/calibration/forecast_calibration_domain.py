# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: DESIGN_PHASE2_5_TRANSFER_POLICY_REPLACEMENT.md
#                  + may4math.md Finding 1+2 (full domain key required for
#                    calibration transfer; validate via OOS evidence not
#                    string mapping)
"""ForecastCalibrationDomain — the canonical shape of a calibration domain key.

Used by:
  * calibration_transfer_policy.evaluate_calibration_transfer (Phase 2.5)
  * Platt model bucket key (cycle/source_id/horizon_profile in
    platt_models_v2 + calibration_pairs_v2 — see Phase 2 schema migration)
  * evaluator: derive forecast domain from ens_result, compare against
    Platt model's domain via load_platt_model_v2
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_VALID_CYCLES_FOR_ENTRY_PRIMARY = frozenset({"00", "12"})
_VALID_HORIZON_PROFILES = frozenset({"full", "short"})
_ISO_HHMM_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})")


@dataclass(frozen=True)
class ForecastCalibrationDomain:
    """A 6-tuple identifying a calibration domain.

    Two forecasts share the same calibration domain iff all six fields
    match. Two domains can be calibration-transfer-equivalent if a row
    exists in validated_calibration_transfers with matching train/test
    domain pairs (and current OOS evidence).

    Field semantics:
        source_id: e.g., 'tigge_mars' (archive), 'ecmwf_open_data' (real-time public).
                   Distinguishes physical product (resolution, packaging, latency).
        cycle_hour_utc: '00' or '12' for entry-primary; 06/18 not in TIGGE.
        horizon_profile: 'full' (00z/12z, 240+ lead) | 'short' (06z/18z, ~120 lead).
        metric: 'high' | 'low' (max/min temperature).
        season: 'DJF' | 'MAM' | 'JJA' | 'SON' (local-time hemisphere-flipped).
        data_version: e.g., 'tigge_mx2t6_local_calendar_day_max_v1'.
                      Provenance string distinguishing track/version.

    Equality is structural — frozen dataclass auto-generates __eq__/__hash__.
    """

    source_id: str
    cycle_hour_utc: str
    horizon_profile: str
    metric: str
    season: str
    data_version: str

    def matches(self, other: "ForecastCalibrationDomain") -> bool:
        """Exact 6-field match."""
        return self == other

    def is_categorically_invalid(self) -> bool:
        """Quick check for hard-block conditions.

        Returns True when this domain CANNOT serve entry_primary regardless
        of validated_calibration_transfers state. Specifically:
        - cycle not in {'00','12'} for full-horizon entry path
        """
        if self.horizon_profile == "full" and self.cycle_hour_utc not in _VALID_CYCLES_FOR_ENTRY_PRIMARY:
            return True
        if self.horizon_profile not in _VALID_HORIZON_PROFILES:
            return True
        return False


def parse_cycle_from_issue_time(issue_time_iso: Optional[str]) -> Optional[str]:
    """Extract cycle_hour_utc from an ISO-8601 issue_time string.

    Returns the 2-character HH portion (e.g., '00','06','12','18') if the
    string is well-formed; None otherwise. Tolerates trailing timezone
    designators including 'Z' and '+HH:MM'.

    Example:
        parse_cycle_from_issue_time('2026-05-02T12:00:00+00:00')  # → '12'
        parse_cycle_from_issue_time('2026-05-02T00:00:00Z')       # → '00'
        parse_cycle_from_issue_time(None)                         # → None
        parse_cycle_from_issue_time('not-a-date')                 # → None
    """
    if not isinstance(issue_time_iso, str):
        return None
    match = _ISO_HHMM_RE.match(issue_time_iso)
    if match is None:
        return None
    hh = match.group(4)
    return hh


def derive_source_id_from_data_version(data_version: Optional[str]) -> Optional[str]:
    """Map a data_version string to its canonical source_id.

    Currently:
        'tigge_*'           → 'tigge_mars'
        'ecmwf_opendata_*'  → 'ecmwf_open_data'
        anything else       → None  (caller should reject as
                                     UNKNOWN_FORECAST_SOURCE_FAMILY)

    Conservative: returns None for unrecognized prefixes rather than
    guessing. Callers (evaluator) should treat None as a categorical
    rejection signal.
    """
    if not isinstance(data_version, str) or not data_version:
        return None
    if data_version.startswith("tigge_"):
        return "tigge_mars"
    if data_version.startswith("ecmwf_opendata_"):
        return "ecmwf_open_data"
    return None
