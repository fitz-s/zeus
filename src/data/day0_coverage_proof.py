# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #2) — strengthen Day0 coverage from a
#   first-sample heuristic to a proof object that also certifies max-gap, expected cadence, and
#   coverage-through-decision. Reuses the existing window-grace + min-count logic
#   (src/data/observation_client._compute_day0_coverage_status) as the SINGLE authority for that
#   part; adds the gap / cadence / coverage-through proof on top. Live Day0 entry accepts only
#   FULL_THROUGH_DECISION (or an explicit probe-only state).
"""Day0CoverageProof — a richer coverage certificate for the Day0 live lane.

The legacy coverage status only proves the first sample landed within a grace window of local
midnight and that there are enough samples. That does NOT prove there were no large mid-day gaps,
nor that coverage extends THROUGH the decision time. This module produces a typed proof that adds:

  * max_gap_minutes        — largest gap between consecutive samples (None when the per-sample
                             sequence is unavailable; a count-density proxy drives GAP_INCOMPLETE then)
  * expected_cadence_minutes — the nominal report cadence the gap is judged against
  * coverage_through_utc    — the last sample time (how far coverage is proven to extend)
  * dst_day_length_hours    — 23 / 24 / 25, computed from the city tz + target local date
  * status                  — FULL_THROUGH_DECISION | GAP_INCOMPLETE | WINDOW_INCOMPLETE | LOW_COVERAGE

Pure: no network, no DB. Status precedence (first match wins):
  WINDOW_INCOMPLETE  (first sample too long after local midnight)
  LOW_COVERAGE       (fewer than min samples)
  GAP_INCOMPLETE     (a gap — true max_gap or the density proxy — exceeds the cadence tolerance,
                      OR coverage does not extend close enough to the decision time)
  FULL_THROUGH_DECISION (none of the above)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.data.observation_client import _compute_day0_coverage_status

# Default METAR-class report cadence (minutes). Routine METAR is ~hourly; specials are denser. The
# gap test allows GAP_TOLERANCE_FACTOR × cadence before flagging a hole.
DEFAULT_EXPECTED_CADENCE_MINUTES = 60.0
GAP_TOLERANCE_FACTOR = 2.5
# Coverage must extend to within this × cadence of the decision time to be FULL_THROUGH_DECISION
# (i.e. no stale tail between the last obs and the decision).
THROUGH_DECISION_TOLERANCE_FACTOR = 2.5
# Density proxy (used only when the per-sample sequence is absent): if the observed sample count is
# below this fraction of the cadence-implied expectation over [first, last], call it GAP_INCOMPLETE.
DENSITY_MIN_FRACTION = 0.5

_MIN_SAMPLE_COUNT = 4  # mirrors observation_client._DAY0_MIN_SAMPLE_COUNT


@dataclass(frozen=True, slots=True)
class Day0CoverageProof:
    status: str
    first_sample_utc: str | None
    last_sample_utc: str | None
    coverage_through_utc: str | None
    max_gap_minutes: float | None
    expected_cadence_minutes: float
    sample_count: int
    dst_day_length_hours: float
    proof_source: str

    @property
    def is_full_through_decision(self) -> bool:
        return self.status == "FULL_THROUGH_DECISION"


def dst_day_length_hours(target_local_date: date, tz: ZoneInfo) -> float:
    """Length of the local calendar day in hours (24 normally, 23 spring-forward, 25 fall-back)."""
    start = datetime(target_local_date.year, target_local_date.month, target_local_date.day, tzinfo=tz)
    nxt = target_local_date + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return (end.astimezone(ZoneInfo("UTC")) - start.astimezone(ZoneInfo("UTC"))).total_seconds() / 3600.0


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(ZoneInfo("UTC")).isoformat() if dt is not None else None


def compute_day0_coverage_proof(
    *,
    target_local_date: date,
    tz: ZoneInfo,
    decision_time_utc: datetime,
    first_sample_local: datetime | None,
    last_sample_utc: datetime | None,
    sample_count: int,
    sample_times_utc: list[datetime] | None = None,
    expected_cadence_minutes: float = DEFAULT_EXPECTED_CADENCE_MINUTES,
    min_samples: int = _MIN_SAMPLE_COUNT,
    proof_source: str = "",
) -> Day0CoverageProof:
    """Build the Day0 coverage proof.

    ``first_sample_local`` is in the city's local tz (the legacy window-grace check is local). All
    other times are UTC-aware. ``sample_times_utc`` (the full per-sample sequence) is optional: when
    present, ``max_gap_minutes`` is the true largest consecutive gap; when absent, max_gap is None and
    a count-density proxy drives the GAP_INCOMPLETE decision.
    """
    dst_len = dst_day_length_hours(target_local_date, tz)
    first_iso = _iso(first_sample_local) if first_sample_local is not None else None
    last_iso = _iso(last_sample_utc)
    coverage_through = last_iso  # coverage is proven up to the last sample

    # --- max gap (true if the sequence is given) ---
    max_gap: float | None = None
    if sample_times_utc:
        ordered = sorted(sample_times_utc)
        if len(ordered) >= 2:
            gaps = [
                (b - a).total_seconds() / 60.0
                for a, b in zip(ordered[:-1], ordered[1:])
            ]
            max_gap = max(gaps)

    def _proof(status: str) -> Day0CoverageProof:
        return Day0CoverageProof(
            status=status,
            first_sample_utc=first_iso,
            last_sample_utc=last_iso,
            coverage_through_utc=coverage_through,
            max_gap_minutes=max_gap,
            expected_cadence_minutes=expected_cadence_minutes,
            sample_count=int(sample_count),
            dst_day_length_hours=dst_len,
            proof_source=proof_source,
        )

    # 1) WINDOW_INCOMPLETE / LOW_COVERAGE — reuse the SINGLE legacy authority for the window+count part.
    if first_sample_local is None or sample_count <= 0:
        return _proof("WINDOW_INCOMPLETE")
    legacy = _compute_day0_coverage_status(first_sample_local, int(sample_count), min_samples=min_samples)
    if legacy == "WINDOW_INCOMPLETE":
        return _proof("WINDOW_INCOMPLETE")
    if legacy == "LOW_COVERAGE":
        return _proof("LOW_COVERAGE")

    # 2) GAP_INCOMPLETE — interior hole, by true max_gap or density proxy.
    gap_ceiling = GAP_TOLERANCE_FACTOR * expected_cadence_minutes
    if max_gap is not None:
        if max_gap > gap_ceiling:
            return _proof("GAP_INCOMPLETE")
    elif last_sample_utc is not None and first_sample_local is not None:
        # density proxy: expected samples over the observed span vs actual count
        span_min = (last_sample_utc - first_sample_local.astimezone(ZoneInfo("UTC"))).total_seconds() / 60.0
        if span_min > 0:
            expected_n = span_min / expected_cadence_minutes + 1.0
            if sample_count < DENSITY_MIN_FRACTION * expected_n:
                return _proof("GAP_INCOMPLETE")

    # 3) coverage must extend close enough to the decision time (no stale tail).
    if last_sample_utc is None:
        return _proof("GAP_INCOMPLETE")
    tail_min = (decision_time_utc - last_sample_utc).total_seconds() / 60.0
    if tail_min > THROUGH_DECISION_TOLERANCE_FACTOR * expected_cadence_minutes:
        return _proof("GAP_INCOMPLETE")

    # 4) full coverage through the decision.
    return _proof("FULL_THROUGH_DECISION")


def coverage_proof_from_first_sample(
    first_sample_local: datetime,
    sample_count: int,
    *,
    target_local_date: date,
    tz: ZoneInfo,
    min_samples: int = _MIN_SAMPLE_COUNT,
    proof_source: str = "legacy_first_sample",
) -> Day0CoverageProof:
    """Weak proof from only (first sample, count) — the legacy callers' information.

    Can NEVER return FULL_THROUGH_DECISION (no gap/through-decision evidence): a caller with only the
    first sample and a count cannot certify the strong proof the live admission gate requires.
    """
    dst_len = dst_day_length_hours(target_local_date, tz)
    legacy = _compute_day0_coverage_status(first_sample_local, int(sample_count), min_samples=min_samples)
    status = legacy if legacy in ("WINDOW_INCOMPLETE", "LOW_COVERAGE") else "GAP_INCOMPLETE"
    return Day0CoverageProof(
        status=status,
        first_sample_utc=_iso(first_sample_local),
        last_sample_utc=None,
        coverage_through_utc=None,
        max_gap_minutes=None,
        expected_cadence_minutes=DEFAULT_EXPECTED_CADENCE_MINUTES,
        sample_count=int(sample_count),
        dst_day_length_hours=dst_len,
        proof_source=proof_source,
    )
