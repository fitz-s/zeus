# Created: 2026-06-10
# Last reused or audited: 2026-07-29
# Authority basis: adversarial review /tmp/day0_adversarial_review.md MUST-FIX
#   #1 (hard-fact bin-death exit lane) + #3-wiring (resting-order cancel on bin
#   death) — operator requirement "新高出现时能否立即drop". Calibration artifact:
#   config/wu_metar_divergence.json (empirical METAR-vs-WU divergence).
"""Day0 HARD-FACT exit lane: absorbing-boundary bin death exits immediately.

The category split this module encodes (the panic-sell fix's missing half):

  ESTIMATOR FLIP (probability opinion moved)   -> maturity gate + CI-separation
                                                  evidence (panic-sell hardening,
                                                  monitor_refresh + portfolio —
                                                  UNCHANGED by this module).
  HARD FACT (running extreme crossed the bin's -> EXIT NOW, this lane. A measured
  survival edge — monotone, irreversible)         max cannot be un-seen; holding a
                                                  structurally dead bin donates the
                                                  remaining salvage value.

Verdicts (both directions, both metrics):
  - buy_yes on a DEAD bin (extreme passed beyond the far edge)      -> EXIT_DEAD_BIN
  - buy_no  on an ABSORBING SHOULDER the extreme entered            -> EXIT_DEAD_BIN
    (the extreme can never leave an open-ended shoulder: NO has structurally lost)
  - buy_no  on a DEAD bin                                           -> HOLD_STRUCTURAL_WIN
    (NO is a guaranteed winner; never sell it on a hard fact)
  - buy_yes on the shoulder the extreme entered                     -> HOLD_STRUCTURAL_WIN
  - finite bin merely CONTAINING the extreme                        -> None
    (not a hard fact for either side: a max can still leave upward / min downward;
     that is estimator territory and stays behind the maturity gate)

Held-position hard-fact authority combines current WU observations, durable WU
rows, and a durable same-station METAR publication monotonically.  The METAR
lane is admissible only when its raw publication ledger, source clocks,
configured station, empirical WU divergence margin, and live-authority Day0
event reproduce one exact running extreme.  A bare fast-tail scalar still has
no absorbing held-side probability or exit authority.
  An ACTIVE oracle-anomaly pause for the family disables the lane entirely
  (a suspect truth source must not drive an irreversible exit).

The lane is consumed by cycle_runtime's monitor loop (every exit-monitor cycle,
~2 min) BEFORE Position.evaluate_exit — it does not depend on fresh_prob, so the
buy_no day0 exit hole (no model authority at all) is closed for the hard-fact
class without touching the estimator-evidence machinery.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = timezone.utc

#: Throttle for the current official observation source. The source family is
#: part of the memo key: WU and HKO facts have different semantics and must not
#: share cached authority merely because city/date match.
_CURRENT_SOURCE_FETCH_INTERVAL_S = 600.0
_CURRENT_SOURCE_FAILURE_RETRY_S = 120.0
SAME_STATION_FAST_TAIL_SOURCE = "same_station_fast_tail"
COMBINED_WU_FAST_TAIL_SOURCE = f"wu_api+{SAME_STATION_FAST_TAIL_SOURCE}"
_CURRENT_SOURCE_MEMO: dict[
    tuple[str, str, str],
    tuple[
        float,
        Optional[float],
        Optional[float],
        "HardFactEvidence | None",
        "HardFactEvidence | None",
    ],
] = {}
_CURRENT_SOURCE_MEMO_LOCK = threading.Lock()
_RESTING_ENTRY_SCAN_CURSOR = 0
_RESTING_ENTRY_SCAN_CURSOR_LOCK = threading.Lock()


@dataclass(frozen=True)
class HardFactEvidence:
    """Persistable source proof for an absorbing Day0 probability.

    The identity is deliberately of the observed source payload, not of the
    forecast/posterior that preceded it.  A hard fact without every field below
    can still be diagnostic telemetry, but cannot authorise a held-side q.
    """

    source: str
    station_id: str
    observed_at: str
    issued_at: str
    raw_extreme: float
    rounded_extreme: float
    payload_identity: str
    source_identity: str
    contributor_payload_identities: tuple[str, ...] = ()

    def is_complete_for(self, city: Any) -> bool:
        expected_station = str(getattr(city, "wu_station", "") or "").strip().upper()
        station = str(self.station_id or "").strip().upper()
        try:
            finite_extrema = (
                math.isfinite(float(self.raw_extreme))
                and math.isfinite(float(self.rounded_extreme))
            )
        except (TypeError, ValueError):
            finite_extrema = False
        payload_identity = _strict_sha256_digest(self.payload_identity)
        contributor_identities = (
            self.contributor_payload_identities or (self.payload_identity,)
        )
        normalized_contributor_identities = tuple(
            _strict_sha256_digest(identity) for identity in contributor_identities
        )
        return bool(
            expected_station
            and station == expected_station
            and str(self.source or "").strip()
            and _is_aware_timestamp(self.observed_at)
            and _is_aware_timestamp(self.issued_at)
            and _timestamps_are_ordered(self.observed_at, self.issued_at)
            and payload_identity is not None
            and all(identity is not None for identity in normalized_contributor_identities)
            and payload_identity in normalized_contributor_identities
            and str(self.source_identity or "").strip()
            and finite_extrema
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "station_id": self.station_id,
            "observed_at": self.observed_at,
            "issued_at": self.issued_at,
            "raw_extreme": self.raw_extreme,
            "rounded_extreme": self.rounded_extreme,
            "payload_identity": self.payload_identity,
            "source_identity": self.source_identity,
            "contributor_payload_identities": list(
                self.contributor_payload_identities
            ),
        }


_SHA256_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


def _strict_sha256_digest(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _SHA256_DIGEST_RE.fullmatch(candidate) else None


def _provenance_payload_digest(value: Any) -> str | None:
    """Extract only canonical raw-payload SHA-256 provenance."""

    try:
        provenance = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(provenance, dict):
        return None
    payload_hash = provenance.get("payload_hash")
    if not isinstance(payload_hash, str):
        return None
    match = _PROVENANCE_SHA256_RE.fullmatch(payload_hash.strip())
    return match.group(1) if match else None


def _is_aware_timestamp(value: Any) -> bool:
    return _aware_datetime(value) is not None


def _aware_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _timestamps_are_ordered(observed_at: Any, issued_at: Any) -> bool:
    observed = _aware_datetime(observed_at)
    issued = _aware_datetime(issued_at)
    return observed is not None and issued is not None and observed <= issued


@dataclass(frozen=True)
class HardFactVerdict:
    action: str  # "EXIT_DEAD_BIN" | "HOLD_STRUCTURAL_WIN"
    reason: str
    metric: str
    rounded_extreme: float
    source: str  # source-family evidence labels, possibly + same_station_fast_tail
    evidence: HardFactEvidence | None = None


@dataclass(frozen=True)
class HardFactMonitorBelief:
    """Exact monitor belief derived from an absorbing Day0 hard fact."""

    held_side_prob: float
    yes_prob: float
    yes_verdict: str  # "YES_WON" | "YES_DEAD"
    held_verdict: str  # "STRUCTURAL_WIN" | "STRUCTURAL_LOSS"


@dataclass(frozen=True)
class DurableObservationExtremes:
    high_evidence: HardFactEvidence | None
    low_evidence: HardFactEvidence | None
    source: str
    row_count: int

    @property
    def high(self) -> Optional[float]:
        return None if self.high_evidence is None else self.high_evidence.rounded_extreme

    @property
    def low(self) -> Optional[float]:
        return None if self.low_evidence is None else self.low_evidence.rounded_extreme


@dataclass(frozen=True)
class FinalDailyObservation:
    """Source-correct, decision-time-causal final daily settlement evidence."""

    raw_extreme: float
    settled_extreme: float
    source: str
    station_id: str
    unit: str
    fetched_at: datetime


def _target_local_day_complete(
    city: Any,
    target_date: str,
    *,
    now: datetime,
) -> bool:
    """Whether the complete contract-local target day is in the past."""

    try:
        target = date.fromisoformat(str(target_date))
        local_day = now.astimezone(ZoneInfo(str(getattr(city, "timezone", "")))).date()
    except (TypeError, ValueError):
        return False
    return target < local_day


def _final_daily_source_matches(city: Any, source: str) -> bool:
    source = str(source or "").strip().lower()
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip().lower()
    if source_type == "hko":
        # The live market contract names finalized HKO Daily Extract data.
        # hko_realtime_api is sampled current-temperature accumulation, not
        # the final daily maximum/minimum product.
        return source == "hko_daily_api" or source.startswith("hko_daily_api_")
    # WU and NOAA/Ogimet rows require complete hourly coverage plus the first
    # following-day datapoint; a daily value alone is insufficient.
    return False


def _as_causal_utc(value: Any, *, now: datetime) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    parsed = parsed.astimezone(UTC)
    return parsed if parsed <= now.astimezone(UTC) else None


def _final_complete_hourly_observation_extreme(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    conn: Any,
) -> FinalDailyObservation | None:
    """Promote a complete settlement-family timeline after day advancement.

    WU and NOAA/Ogimet have no separate daily-final row in the canonical
    observation plane. The first causal observation of the following local day
    proves that the source has advanced past the target day; exact hourly
    coverage proves that no target-day interval was silently omitted. Both
    facts are required.
    """

    source_type = str(
        getattr(city, "settlement_source_type", "") or "wu_icao"
    ).strip().lower()
    station = str(getattr(city, "wu_station", "") or "").strip().upper()
    if source_type == "wu_icao":
        hourly_source = "wu_icao_history"
    elif source_type == "noaa" and station:
        hourly_source = f"ogimet_metar_{station.lower()}"
    else:
        return None
    try:
        target = date.fromisoformat(str(target_date))
        zone = ZoneInfo(str(getattr(city, "timezone", "") or ""))
        start = datetime.combine(target, datetime.min.time(), tzinfo=zone).astimezone(UTC)
        end = datetime.combine(
            target + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
    except (TypeError, ValueError):
        return None
    expected_hours = {
        start + timedelta(hours=offset)
        for offset in range(int((end - start).total_seconds() // 3600))
    }
    field = (
        "running_max"
        if metric == "high"
        else "running_min" if metric == "low" else ""
    )
    if not field or not expected_hours:
        return None
    unit = str(getattr(city, "settlement_unit", "") or "").strip().upper()
    if not station or not unit:
        return None

    required = {
        "city",
        "target_date",
        "source",
        "station_id",
        "utc_timestamp",
        "time_basis",
        field,
        "temp_unit",
        "imported_at",
        "authority",
        "causality_status",
        "source_role",
    }
    following_date = str(target + timedelta(days=1))
    for table_ref in (
        "world.observation_instants",
        "observation_instants",
        "forecasts.observation_instants",
    ):
        try:
            schema, separator, table = table_ref.partition(".")
            pragma = (
                f"PRAGMA {schema}.table_info({table})"
                if separator
                else f"PRAGMA table_info({schema})"
            )
            columns = {
                str(row[1])
                for row in conn.execute(pragma).fetchall()
            }
            if not required <= columns:
                continue
            rows = conn.execute(
                f"""
                SELECT target_date, source, station_id, utc_timestamp,
                       time_basis, {field} AS extreme, temp_unit, imported_at,
                       authority, causality_status, source_role
                 FROM {table_ref}
                 WHERE city = ?
                   AND target_date IN (?, ?)
                   AND source = ?
                   AND station_id = ?
                 ORDER BY utc_timestamp
                """,
                (
                    str(getattr(city, "name", "") or ""),
                    str(target_date),
                    following_date,
                    hourly_source,
                    station,
                ),
            ).fetchall()
        except Exception:  # noqa: BLE001 - absent attachment/schema fails closed
            continue

        target_values: dict[datetime, tuple[float, datetime]] = {}
        following_published_at: datetime | None = None
        for row in rows:
            try:
                row_date = str(row["target_date"] if hasattr(row, "keys") else row[0])
                row_source = str(row["source"] if hasattr(row, "keys") else row[1])
                row_station = str(
                    row["station_id"] if hasattr(row, "keys") else row[2]
                ).strip().upper()
                observed_at = _as_causal_utc(
                    row["utc_timestamp"] if hasattr(row, "keys") else row[3],
                    now=now,
                )
                time_basis = str(
                    row["time_basis"] if hasattr(row, "keys") else row[4]
                ).strip().lower()
                raw_extreme = row["extreme"] if hasattr(row, "keys") else row[5]
                row_unit = str(
                    row["temp_unit"] if hasattr(row, "keys") else row[6]
                ).strip().upper()
                imported_at = _as_causal_utc(
                    row["imported_at"] if hasattr(row, "keys") else row[7],
                    now=now,
                )
                authority = str(
                    row["authority"] if hasattr(row, "keys") else row[8]
                ).strip().upper()
                causality = str(
                    row["causality_status"] if hasattr(row, "keys") else row[9]
                ).strip().upper()
                source_role = str(
                    row["source_role"] if hasattr(row, "keys") else row[10]
                ).strip().lower()
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if (
                row_source != hourly_source
                or row_station != station
                or time_basis != "utc_hour_bucket_extremum"
                or row_unit != unit
                or authority != "VERIFIED"
                or causality != "OK"
                or source_role != "historical_hourly"
                or observed_at is None
                or imported_at is None
            ):
                continue
            if row_date == str(target_date) and observed_at in expected_hours:
                try:
                    target_values[observed_at] = (float(raw_extreme), imported_at)
                except (TypeError, ValueError):
                    continue
            elif row_date == following_date and observed_at == end:
                following_published_at = max(
                    following_published_at or imported_at,
                    imported_at,
                )
        if set(target_values) != expected_hours or following_published_at is None:
            continue
        try:
            values = [value for value, _ in target_values.values()]
            raw = max(values) if metric == "high" else min(values)
            from src.contracts.settlement_semantics import SettlementSemantics

            settled = SettlementSemantics.for_city(city).round_single(raw)
        except Exception:  # noqa: BLE001 - invalid semantics/value cannot authorize q
            continue
        fetched_at = max(
            following_published_at,
            *(imported for _, imported in target_values.values()),
        )
        return FinalDailyObservation(
            raw_extreme=float(raw),
            settled_extreme=float(settled),
            source=f"{hourly_source}:following_day_observed",
            station_id=station,
            unit=unit,
            fetched_at=fetched_at,
        )
    return None


def _final_daily_observation_extreme(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    conn: Any,
) -> FinalDailyObservation | None:
    """Read source-correct final daily settlement evidence after local day end.

    Daily observations are a separate truth plane from Day0 hourly/current
    observations. Only VERIFIED rows from the configured settlement family may
    collapse the held-side probability to an exact outcome.
    """

    if conn is None or not _target_local_day_complete(city, target_date, now=now):
        return None
    metric = str(metric or "").strip().lower()
    field = "high_temp" if metric == "high" else "low_temp" if metric == "low" else ""
    if not field:
        return None
    expected_unit = str(getattr(city, "settlement_unit", "") or "").strip().upper()
    expected_station = (
        "HKO"
        if str(getattr(city, "settlement_source_type", "") or "").strip().lower() == "hko"
        else str(getattr(city, "wu_station", "") or "").strip().upper()
    )
    for table_ref in ("forecasts.observations", "observations"):
        try:
            rows = conn.execute(
                f"""
                SELECT source, station_id, authority, unit, {field} AS extreme,
                       fetched_at
                  FROM {table_ref}
                 WHERE city = ?
                   AND target_date = ?
                   AND {field} IS NOT NULL
                 ORDER BY fetched_at DESC
                """,
                (str(getattr(city, "name", "") or ""), str(target_date)),
            ).fetchall()
        except Exception:  # noqa: BLE001 - absent attachment/schema fails closed
            continue
        for row in rows:
            try:
                source = row["source"] if hasattr(row, "keys") else row[0]
                station = row["station_id"] if hasattr(row, "keys") else row[1]
                authority = row["authority"] if hasattr(row, "keys") else row[2]
                unit = row["unit"] if hasattr(row, "keys") else row[3]
                extreme = row["extreme"] if hasattr(row, "keys") else row[4]
                fetched_at_raw = row["fetched_at"] if hasattr(row, "keys") else row[5]
            except (KeyError, IndexError, TypeError):
                continue
            if not _final_daily_source_matches(city, source):
                continue
            if str(authority or "").strip().upper() != "VERIFIED":
                continue
            if expected_unit and str(unit or "").strip().upper() != expected_unit:
                continue
            station_norm = str(station or "").strip().upper()
            if expected_station and station_norm not in {
                expected_station,
            } and not station_norm.startswith(f"{expected_station}:"):
                continue
            try:
                fetched_at = datetime.fromisoformat(
                    str(fetched_at_raw or "").replace("Z", "+00:00")
                )
                if fetched_at.tzinfo is None:
                    continue
                fetched_at = fetched_at.astimezone(UTC)
                if fetched_at > now.astimezone(UTC):
                    continue
                from src.contracts.settlement_semantics import SettlementSemantics

                raw_extreme = float(extreme)
                settled_grid = SettlementSemantics.for_city(city).round_single(raw_extreme)
            except Exception:  # noqa: BLE001 - invalid semantics/value cannot authorize q
                continue
            return FinalDailyObservation(
                raw_extreme=raw_extreme,
                settled_extreme=float(settled_grid),
                source=str(source),
                station_id=station_norm,
                unit=str(unit).strip().upper(),
                fetched_at=fetched_at,
            )
    return _final_complete_hourly_observation_extreme(
        city=city,
        target_date=target_date,
        metric=metric,
        now=now,
        conn=conn,
    )


def _normalize_direction(direction: Any) -> str:
    value = str(getattr(direction, "value", direction) or "").strip().lower()
    return f"buy_{value}" if value in {"yes", "no"} else value


def hard_fact_bin_verdict(
    *,
    metric: str,
    direction: str,
    bin_low: Optional[float],
    bin_high: Optional[float],
    effective_extreme: float,
) -> Optional[HardFactVerdict]:
    """Pure absorbing-boundary verdict for one held bin against a settlement-grade
    extreme (already margin-adjusted by the caller). None = no hard fact."""
    metric = str(getattr(metric, "value", metric) or "").strip().lower()
    direction = _normalize_direction(direction)
    if metric not in {"high", "low"} or direction not in {"buy_yes", "buy_no"}:
        return None
    if bin_low is None and bin_high is None:
        return None

    def _verdict(action: str, reason: str) -> HardFactVerdict:
        return HardFactVerdict(
            action=action, reason=reason, metric=metric,
            rounded_extreme=float(effective_extreme), source="",
        )

    if metric == "high":
        dead = bin_high is not None and effective_extreme > float(bin_high)
        shoulder_entered = (
            bin_high is None and bin_low is not None and effective_extreme >= float(bin_low)
        )
    else:  # low
        dead = bin_low is not None and effective_extreme < float(bin_low)
        shoulder_entered = (
            bin_low is None and bin_high is not None and effective_extreme <= float(bin_high)
        )

    if dead:
        if direction == "buy_yes":
            return _verdict(
                "EXIT_DEAD_BIN",
                f"running {metric} extreme {effective_extreme} beyond bin "
                f"[{bin_low},{bin_high}] — YES structurally dead",
            )
        return _verdict(
            "HOLD_STRUCTURAL_WIN",
            f"running {metric} extreme {effective_extreme} killed bin "
            f"[{bin_low},{bin_high}] — NO structurally won; hold to settlement",
        )
    if shoulder_entered:
        if direction == "buy_no":
            return _verdict(
                "EXIT_DEAD_BIN",
                f"running {metric} extreme {effective_extreme} entered absorbing "
                f"shoulder [{bin_low},{bin_high}] — NO structurally dead",
            )
        return _verdict(
            "HOLD_STRUCTURAL_WIN",
            f"running {metric} extreme {effective_extreme} entered absorbing "
            f"shoulder [{bin_low},{bin_high}] — YES structurally won",
        )
    return None


def final_observed_bin_verdict(
    *,
    metric: str,
    direction: str,
    bin_low: Optional[float],
    bin_high: Optional[float],
    final_extreme: float,
) -> Optional[HardFactVerdict]:
    """Pure final-day settlement-grid verdict once the local day is complete.

    Intraday, a finite bin merely containing the running extreme is not
    absorbing: a max can still leave upward and a min can still leave downward.
    After the local target day is complete and durable WU rows cover the end of
    that day, the final extreme is settlement-grade enough to decide whether
    YES won the bin. This is the missing complement to ``hard_fact_bin_verdict``.
    """

    metric = str(getattr(metric, "value", metric) or "").strip().lower()
    direction = _normalize_direction(direction)
    if metric not in {"high", "low"} or direction not in {"buy_yes", "buy_no"}:
        return None
    if bin_low is None and bin_high is None:
        return None

    yes_won = True
    if bin_low is not None and final_extreme < float(bin_low):
        yes_won = False
    if bin_high is not None and final_extreme > float(bin_high):
        yes_won = False

    if yes_won:
        reason = (
            f"final {metric} extreme {final_extreme} resolved inside bin "
            f"[{bin_low},{bin_high}] — YES won"
        )
        action = "HOLD_STRUCTURAL_WIN" if direction == "buy_yes" else "EXIT_DEAD_BIN"
    else:
        reason = (
            f"final {metric} extreme {final_extreme} resolved outside bin "
            f"[{bin_low},{bin_high}] — YES dead"
        )
        action = "EXIT_DEAD_BIN" if direction == "buy_yes" else "HOLD_STRUCTURAL_WIN"
    return HardFactVerdict(
        action=action,
        reason=reason,
        metric=metric,
        rounded_extreme=float(final_extreme),
        source="",
    )


def hard_fact_monitor_belief(
    *, verdict: HardFactVerdict, direction: Any
) -> Optional[HardFactMonitorBelief]:
    """Convert a hard-fact action into exact YES and held-side probabilities."""

    direction = _normalize_direction(direction)
    action = str(getattr(verdict, "action", "") or "")
    if direction == "buy_yes" and action == "EXIT_DEAD_BIN":
        return HardFactMonitorBelief(
            held_side_prob=0.0,
            yes_prob=0.0,
            yes_verdict="YES_DEAD",
            held_verdict="STRUCTURAL_LOSS",
        )
    if direction == "buy_no" and action == "HOLD_STRUCTURAL_WIN":
        return HardFactMonitorBelief(
            held_side_prob=1.0,
            yes_prob=0.0,
            yes_verdict="YES_DEAD",
            held_verdict="STRUCTURAL_WIN",
        )
    if direction == "buy_yes" and action == "HOLD_STRUCTURAL_WIN":
        return HardFactMonitorBelief(
            held_side_prob=1.0,
            yes_prob=1.0,
            yes_verdict="YES_WON",
            held_verdict="STRUCTURAL_WIN",
        )
    if direction == "buy_no" and action == "EXIT_DEAD_BIN":
        return HardFactMonitorBelief(
            held_side_prob=0.0,
            yes_prob=1.0,
            yes_verdict="YES_WON",
            held_verdict="STRUCTURAL_LOSS",
        )
    return None


def _metar_kill_margin_units(city_name: str, unit: str) -> Optional[float]:
    """Whole-unit margin a METAR-sourced extreme must exceed beyond the bin edge
    before its crossing counts as a HARD fact.

    Derived from the measured calibration artifact (operator rule: 'boundary
    crossing measured beyond the empirical divergence threshold + rounding
    quantum'): the integer-grid strict crossing already consumes one full
    rounding quantum, and the divergence allowance is the measured p99 —
    0 for cities where the feeds are byte-identical post-rounding (threshold
    1.0), `threshold` extra whole units for unmeasured/spread cities.

    2026-07-16 (day0 defect-5): delegates to the shared lookup
    (day0_oracle_anomaly.metar_margin_units_for_city) so this and the
    emission layer (day0_fast_obs.fast_obs_source_for_city) use ONE margin
    mechanism. A measured-but-not-settlement-faithful city with an adequate
    sample (Seoul/RKSI class) now gets a margin here too instead of None —
    it used to be unreachable for such a city (the emission layer already
    excluded it before this function was ever called), which was the same
    "margin machinery exists but the boolean gate never lets it run" defect
    as the two callers being reconciled. Returns None only when METAR must
    not drive kills at all (thin/absent divergence measurement).
    """
    from src.data.day0_oracle_anomaly import metar_margin_units_for_city

    return metar_margin_units_for_city(city_name, unit)


def _current_source_rounded_extremes(
    city: Any,
    target_date: str,
    *,
    now: datetime,
    source_family: str,
    accepted_sources: Collection[str],
    observation_getter: Any,
) -> tuple[Optional[float], Optional[float]]:
    """Read and round extrema only when the observation matches its source family."""
    city_name = str(getattr(city, "name", ""))
    key = (source_family, city_name, str(target_date))
    monotonic_now = time.monotonic()
    with _CURRENT_SOURCE_MEMO_LOCK:
        cached = _CURRENT_SOURCE_MEMO.get(key)
        if cached is not None:
            retry_after = (
                _CURRENT_SOURCE_FETCH_INTERVAL_S
                if cached[1] is not None or cached[2] is not None
                else _CURRENT_SOURCE_FAILURE_RETRY_S
            )
            if monotonic_now - cached[0] < retry_after:
                return cached[1], cached[2]
    high = low = None
    high_evidence = low_evidence = None
    try:
        from src.contracts.settlement_semantics import SettlementSemantics

        obs = observation_getter(city, target_date=target_date, reference_time=now)
        observed_source = str(getattr(obs, "source", "") or "").strip().lower()
        allowed = {str(source).strip().lower() for source in accepted_sources}
        if observed_source not in allowed:
            raise ValueError(
                f"{source_family} observation source mismatch: {observed_source or '<missing>'}"
            )
        semantics = SettlementSemantics.for_city(city)
        raw_high = getattr(obs, "high_so_far", None)
        raw_low = getattr(obs, "low_so_far", None)
        observed_at = str(getattr(obs, "observation_time", "") or "").strip()
        issued_at = str(
            getattr(obs, "provider_reported_time", None)
            or getattr(obs, "observation_available_at", "")
            or ""
        ).strip()
        station_id = str(getattr(obs, "station_id", "") or "").strip().upper()
        payload_identity = _strict_sha256_digest(
            getattr(obs, "raw_payload_hash", "")
        )

        def _evidence(raw_value: Any, rounded_value: float) -> HardFactEvidence:
            return HardFactEvidence(
                source=observed_source,
                station_id=station_id,
                observed_at=observed_at,
                issued_at=issued_at,
                raw_extreme=float(raw_value),
                rounded_extreme=rounded_value,
                payload_identity=payload_identity or "",
                source_identity=f"{observed_source}:{station_id}",
                contributor_payload_identities=(
                    (payload_identity,) if payload_identity is not None else ()
                ),
            )

        if raw_high is not None:
            high = float(semantics.round_single(float(raw_high)))
            high_evidence = _evidence(raw_high, high)
        if raw_low is not None:
            low = float(semantics.round_single(float(raw_low)))
            low_evidence = _evidence(raw_low, low)
    except Exception as exc:  # noqa: BLE001 — source fail-soft, lane holds
        logger.debug(
            "day0 hard-fact %s source unavailable for %s/%s: %s",
            source_family,
            city_name,
            target_date,
            exc,
        )
    with _CURRENT_SOURCE_MEMO_LOCK:
        _CURRENT_SOURCE_MEMO[key] = (
            monotonic_now,
            high,
            low,
            high_evidence,
            low_evidence,
        )
    return high, low


def _current_source_hard_fact_evidence(
    *, city: Any, target_date: str, metric: str
) -> HardFactEvidence | None:
    """Return the typed proof retained alongside the current WU scalar memo."""

    key = ("wu", str(getattr(city, "name", "")), str(target_date))
    with _CURRENT_SOURCE_MEMO_LOCK:
        cached = _CURRENT_SOURCE_MEMO.get(key)
    if cached is None:
        return None
    return cached[3] if metric == "high" else cached[4]


def _wu_rounded_extremes(
    city: Any, target_date: str, *, now: datetime
) -> tuple[Optional[float], Optional[float]]:
    """Rounded WU bucket extrema for a WU-settled contract."""
    from src.data.observation_client import get_live_wu_observation

    return _current_source_rounded_extremes(
        city,
        target_date,
        now=now,
        source_family="wu",
        accepted_sources=("wu_api",),
        observation_getter=get_live_wu_observation,
    )


def _hko_rounded_extremes(
    city: Any, target_date: str, *, now: datetime
) -> tuple[Optional[float], Optional[float]]:
    """Rounded latest official HKO cumulative extrema for an HKO contract."""
    from src.data.observation_client import get_current_observation

    return _current_source_rounded_extremes(
        city,
        target_date,
        now=now,
        source_family="hko",
        accepted_sources=("hko_hourly_accumulator",),
        observation_getter=get_current_observation,
    )


def _metar_rounded_extreme(
    city_name: str, target_date: str, metric: str, *, world_conn: Any = None
) -> Optional[float]:
    """Settlement-grade rounded extreme from the fast METAR lane's emit memo
    (values there passed the LIVE_AUTHORITY hard-fact statuses at emission).

    ``world_conn`` is threaded from the caller's composite connection so the
    kill-memo restart-recovery path does not open an independent world connection.
    When None (non-composite callers), recovery is skipped for this call — the
    in-process memo is used when warm, or None is returned when cold.
    """
    try:
        from src.data.day0_fast_obs import get_fast_obs_emitter

        return get_fast_obs_emitter().latest_rounded_extreme(
            city_name, target_date, metric, world_conn=world_conn
        )
    except Exception:  # noqa: BLE001
        return None


def _durable_observation_instants_summary(
    *,
    city: Any,
    target_date: str,
    now: datetime,
    world_conn: Any = None,
) -> DurableObservationExtremes | None:
    """Verified durable WU-hourly extrema for the local target date.

    This is the restart-safe side of the hard-fact lane. WU live API and METAR
    memo are useful when warm, but monitor decisions must also consume verified
    rows already written to the canonical observation surface. LOW uses the
    monotone minimum over the local target date; HIGH uses the monotone maximum.
    """

    if world_conn is None:
        return None
    city_name = str(getattr(city, "name", "") or "")
    if not city_name or not target_date:
        return None

    station_id = str(getattr(city, "wu_station", "") or "").strip().upper()
    if not station_id:
        return None
    metric_filter = ("", "high", "low")
    now_iso = now.astimezone(UTC).isoformat()
    table_refs = (
        "world.observation_instants",
        "observation_instants",
        "forecasts.observation_instants",
    )
    for table_ref in table_refs:
        try:
            rows = world_conn.execute(
                f"""
                SELECT
                    CAST(running_max AS REAL) AS high_raw,
                    CAST(running_min AS REAL) AS low_raw,
                    source,
                    station_id,
                    utc_timestamp,
                    imported_at,
                    provenance_json
                FROM {table_ref}
                WHERE city = ?
                  AND target_date = ?
                  AND substr(local_timestamp, 1, 10) = target_date
                  AND utc_timestamp <= ?
                  AND UPPER(COALESCE(authority, '')) = 'VERIFIED'
                  AND COALESCE(causality_status, 'OK') = 'OK'
                  AND LOWER(COALESCE(source, '')) LIKE 'wu%'
                  AND UPPER(COALESCE(station_id, '')) = ?
                  AND LOWER(COALESCE(temperature_metric, '')) IN (?, ?, ?)
                """,
                (city_name, target_date, now_iso, station_id, *metric_filter),
            ).fetchall()
        except Exception:  # noqa: BLE001 - missing attachment/table/columns fail soft
            continue
        if not rows:
            continue
        # M-8 (audit 2026-07-18): settlement-round the durable extremes before any
        # grid comparison — the WU/METAR sibling paths round, and the market settles
        # on the rounded integer. A raw 26.4 vs a 26 bin must read as 26 (inside,
        # winnable), never as beyond-the-edge dead.
        try:
            from src.contracts.settlement_semantics import SettlementSemantics

            _round = SettlementSemantics.for_city(city).round_single
        except Exception:  # noqa: BLE001 — semantics unavailable: raw value fail-soft
            def _round(value: float) -> float:
                return value
        def _row_value(row: Any, key: str, index: int) -> Any:
            return row[key] if hasattr(row, "keys") else row[index]

        def _evidence(row: Any, *, raw_value: float, rounded_value: float) -> HardFactEvidence:
            source = str(_row_value(row, "source", 2) or "").strip()
            row_station = str(_row_value(row, "station_id", 3) or "").strip().upper()
            observed_at = str(_row_value(row, "utc_timestamp", 4) or "").strip()
            issued_at = str(_row_value(row, "imported_at", 5) or "").strip()
            payload_identity = _provenance_payload_digest(
                _row_value(row, "provenance_json", 6)
            )
            return HardFactEvidence(
                source=source,
                station_id=row_station,
                observed_at=observed_at,
                issued_at=issued_at,
                raw_extreme=raw_value,
                rounded_extreme=rounded_value,
                payload_identity=payload_identity or "",
                source_identity=f"{source}:{row_station}",
                contributor_payload_identities=(
                    (payload_identity,) if payload_identity is not None else ()
                ),
            )

        high_candidates: list[tuple[float, HardFactEvidence]] = []
        low_candidates: list[tuple[float, HardFactEvidence]] = []
        for row in rows:
            try:
                high_raw = _row_value(row, "high_raw", 0)
                low_raw = _row_value(row, "low_raw", 1)
                if high_raw is not None and math.isfinite(float(high_raw)):
                    raw_high = float(high_raw)
                    high_evidence = _evidence(
                        row,
                        raw_value=raw_high,
                        rounded_value=float(_round(raw_high)),
                    )
                    issued = _aware_datetime(high_evidence.issued_at)
                    if (
                        high_evidence.is_complete_for(city)
                        and issued is not None
                        and issued <= now
                    ):
                        high_candidates.append((raw_high, high_evidence))
                if low_raw is not None and math.isfinite(float(low_raw)):
                    raw_low = float(low_raw)
                    low_evidence = _evidence(
                        row,
                        raw_value=raw_low,
                        rounded_value=float(_round(raw_low)),
                    )
                    issued = _aware_datetime(low_evidence.issued_at)
                    if (
                        low_evidence.is_complete_for(city)
                        and issued is not None
                        and issued <= now
                    ):
                        low_candidates.append((raw_low, low_evidence))
            except (TypeError, KeyError, IndexError, ValueError):
                continue
        if not high_candidates and not low_candidates:
            continue
        high_evidence = None
        low_evidence = None
        if high_candidates:
            _, high_evidence = max(high_candidates, key=lambda item: item[0])
        if low_candidates:
            _, low_evidence = min(low_candidates, key=lambda item: item[0])
        return DurableObservationExtremes(
            high_evidence=high_evidence,
            low_evidence=low_evidence,
            source="durable_observation_instants",
            row_count=len(rows),
        )
    return None


def _durable_observation_instants_extremes(
    *,
    city: Any,
    target_date: str,
    now: datetime,
    world_conn: Any = None,
) -> tuple[Optional[float], Optional[float], str]:
    summary = _durable_observation_instants_summary(
        city=city,
        target_date=target_date,
        now=now,
        world_conn=world_conn,
    )
    if summary is None:
        return None, None, ""
    return summary.high, summary.low, summary.source


def _durable_fast_tail_hard_fact_evidence(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    world_conn: Any,
) -> HardFactEvidence | None:
    """Reproduce one authorized same-station METAR monotone-bound event.

    ``observation_prints`` supplies the immutable raw publication and clocks;
    ``DAY0_EXTREME_UPDATED`` supplies the already-gated city/date/metric/unit
    interpretation.  Neither surface is sufficient alone.  The existing
    per-city divergence margin is replayed over the raw reports, so this path
    cannot turn an unmeasured station or an unbound scalar memo into an exit.
    """

    if world_conn is None or metric not in {"high", "low"}:
        return None
    if (
        str(getattr(city, "settlement_source_type", "") or "").strip().lower()
        != "wu_icao"
    ):
        return None
    city_name = str(getattr(city, "name", "") or "").strip()
    station = str(getattr(city, "wu_station", "") or "").strip().upper()
    unit = str(getattr(city, "settlement_unit", "") or "").strip().upper()
    if not city_name or not station or unit not in {"C", "F"}:
        return None

    try:
        from src.contracts.settlement_semantics import SettlementSemantics
        from src.data.day0_fast_obs import (
            FAST_OBS_SOURCE_ID,
            MetarReport,
            fast_obs_source_for_city,
            metar_observation_time_from_raw,
            running_extremes_for_local_day,
        )

        source = fast_obs_source_for_city(city)
        if source is None or source.source_id != FAST_OBS_SOURCE_ID:
            return None
        margin = _metar_kill_margin_units(city_name, unit)
        if margin is None or not math.isfinite(float(margin)) or margin < 0.0:
            return None
        target = date.fromisoformat(str(target_date)[:10])
        zone = ZoneInfo(str(getattr(city, "timezone", "") or ""))
        day_start = datetime.combine(target, datetime.min.time(), tzinfo=zone).astimezone(UTC)
        day_end = datetime.combine(
            target + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
    except Exception:
        return None

    print_rows = None
    for table_ref in ("world.observation_prints", "observation_prints"):
        try:
            print_rows = world_conn.execute(
                f"""
                SELECT publish_ts_utc, value_native, unit, station_id,
                       raw_report, fetched_at_utc
                  FROM {table_ref}
                 WHERE city = ?
                   AND upper(station_id) = ?
                   AND source_channel = ?
                   AND publish_ts_utc >= ?
                   AND publish_ts_utc < ?
                   AND publish_ts_utc <= ?
                   AND fetched_at_utc <= ?
                 ORDER BY publish_ts_utc, id
                """,
                (
                    city_name,
                    station,
                    source.source_id,
                    day_start.isoformat(),
                    day_end.isoformat(),
                    now.astimezone(UTC).isoformat(),
                    now.astimezone(UTC).isoformat(),
                ),
            ).fetchall()
            break
        except Exception:  # noqa: BLE001 - absent attachment/schema fails closed
            print_rows = None
    if not print_rows:
        return None

    reports = []
    contributor_digests: list[str] = []
    for row in print_rows:
        try:
            publish_raw = row["publish_ts_utc"] if hasattr(row, "keys") else row[0]
            value_raw = row["value_native"] if hasattr(row, "keys") else row[1]
            row_unit = row["unit"] if hasattr(row, "keys") else row[2]
            row_station = row["station_id"] if hasattr(row, "keys") else row[3]
            raw_report = row["raw_report"] if hasattr(row, "keys") else row[4]
            fetched_raw = row["fetched_at_utc"] if hasattr(row, "keys") else row[5]
            published = _aware_datetime(publish_raw)
            fetched = _aware_datetime(fetched_raw)
            if (
                published is None
                or fetched is None
                or published > now
                or fetched > now
                or str(row_station or "").strip().upper() != station
                or str(row_unit or "").strip().upper() != "C"
            ):
                continue
            raw_report = str(raw_report or "").strip()
            observed = metar_observation_time_from_raw(
                raw_report, published_at=published
            )
            if observed is None or observed > published or observed > now:
                continue
            reports.append(
                MetarReport(
                    station_id=station,
                    obs_time=observed.astimezone(UTC),
                    receipt_time=published.astimezone(UTC),
                    temp_c=float(value_raw),
                    metar_type="METAR",
                    raw=raw_report,
                )
            )
            contributor_digests.append(
                hashlib.sha256(
                    json.dumps(
                        {
                            "fetched_at": fetched.astimezone(UTC).isoformat(),
                            "published_at": published.astimezone(UTC).isoformat(),
                            "raw_report": raw_report,
                            "station_id": station,
                            "value_c": float(value_raw),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            )
        except Exception:  # noqa: BLE001 - one malformed print proves nothing
            continue
    if not reports:
        return None

    try:
        extremes = running_extremes_for_local_day(
            reports,
            city=city,
            target_date=target,
            as_of=now,
            margin_units=float(margin),
        )
        replayed_raw = (
            extremes.high_so_far if metric == "high" else extremes.low_so_far
        )
        if replayed_raw is None or extremes.last_obs_time is None:
            return None
        replayed_rounded = float(
            SettlementSemantics.for_city(city).round_single(float(replayed_raw))
        )
    except Exception:
        return None

    event_row = None
    for table_ref in ("world.opportunity_events", "opportunity_events"):
        try:
            event_row = world_conn.execute(
                f"""
                SELECT event_id, payload_json, observed_at, available_at,
                       received_at
                  FROM {table_ref}
                 WHERE event_type = 'DAY0_EXTREME_UPDATED'
                   AND json_extract(payload_json, '$.city') = ?
                   AND json_extract(payload_json, '$.target_date') = ?
                   AND json_extract(payload_json, '$.metric') = ?
                   AND json_extract(payload_json, '$.settlement_source') = ?
                   AND json_extract(payload_json, '$.station_id') = ?
                   AND json_extract(payload_json, '$.live_authority_status') = 'live'
                   AND available_at <= ?
                   AND received_at <= ?
                 ORDER BY available_at DESC, received_at DESC
                 LIMIT 1
                """,
                (
                    city_name,
                    target.isoformat(),
                    metric,
                    source.source_id,
                    station,
                    now.astimezone(UTC).isoformat(),
                    now.astimezone(UTC).isoformat(),
                ),
            ).fetchone()
            break
        except Exception:  # noqa: BLE001 - absent attachment/schema fails closed
            event_row = None
    if event_row is None:
        return None
    try:
        event_id = str(event_row["event_id"] if hasattr(event_row, "keys") else event_row[0])
        payload = json.loads(
            str(event_row["payload_json"] if hasattr(event_row, "keys") else event_row[1])
        )
        observed_at = str(payload.get("observation_time") or "")
        issued_at = str(payload.get("observation_available_at") or "")
        row_observed_at = str(
            event_row["observed_at"] if hasattr(event_row, "keys") else event_row[2]
        )
        row_available_at = str(
            event_row["available_at"] if hasattr(event_row, "keys") else event_row[3]
        )
        row_received_at = str(
            event_row["received_at"] if hasattr(event_row, "keys") else event_row[4]
        )
        if (
            payload.get("settlement_source") != source.source_id
            or payload.get("settlement_source_type") != "wu_icao"
            or str(payload.get("station_id") or "").strip().upper() != station
            or payload.get("source_authorized_status") != "AUTHORIZED"
            or payload.get("source_match_status") != "MATCH"
            or payload.get("station_match_status") != "MATCH"
            or payload.get("local_date_status") != "MATCH"
            or payload.get("dst_status") != "UNAMBIGUOUS"
            or payload.get("metric_match_status") != "MATCH"
            or payload.get("rounding_status") != "MATCH"
            or payload.get("live_authority_status") != "live"
            or payload.get("evidence_finality") != "MONOTONE_SETTLEMENT_BOUND"
            or abs(float(payload.get("metar_margin_units_applied")) - float(margin)) > 1e-9
            or abs(float(payload.get("raw_value")) - float(replayed_raw)) > 1e-9
            or abs(float(payload.get("rounded_value")) - replayed_rounded) > 1e-9
            or not _timestamps_are_ordered(observed_at, issued_at)
            or _aware_datetime(observed_at) != _aware_datetime(row_observed_at)
            or _aware_datetime(issued_at) != _aware_datetime(row_available_at)
            or not _timestamps_are_ordered(row_available_at, row_received_at)
            or _aware_datetime(row_received_at) > now
            or _aware_datetime(issued_at) > now
        ):
            return None
    except Exception:
        return None

    contributors = tuple(dict.fromkeys(contributor_digests))
    payload_identity = hashlib.sha256(
        json.dumps(
            {
                "event_id": event_id,
                "margin_units": float(margin),
                "metric": metric,
                "publication_payloads": contributors,
                "rounded_extreme": replayed_rounded,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return HardFactEvidence(
        source=f"{source.source_id}:durable_monotone_bound",
        station_id=station,
        observed_at=observed_at,
        issued_at=issued_at,
        raw_extreme=float(replayed_raw),
        rounded_extreme=replayed_rounded,
        payload_identity=payload_identity,
        source_identity=f"{source.source_id}:{station}:margin={float(margin):g}",
        # ``payload_identity`` commits the complete canonical contributor set;
        # retain that fixed-size root rather than copying a whole target day's
        # report hashes into every monitor event.
        contributor_payload_identities=(payload_identity,),
    )


def _combined_wu_hard_fact_evidence(
    evidence: Collection[HardFactEvidence], *, metric: str, city: Any
) -> HardFactEvidence | None:
    """Monotonically combine direct and durable WU facts from one station."""

    complete = [item for item in evidence if item.is_complete_for(city)]
    if not complete:
        return None
    selected = (
        max(complete, key=lambda item: item.rounded_extreme)
        if metric == "high"
        else min(complete, key=lambda item: item.rounded_extreme)
    )
    sources = tuple(dict.fromkeys(item.source for item in complete))
    contributor_payload_identities = tuple(
        dict.fromkeys(
            digest
            for item in complete
            for digest in (
                item.contributor_payload_identities or (item.payload_identity,)
            )
        )
    )
    source_identity_payload = {
        "contributor_payload_identities": contributor_payload_identities,
        "metric": metric,
        "source_identities": tuple(item.source_identity for item in complete),
    }
    return HardFactEvidence(
        source="+".join(sources),
        station_id=selected.station_id,
        observed_at=selected.observed_at,
        issued_at=selected.issued_at,
        raw_extreme=selected.raw_extreme,
        rounded_extreme=selected.rounded_extreme,
        payload_identity=selected.payload_identity,
        source_identity="wu-hard-fact:"
        + hashlib.sha256(
            json.dumps(
                source_identity_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        contributor_payload_identities=contributor_payload_identities,
    )


def _wu_hard_fact_evidence(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    world_conn: Any,
    durable_only: bool,
) -> HardFactEvidence | None:
    """Return the typed evidence classes that may drive held Day0 hard facts."""

    candidates: list[HardFactEvidence] = []
    if not durable_only:
        try:
            _wu_rounded_extremes(city, target_date, now=now)
        except Exception as exc:  # noqa: BLE001 - durable evidence remains usable
            logger.debug(
                "day0 hard-fact direct WU unavailable for %s/%s: %s",
                getattr(city, "name", "?"), target_date, exc,
            )
        direct = _current_source_hard_fact_evidence(
            city=city, target_date=target_date, metric=metric
        )
        if direct is not None:
            candidates.append(direct)
    durable = _durable_observation_instants_summary(
        city=city, target_date=target_date, now=now, world_conn=world_conn
    )
    if durable is not None:
        durable_evidence = (
            durable.high_evidence if metric == "high" else durable.low_evidence
        )
        if durable_evidence is not None:
            candidates.append(durable_evidence)
    fast = _durable_fast_tail_hard_fact_evidence(
        city=city,
        target_date=target_date,
        metric=metric,
        now=now,
        world_conn=world_conn,
    )
    if fast is not None:
        candidates.append(fast)
    return _combined_wu_hard_fact_evidence(candidates, metric=metric, city=city)


def settlement_grade_effective_extreme(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    world_conn: Any = None,
    durable_only: bool = False,
) -> tuple[Optional[float], str]:
    """(effective_extreme, source) for hard-fact decisions, margin-adjusted.

    Current evidence is routed by ``settlement_source_type``. WU contracts may
    compose WU live/durable bucket facts with calibrated same-station METAR.
    HKO intraday cumulative extrema are provider-correct but provisional and may
    be revised. They are probability evidence, not a pathwise payoff fact, so
    this hard-fact seam abstains until the final daily HKO product is available.
    None is returned when no logically absorbing source is available.

    ``world_conn`` is threaded from the monitoring-phase composite connection so
    the METAR kill-memo recovery (cold-start path) does not open an independent
    world connection — see connection-burst antibody (2026-06-13).
    """
    city_name = str(getattr(city, "name", "") or "")
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip().lower()
    current_high = current_low = None
    current_source = ""
    if not durable_only and source_type == "wu_icao":
        current_high, current_low = _wu_rounded_extremes(city, target_date, now=now)
        current_source = "wu_api"

    durable_high = durable_low = None
    durable_source = ""
    if source_type == "wu_icao":
        durable_high, durable_low, durable_source = _durable_observation_instants_extremes(
            city=city,
            target_date=target_date,
            now=now,
            world_conn=world_conn,
        )

    reference_values = []
    reference_sources = []
    api_value = current_high if metric == "high" else current_low
    durable_value = durable_high if metric == "high" else durable_low
    if api_value is not None:
        reference_values.append(float(api_value))
        reference_sources.append(current_source)
    if durable_value is not None:
        reference_values.append(float(durable_value))
        reference_sources.append(durable_source)
    if reference_values:
        reference_value = max(reference_values) if metric == "high" else min(reference_values)
        reference_source = "+".join(dict.fromkeys(reference_sources))
    else:
        reference_value = None
        reference_source = ""

    from src.data.day0_fast_obs import fast_obs_source_for_city

    metar_value = None
    fast_source = fast_obs_source_for_city(city) if source_type == "wu_icao" else None
    margin = _metar_kill_margin_units(city_name, unit) if fast_source is not None else None
    if margin is not None:
        raw = _metar_rounded_extreme(city_name, target_date, metric, world_conn=world_conn)
        if raw is not None:
            metar_value = raw - margin if metric == "high" else raw + margin

    if reference_value is None and metar_value is None:
        return None, ""
    if metar_value is None:
        return float(reference_value), reference_source
    if reference_value is None:
        return float(metar_value), SAME_STATION_FAST_TAIL_SOURCE
    if metric == "high":
        return float(max(reference_value, metar_value)), f"{reference_source}+{SAME_STATION_FAST_TAIL_SOURCE}"
    return float(min(reference_value, metar_value)), f"{reference_source}+{SAME_STATION_FAST_TAIL_SOURCE}"


def day0_entry_bin_still_alive(
    *,
    city: Any,
    target_date: str,
    metric: str,
    direction: str,
    bin_low: Optional[float],
    bin_high: Optional[float],
    now: Optional[datetime] = None,
    world_conn: Any = None,
) -> bool:
    """Submit-time hard-fact re-check for a Day0 ENTRY (H-2, audit 2026-07-18).

    Selection-time truth != submit-time truth: between selection and submit the
    running extreme can cross the selected bin's survival edge. This asks the SAME
    durable settlement-grade extreme + verdict the exit/cancel lanes use whether
    the entry side is now structurally DEAD. The final submit seam must not add a
    network request: durable monotone extrema are sufficient to prove death, while
    absence of such a fact abstains. A current anomaly pause also refuses submit.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        from src.data.day0_oracle_anomaly import is_day0_family_paused

        city_name = str(getattr(city, "name", "") or "")
        if is_day0_family_paused(city_name, target_date, now=moment):
            return False
        evidence = _wu_hard_fact_evidence(
            city=city,
            target_date=target_date,
            metric=metric,
            now=moment,
            world_conn=world_conn,
            durable_only=True,
        )
        if evidence is None:
            return True
        verdict = hard_fact_bin_verdict(
            metric=metric, direction=direction,
            bin_low=bin_low, bin_high=bin_high,
            effective_extreme=evidence.rounded_extreme,
        )
        return verdict is None or verdict.action != "EXIT_DEAD_BIN"
    except Exception:  # noqa: BLE001 — fail-soft: never block a submit on a lane error
        return True


def evaluate_hard_fact_exit(
    *,
    position: Any,
    city: Any,
    now: Optional[datetime] = None,
    world_conn: Any = None,
    durable_only: bool = False,
) -> Optional[HardFactVerdict]:
    """The lane entry point for one held day0 position. None = no hard fact
    (the estimator-evidence lane proceeds unchanged). Fail-soft everywhere:
    any data gap or active oracle-anomaly pause yields None (hold).

    ``world_conn`` should be the caller's composite world connection (zeus_trades
    with zeus-world ATTACHed). It is threaded through to the METAR kill-memo
    recovery path so the cold-start restart does not open an independent world
    connection per city. When None, the METAR memo recovery is skipped for cold
    cells; warm memo cells are unaffected.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        target_date = str(getattr(position, "target_date", "") or "")
        direction = _normalize_direction(getattr(position, "direction", "") or "")
        metric = str(getattr(position, "temperature_metric", "") or "high")
        if not target_date or direction not in {"buy_yes", "buy_no"}:
            return None

        from src.data.day0_oracle_anomaly import is_day0_family_paused

        city_name = str(getattr(city, "name", "") or "")
        if is_day0_family_paused(city_name, target_date, now=moment):
            logger.warning(
                "DAY0_HARD_FACT_LANE_SUSPENDED city=%s date=%s trade=%s — oracle anomaly pause active",
                city_name, target_date, getattr(position, "trade_id", "?"),
            )
            return None

        from src.data.market_scanner import _parse_temp_range

        bin_low, bin_high = _parse_temp_range(str(getattr(position, "bin_label", "") or ""))
        if bin_low is None and bin_high is None:
            return None

        evidence = _wu_hard_fact_evidence(
            city=city,
            target_date=target_date,
            metric=metric,
            now=moment,
            world_conn=world_conn,
            durable_only=durable_only,
        )
        if evidence is None:
            return None
        verdict = hard_fact_bin_verdict(
            metric=metric, direction=direction,
            bin_low=bin_low, bin_high=bin_high,
            effective_extreme=evidence.rounded_extreme,
        )
        if verdict is None:
            return None
        verdict = HardFactVerdict(
            action=verdict.action, reason=verdict.reason, metric=verdict.metric,
            rounded_extreme=verdict.rounded_extreme,
            source=evidence.source,
            evidence=evidence,
        )
        log = logger.warning if verdict.action == "EXIT_DEAD_BIN" else logger.info
        log(
            "DAY0_HARD_FACT_%s trade=%s city=%s date=%s dir=%s bin=[%s,%s] extreme=%s source=%s: %s",
            verdict.action, getattr(position, "trade_id", "?"), city_name, target_date,
            direction, bin_low, bin_high, evidence.rounded_extreme, evidence.source, verdict.reason,
        )
        return verdict
    except Exception as exc:  # noqa: BLE001 — the lane must never break the monitor
        logger.warning(
            "DAY0_HARD_FACT_LANE_ERROR trade=%s exc=%s: %s",
            getattr(position, "trade_id", "?"), type(exc).__name__, exc,
        )
        return None


# ---------------------------------------------------------------------------
# FIX 2 — resting-order cancel on bin death / family anomaly pause.
# Minimal correct cut (adversarial review finding 4): day0 families' resting
# ENTRY orders are cancelled when their bin is hard-fact dead for the order's
# side, or when the family is oracle-anomaly paused. The general
# screen_reprice/stale-quote cancel wiring remains future work.
# ---------------------------------------------------------------------------


def _order_field(order: dict, *names: str) -> str:
    for name in names:
        value = order.get(name)
        if value:
            return str(value)
    return ""


def _row_get(row: Any, key: str, index: int) -> Any:
    return row[key] if hasattr(row, "keys") else row[index]


def _resolve_order_bin_identity(
    conn: Any,
    token_id: str,
    *,
    market_conn: Any | None = None,
) -> Optional[dict]:
    """Token -> (city, target_date, metric, range bounds, direction) using the
    PRODUCTION topology surfaces (PR#404 P1 fix — the prior single
    market_events.token_id lookup missed every NO token, because market_events
    stores only the YES token; and the metric was guessed from the slug).

    Resolution chain (all fail-soft per source):
      1. executable_market_snapshots (trades main schema): yes_token_id /
         no_token_id -> condition_id + DIRECTION (asset==no_token -> buy_no).
      2. market_events by condition_id OR token_id (main / world. / forecasts.
         schemas): city, target_date, range_low/high, and — where the schema
         carries it — the TYPED temperature_metric column.
      3. market_topology_state by condition_id (trades main schema): the TYPED
         temperature_metric + city_id + target_local_date authority.
    The metric is NEVER derived from slug substrings: a row whose metric
    cannot be typed is SKIPPED (no cancel — fail-soft, never wrong-direction).
    """
    import sqlite3 as _sqlite3

    condition_id = ""
    direction = ""
    try:
        row = conn.execute(
            """
            SELECT condition_id, yes_token_id, no_token_id
            FROM executable_market_snapshots
            WHERE yes_token_id = ? OR no_token_id = ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (token_id, token_id),
        ).fetchone()
        if row is not None:
            condition_id = str(_row_get(row, "condition_id", 0) or "")
            no_token = str(_row_get(row, "no_token_id", 2) or "")
            direction = "buy_no" if token_id == no_token else "buy_yes"
    except _sqlite3.Error:
        pass

    identity: dict = {}
    # EXPLICIT COLUMN LISTS + tuple-safe access (PR#404 round-2 P1-B): the
    # prior SELECT * + `dict(row) if hasattr(row, "keys") else {}` silently
    # produced an EMPTY identity on connections WITHOUT sqlite3.Row factory —
    # a dead-bin resting order quietly escaped cancellation because of an
    # implicit connection attribute. A risk-reduction path must be
    # row-factory-agnostic: explicit columns + positional _row_get, with a
    # two-query fallback for legacy schemas lacking temperature_metric.
    _ME_COLS_WITH_METRIC = (
        "city, target_date, range_low, range_high, temperature_metric, condition_id, token_id"
    )
    _ME_COLS_LEGACY = "city, target_date, range_low, range_high, condition_id, token_id"
    market_surfaces = []
    if market_conn is not None:
        market_surfaces.append((market_conn, "market_events"))
    market_surfaces.extend(
        (
            (conn, "market_events"),
            (conn, "world.market_events"),
            (conn, "forecasts.market_events"),
        )
    )
    for surface_conn, table_ref in market_surfaces:
        me_row = None
        has_metric_col = True
        for columns, with_metric in ((_ME_COLS_WITH_METRIC, True), (_ME_COLS_LEGACY, False)):
            try:
                if condition_id:
                    me_row = surface_conn.execute(
                        f"SELECT {columns} FROM {table_ref} "
                        "WHERE condition_id = ? OR token_id = ? LIMIT 1",
                        (condition_id, token_id),
                    ).fetchone()
                else:
                    me_row = surface_conn.execute(
                        f"SELECT {columns} FROM {table_ref} WHERE token_id = ? LIMIT 1",
                        (token_id,),
                    ).fetchone()
                has_metric_col = with_metric
                break  # query shape accepted (row may still be None)
            except _sqlite3.Error:
                me_row = None
                continue  # missing table/schema OR missing temperature_metric column
        if me_row is None:
            continue
        if has_metric_col:
            metric_value = str(_row_get(me_row, "temperature_metric", 4) or "")
            cond_value = str(_row_get(me_row, "condition_id", 5) or "")
            row_token = str(_row_get(me_row, "token_id", 6) or "")
        else:
            metric_value = ""
            cond_value = str(_row_get(me_row, "condition_id", 4) or "")
            row_token = str(_row_get(me_row, "token_id", 5) or "")
        identity = {
            "city": str(_row_get(me_row, "city", 0) or ""),
            "target_date": str(_row_get(me_row, "target_date", 1) or ""),
            "range_low": _row_get(me_row, "range_low", 2),
            "range_high": _row_get(me_row, "range_high", 3),
            "metric": metric_value,
            "condition_id": condition_id or cond_value,
        }
        if not direction:
            # market_events stores the YES token; matching by token_id here
            # means the order IS the YES side.
            direction = "buy_yes" if row_token == token_id else ""
        break
    if not identity:
        return None

    if not identity.get("metric") and identity.get("condition_id"):
        # TYPED metric authority: market_topology_state (never slug guessing).
        try:
            mts = conn.execute(
                """
                SELECT temperature_metric, city_id, target_local_date
                FROM market_topology_state
                WHERE condition_id = ?
                ORDER BY recorded_at DESC LIMIT 1
                """,
                (identity["condition_id"],),
            ).fetchone()
            if mts is not None:
                identity["metric"] = str(_row_get(mts, "temperature_metric", 0) or "")
                identity.setdefault("city", str(_row_get(mts, "city_id", 1) or ""))
                if not identity.get("target_date"):
                    identity["target_date"] = str(_row_get(mts, "target_local_date", 2) or "")
        except _sqlite3.Error:
            pass

    if identity.get("metric") not in {"high", "low"} or not direction:
        return None
    identity["direction"] = direction
    return identity


def classify_day0_dead_bin_entry_cancels(
    entries: Collection[dict[str, Any]],
    *,
    trade_conn: Any,
    forecasts_conn: Any,
    cities_by_name: dict[str, Any],
    now: Optional[datetime] = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Classify canonical open ENTRY commands; never call the venue."""

    from src.data.day0_oracle_anomaly import is_day0_family_paused
    from zoneinfo import ZoneInfo

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    ordered = list(entries)
    if not ordered:
        return []
    scan_limit = min(len(ordered), max(1, int(limit)))
    with _RESTING_ENTRY_SCAN_CURSOR_LOCK:
        global _RESTING_ENTRY_SCAN_CURSOR
        start = _RESTING_ENTRY_SCAN_CURSOR % len(ordered)
        _RESTING_ENTRY_SCAN_CURSOR = (start + scan_limit) % len(ordered)
    scan_entries = [ordered[(start + offset) % len(ordered)] for offset in range(scan_limit)]

    cancel_set: list[dict[str, Any]] = []
    for entry in scan_entries:
        try:
            if str(entry.get("command_side") or entry.get("side") or "").upper() != "BUY":
                continue
            command_id = str(entry.get("command_id") or "").strip()
            token_id = str(entry.get("token_id") or "").strip()
            if not command_id or not token_id:
                logger.warning(
                    "Day0 cancel classification missing canonical identity: command=%s token=%s",
                    command_id or "missing",
                    token_id or "missing",
                )
                continue
            identity = _resolve_order_bin_identity(
                trade_conn,
                token_id,
                market_conn=forecasts_conn,
            )
            if identity is None:
                logger.warning(
                    "Day0 cancel classification unresolved token identity: command=%s token=%s",
                    command_id,
                    token_id,
                )
                continue
            city_name = str(identity["city"])
            target_date = str(identity["target_date"])
            metric = str(identity["metric"])
            city = cities_by_name.get(city_name)
            if city is None:
                continue
            local_today = moment.astimezone(ZoneInfo(str(city.timezone))).date().isoformat()
            if target_date[:10] != local_today:
                continue

            paused = is_day0_family_paused(city_name, target_date, now=moment)
            verdict = None
            if not paused:
                evidence = _wu_hard_fact_evidence(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    now=moment,
                    world_conn=forecasts_conn,
                    durable_only=False,
                )
                if evidence is not None:
                    verdict = hard_fact_bin_verdict(
                        metric=metric,
                        direction=str(identity["direction"]),
                        bin_low=(
                            float(identity["range_low"])
                            if identity["range_low"] is not None
                            else None
                        ),
                        bin_high=(
                            float(identity["range_high"])
                            if identity["range_high"] is not None
                            else None
                        ),
                        effective_extreme=evidence.rounded_extreme,
                    )
            if not paused and (verdict is None or verdict.action != "EXIT_DEAD_BIN"):
                continue
            reason = "ORACLE_ANOMALY_PAUSE" if paused else "HARD_FACT_BIN_DEAD"
            cancel_set.append(
                {
                    **entry,
                    "family": (city_name, target_date, metric),
                    "cancel_reason": reason,
                    "cancel_action": "CANCEL_REPLACE",
                    "cancel_detail": {
                        "trigger": "day0_dead_bin_cancel",
                        "direction": str(identity["direction"]),
                        "reason": reason,
                        "verdict_reason": "" if paused else str(verdict.reason),
                    },
                }
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            logger.warning(
                "Day0 cancel classification rejected command=%s: %s",
                str(entry.get("command_id") or "unknown"),
                exc,
            )
    return cancel_set


_TARGET_CANCEL_COMMAND_STATES = (
    "POSTING",
    "POST_ACKED",
    "SUBMITTING",
    "ACKED",
    "UNKNOWN",
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "PARTIAL",
    "CANCEL_PENDING",
    "REVIEW_REQUIRED",
)


def _target_family_entry_orders(
    conn: Any,
    target_family_keys: set[tuple[str, str, str]],
) -> Optional[list[dict[str, str]]]:
    """Return command-known target orders, or None when local scope is incomplete."""
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(venue_commands)").fetchall()
        }
    except Exception:  # noqa: BLE001 - unavailable local authority falls back to venue scan
        return None
    if not {
        "intent_kind",
        "side",
        "state",
        "token_id",
        "venue_order_id",
    }.issubset(columns):
        return None

    placeholders = ",".join("?" for _ in _TARGET_CANCEL_COMMAND_STATES)
    family_joined = "position_id" in columns
    try:
        if family_joined:
            try:
                rows = conn.execute(
                    f"""
                    SELECT vc.venue_order_id, vc.token_id, vc.side, vc.state,
                           vc.position_id, pc.city AS local_city,
                           pc.target_date AS local_target_date,
                           pc.temperature_metric AS local_metric
                      FROM venue_commands AS vc
                 LEFT JOIN position_current AS pc
                        ON pc.position_id = vc.position_id
                     WHERE vc.intent_kind = 'ENTRY'
                       AND upper(vc.side) = 'BUY'
                       AND vc.state IN ({placeholders})
                    """,
                    _TARGET_CANCEL_COMMAND_STATES,
                ).fetchall()
            except Exception:  # noqa: BLE001 - legacy schemas keep token fallback
                family_joined = False
        if not family_joined:
            rows = conn.execute(
                f"""
                SELECT venue_order_id, token_id, side, state
                  FROM venue_commands
                 WHERE intent_kind = 'ENTRY'
                   AND upper(side) = 'BUY'
                   AND state IN ({placeholders})
                """,
                _TARGET_CANCEL_COMMAND_STATES,
            ).fetchall()
    except Exception:  # noqa: BLE001 - preserve the prior authoritative venue fallback
        return None

    orders: dict[str, dict[str, str]] = {}
    for row in rows:
        token_id = str(_row_get(row, "token_id", 1) or "").strip()
        if not token_id:
            return None
        local_family = (
            (
                str(_row_get(row, "local_city", 5) or "").strip().casefold(),
                str(_row_get(row, "local_target_date", 6) or "").strip()[:10],
                str(_row_get(row, "local_metric", 7) or "").strip().lower(),
            )
            if family_joined
            else ("", "", "")
        )
        if all(local_family):
            family_key = local_family
        else:
            identity = _resolve_order_bin_identity(conn, token_id)
            if identity is None:
                return None
            family_key = (
                str(identity.get("city") or "").strip().casefold(),
                str(identity.get("target_date") or "").strip()[:10],
                str(identity.get("metric") or "").strip().lower(),
            )
        if family_key not in target_family_keys:
            continue

        order_id = str(_row_get(row, "venue_order_id", 0) or "").strip()
        if not order_id:
            return None
        orders[order_id] = {
            "orderID": order_id,
            "asset_id": token_id,
            "side": str(_row_get(row, "side", 2) or "BUY"),
        }
    return list(orders.values())


def cancel_day0_dead_bin_resting_entries(
    *,
    clob: Any,
    conn: Any,
    cities_by_name: dict[str, Any],
    now: Optional[datetime] = None,
    limit: int = 25,
    target_families: Collection[tuple[str, str, str]] | None = None,
) -> int:
    """Compatibility seam routed through canonical classification and journal.

    New production code calls the C3 owner directly. This seam remains only for
    older callers; it never scans wallet orders and never invokes a raw venue
    cancel method.
    """
    from src.execution.batch_order_submission import cancel_commands_batch
    from src.execution.staleness_cancel import find_open_entry_rests

    entries = find_open_entry_rests(conn)
    proposals = classify_day0_dead_bin_entry_cancels(
        entries,
        trade_conn=conn,
        forecasts_conn=conn,
        cities_by_name=cities_by_name,
        now=now,
        limit=limit,
    )
    if target_families is not None:
        targets = {
            (
                str(city).strip().casefold(),
                str(target_date).strip()[:10],
                str(metric).strip().lower(),
            )
            for city, target_date, metric in target_families
        }
        proposals = [
            proposal
            for proposal in proposals
            if tuple(str(value).strip().casefold() for value in proposal["family"])
            in targets
        ]
    if not proposals:
        return 0
    outcomes = cancel_commands_batch(
        conn,
        clob,
        [str(proposal["command_id"]) for proposal in proposals],
    )
    return sum(1 for outcome in outcomes if outcome.status == "acked")


def _reset_wu_memo_for_tests() -> None:
    global _RESTING_ENTRY_SCAN_CURSOR
    with _CURRENT_SOURCE_MEMO_LOCK:
        _CURRENT_SOURCE_MEMO.clear()
    with _RESTING_ENTRY_SCAN_CURSOR_LOCK:
        _RESTING_ENTRY_SCAN_CURSOR = 0
