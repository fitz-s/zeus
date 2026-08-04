# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~03:40Z — "不能再手动必须确认自动下载并且
#   在我们需要他之前就下载像其他数据一样 … 数字不要乱猜". Raw-input cycles must be fetched
#   AUTOMATICALLY, BEFORE they are needed, with availability discovered by PROBES — never by
#   a guessed release-lag constant. Consolidated overhaul ledger item K4.0b(a).
"""Probe-resolved replacement-forecast cycle availability.

The legacy download cadence resolved "the available cycle" as now minus a fixed lag-hours
floored to a 6h cycle — a GUESS constant. A wrong guess either downloads a not-yet-published
cycle (wasted fire, no retry until the next cron slot) or wastes hours of freshness waiting
for a cycle that has long been published. 2026-06-10 the 12Z anchor leg was simply not
published at the provider when the single cron fire ran, and the engine then starved for
hours on stale bounds (RULE-1 audit, docs/evidence/rule1_audits/).

This module replaces the guess with Open-Meteo anchor PROBES:

- ``probe_openmeteo_single_run_available``: one minimal single-runs API request
  (one probe city, forecast_hours=1) — their API answers 400 "model run is not available"
  until the run is published, 200 after.
- ``resolve_anchor_cycle_availability``: pure logic — walk candidate cycles newest→oldest
  from wall clock (NO lag constant anywhere in the decision) and report the anchor
  leg's published state.

Observed publication lags are MEASURED for free as a by-product: each leg's source_run /
raw_forecast_artifacts row carries captured_at, and (captured_at − source_cycle_time) under
a polling fetcher converges on the true publication lag (poll-cadence resolution). The time
registry references that derivation instead of declaring a number.
"""
from __future__ import annotations

import logging
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from src.data.openmeteo_client import fetch as _fetch_openmeteo
from src.data.openmeteo_quota import quota_tracker

logger = logging.getLogger("zeus.replacement_cycle_availability")

UTC = timezone.utc

CYCLE_HOURS = (0, 6, 12, 18)

# Probe city for the open-meteo single-runs availability check. Any modeled city works —
# the run is published globally or not at all; Atlanta is the alphabetically-first config
# city and was the leg's own first request in the 2026-06-10 incident.
OPENMETEO_PROBE_LAT = 33.63
OPENMETEO_PROBE_LON = -84.44
OPENMETEO_SINGLE_RUNS_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&hourly=temperature_2m&models=ecmwf_ifs"
    "&run={run}&forecast_hours=1&temperature_unit=celsius&timezone=UTC"
)
OPENMETEO_PROBE_TIMEOUT_SECONDS = 20.0

# How many cycles to walk back from wall clock when resolving availability. Four 6h cycles
# = one day of lookback, strictly more than any observed publication lag; beyond that the
# cycle is superseded anyway. This is a SEARCH BOUND, not an availability assumption.
DEFAULT_MAX_LOOKBACK_CYCLES = 4


def floor_to_cycle(now: datetime) -> datetime:
    """Newest cycle timestamp not after ``now`` (00/06/12/18Z grid)."""
    now_utc = now.astimezone(UTC)
    hour = max(h for h in CYCLE_HOURS if h <= now_utc.hour)
    return now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)


def candidate_cycles(
    now: datetime, *, max_lookback_cycles: int = DEFAULT_MAX_LOOKBACK_CYCLES
) -> tuple[datetime, ...]:
    """Candidate cycles newest→oldest from wall clock. No lag constant involved."""
    newest = floor_to_cycle(now)
    return tuple(newest - timedelta(hours=6 * i) for i in range(max_lookback_cycles))


def probe_openmeteo_single_run_available(
    cycle: datetime,
    *,
    urlopen: Callable[..., object] | None = None,
) -> bool:
    """True iff the open-meteo single-runs API serves this ecmwf_ifs run.

    Their API contract (observed + pinned 2026-06-10/11): HTTP 400 with reason
    "The requested model run is not available" before publication, HTTP 200 after.
    Any transport error → False (treat as not-yet-available; the poll retries).
    """
    run = cycle.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
    url = OPENMETEO_SINGLE_RUNS_URL.format(
        lat=OPENMETEO_PROBE_LAT, lon=OPENMETEO_PROBE_LON, run=run
    )
    if urlopen is None:
        try:
            with quota_tracker.priority_lane():
                _fetch_openmeteo(
                    url,
                    {},
                    timeout=OPENMETEO_PROBE_TIMEOUT_SECONDS,
                    max_retries=1,
                    endpoint_label="source_clock_anchor_availability",
                    fast_fail_429=True,
                )
            return True
        except Exception as exc:  # noqa: BLE001 — next poll retries fresh provider truth.
            logger.debug("openmeteo single-run probe unavailable: %s", exc)
            return False
    try:
        with urlopen(url, timeout=OPENMETEO_PROBE_TIMEOUT_SECONDS) as resp:  # type: ignore[call-arg]
            return int(getattr(resp, "status", 0) or 0) == 200
    except urllib.error.HTTPError:
        return False
    except Exception as exc:  # noqa: BLE001 — transport noise = not available yet
        logger.debug("openmeteo single-run probe error (treated unavailable): %s", exc)
        return False


def probe_bucket_run_declared(cycle: datetime) -> bool:
    """True iff the S3 data_spatial bucket declares this run (rung-3 transport probe).

    Declaration (a latest/in-progress manifest with reference_time == cycle) is the
    necessary condition for the bucket transport; per-city timestep admission and the
    cross-check whitelist gate at FETCH time (per-city fail-soft skip in the downloader),
    so declaration alone marks the leg fetchable for the poll. Any probe error → False
    (treated not-yet-available; the poll retries next tick)."""
    try:
        from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
            fetch_bucket_run_manifest,
            select_declaring_manifest,
        )

        manifests = fetch_bucket_run_manifest()
        return (
            select_declaring_manifest(manifests, wanted_run=cycle.astimezone(UTC))
            is not None
        )
    except Exception as exc:  # noqa: BLE001 — probe noise = not available yet
        logger.debug("bucket run probe error (treated unavailable): %s", exc)
        return False


class AnchorAvailabilityProbe:
    """One provider snapshot reused across one candidate-cycle decision.

    Single-runs and bucket availability remain cycle-specific.  Model metadata
    describes one provider-current run, so fetching it once per candidate was
    redundant and made the 15-second poll deterministically exhaust daily quota.
    """

    def __init__(
        self,
        *,
        urlopen: Callable[..., object] | None = None,
        meta_fetch: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self._urlopen = urlopen
        self._meta_fetch = meta_fetch
        self._meta_loaded = False
        self._meta: Mapping[str, Any] | None = None

    def _model_meta(self) -> Mapping[str, Any] | None:
        if self._meta_loaded:
            return self._meta
        self._meta_loaded = True
        try:
            if self._meta_fetch is None:
                from src.data.openmeteo_ecmwf_ifs9_anchor import (
                    fetch_openmeteo_ifs9_model_meta,
                )

                self._meta = fetch_openmeteo_ifs9_model_meta()
            else:
                self._meta = self._meta_fetch()
        except Exception as exc:  # noqa: BLE001 -- next transport rung remains valid.
            logger.debug("anchor meta probe error (treated unavailable): %s", exc)
            self._meta = None
        return self._meta

    def __call__(self, cycle: datetime) -> bool:
        if probe_openmeteo_single_run_available(cycle, urlopen=self._urlopen):
            return True
        meta = self._model_meta()
        if meta is not None:
            try:
                if (
                    meta["run_initialisation_utc"] == cycle.astimezone(UTC)
                    and meta["run_availability_utc"]
                    >= meta["run_initialisation_utc"]
                ):
                    return True
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("anchor meta probe malformed (treated unavailable): %s", exc)
        return probe_bucket_run_declared(cycle)


def probe_anchor_available_any(
    cycle: datetime,
    *,
    urlopen: Callable[..., object] | None = None,
) -> bool:
    """True iff the anchor leg can be fetched for this cycle by ANY ladder transport.

    Transport ladder mirror (K4.0b(f)): run-pinned single-runs OR the meta-stamped
    standard API (provider meta declares exactly this run as its current completed run)
    OR the S3 data_spatial bucket declaring the run (rung 3 — serves a run's steps the
    moment they are written, hours before the API publishes; 2026-06-11 the 00Z run was
    bucket-only while meta still declared 06-10T06Z). Every path yields a journalable
    anchor artifact with explicit run authority, so the availability poll may treat the
    leg as published when any probe passes. The probe set MUST stay a superset-mirror of
    the downloader's ladder: a rung the probe cannot see is a rung the run-selection
    authority will starve (the downloader only ever fetches probe-confirmed cycles)."""
    return AnchorAvailabilityProbe(urlopen=urlopen)(cycle)


_DEFAULT_ANCHOR_PROBE = probe_anchor_available_any


def resolve_provider_anchor_cycle_availability(
    now: datetime,
    *,
    max_lookback_cycles: int = DEFAULT_MAX_LOOKBACK_CYCLES,
) -> tuple["AnchorCycleAvailability", ...]:
    """Resolve provider availability from one coherent per-poll meta snapshot."""

    current_probe = probe_anchor_available_any
    # Preserve dependency injection used by replay/tests without adding network I/O.
    probe: Callable[[datetime], bool] = (
        current_probe
        if current_probe is not _DEFAULT_ANCHOR_PROBE
        else AnchorAvailabilityProbe()
    )
    return resolve_anchor_cycle_availability(
        now,
        probe_anchor=probe,
        max_lookback_cycles=max_lookback_cycles,
    )


@dataclass(frozen=True)
class AnchorCycleAvailability:
    """Published state of one candidate cycle's Open-Meteo anchor leg."""

    cycle: datetime
    anchor_available: bool

    @property
    def complete(self) -> bool:
        return self.anchor_available


def resolve_anchor_cycle_availability(
    now: datetime,
    *,
    probe_anchor: Callable[[datetime], bool],
    max_lookback_cycles: int = DEFAULT_MAX_LOOKBACK_CYCLES,
) -> tuple[AnchorCycleAvailability, ...]:
    """Anchor-only availability for the current replacement live chain."""

    out: list[AnchorCycleAvailability] = []
    anchor_known_available_from: datetime | None = None
    for cycle in candidate_cycles(now, max_lookback_cycles=max_lookback_cycles):
        if anchor_known_available_from is not None and cycle <= anchor_known_available_from:
            anchor_ok = True
        else:
            anchor_ok = bool(probe_anchor(cycle))
            if anchor_ok:
                anchor_known_available_from = cycle
        out.append(AnchorCycleAvailability(cycle=cycle, anchor_available=anchor_ok))
    return tuple(out)


def newest_complete_cycle(
    availability: tuple[AnchorCycleAvailability, ...],
) -> datetime | None:
    for leg in availability:  # newest→oldest order preserved
        if leg.complete:
            return leg.cycle
    return None
