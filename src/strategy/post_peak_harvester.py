# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: post-peak microstructure edge proven live 2026-06-13
#   (London "22°C" bin BUY NO filled 58 @ 0.4667, +20c MTM). The edge is
#   REPRICING LATENCY, not forecast skill: once a city passes its daily
#   temperature peak (settlement-station METAR max-so-far locked / declining),
#   Polymarket is slow to reprice bins the observed max has made near-impossible,
#   so their NO still trades cheap. We buy NO on those. The end-of-day window is
#   already efficient (market tracks obs instantly); the edge lives in the
#   POST-PEAK-but-pre-settlement window.
#
#   Reuse provenance (audited 2026-06-14, all CURRENT_REUSABLE):
#   - src/data/day0_fast_obs.py (running_extremes_for_local_day, fast_obs_source_for_city,
#     fetch_metar_reports): REAL ICAO settlement-station METAR max-so-far, with the
#     settlement-faithfulness gate. Authority basis 2026-06-13.
#   - src/contracts/settlement_semantics.py (SettlementSemantics.for_city,
#     settlement_preimage_offsets): wmo_half_up rounding + the single declarative
#     preimage convention. Authority 2026-05-18 / 2026-04-27.
#   - src/signal/ensemble_signal.py (sigma_instrument_for_city): per-city sensor sigma.
#   - src/strategy/fees.py (venue_fee_rate, phi): canonical Polymarket fee function.
#   - src/strategy/kelly.py (fractional-Kelly basis): sizing.
#   - src/data/polymarket_client.py (PolymarketClient.get_best_ask /
#     get_orderbook_snapshots): live NO ask + book depth.
#   - src/data/market_scanner.py (find_weather_markets, build_market_support_topology):
#     active city/date market topology + bin partition.
"""Post-peak microstructure harvester: a SCANNER that surfaces and sizes ranked,
recorded BUY-NO opportunities on near-impossible bins of post-peak temperature
markets. It does NOT place orders — execution stays a separate verified gated step.

First principles
----------------
A daily-high temperature market settles on the city's locked daily maximum at the
real ICAO settlement station (the airport METAR — NOT the city-grid Open-Meteo
forecast, which fabricates fake mispricings; proven 2026-06-13). Once that station's
running max is LOCKED (flat or declining for >= ~1h), every bin strictly below the
max's bin is already impossible (the max has been exceeded), and every bin requiring
a large further spike above a declining max is near-impossible. The market reprices
this slowly. The harvester:

  1. Determines the POST-PEAK window per city/date (local time past the typical peak
     AND METAR max-so-far flat/declining for >= ~1h).
  2. Computes, per bin, the obs-conditioned P(bin is the day's settled max) from the
     LOCKED running max + a small Gaussian remaining-day upside tail. Bins below the
     max bin are P=0; the max bin and bins above need a future rise.
  3. Flags an opportunity when a near-impossible bin's live NO ask is cheap:
        edge_cents = ((1 - fee) - no_ask) * 100  >  margin
     and PASSES THE PARANOID SPIKE-RESILIENCE GUARD: re-price P under a deliberately
     unfair model that GRANTS a plausible +1 single-notch rise above the locked max
     (and widens sigma); keep ONLY opportunities still +EV under the paranoid model.
     This is what separated London (survived) from Paris/Munich (guard flipped them
     negative) on 2026-06-13.
  4. Sizes each by fractional-Kelly on the PARANOID edge, inside a $25-40 envelope,
     capped by live book depth at the NO ask.
  5. Emits ranked opportunities with full provenance for a recorded back-test.

NOTHING here submits an order. ``scan_*`` returns data; the operator's separate
verified execution path consumes it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from src.config import City
from src.contracts.settlement_semantics import (
    SettlementSemantics,
    settlement_preimage_offsets,
)
from src.data.day0_fast_obs import (
    MetarReport,
    fast_obs_source_for_city,
    fetch_metar_reports,
    running_extremes_for_local_day,
)
from src.types.market import Bin

logger = logging.getLogger(__name__)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Tunables (operator-visible; no hidden caps — these are HONEST physical /
# economic thresholds, per the no-caps / no-over-engineering operator law).
# ---------------------------------------------------------------------------

#: A bin is "near-impossible" when its obs-conditioned P(it is the day's max)
#: is at/below this. NO on such a bin is near-certain to win.
NEAR_IMPOSSIBLE_P_MAX = 0.05

#: Minimum post-cost edge (in cents) to surface an opportunity. The live trade
#: cleared ~13c gross of cost; 3c is a conservative floor that still admits it.
DEFAULT_EDGE_MARGIN_CENTS = 3.0

#: Window for "max-so-far has been flat/declining" — the lock confirmation.
#: The max must not have advanced within this trailing window.
PEAK_LOCK_WINDOW = timedelta(hours=1)

#: How far past the city's typical daily peak hour we require local time to be
#: before the window opens. The peak hour itself is city.historical_peak_hour.
PEAK_HOUR_MARGIN_HOURS = 0.0

#: PARANOID GUARD: the single-notch rise (in settlement units) the unfair model
#: GRANTS for free above the locked max. One notch = 1 degree on the integer grid.
PARANOID_FREE_RISE = 1.0

#: PARANOID GUARD: sigma inflation multiplier applied to the remaining-day upside
#: tail. >1 widens the tail so a borderline two-notch spike is given more mass.
PARANOID_SIGMA_MULT = 2.0

#: Sizing envelope (USD). Fractional-Kelly size is clamped into [min, max] then
#: capped by book depth at the NO ask.
SIZE_ENVELOPE_MIN_USD = 25.0
SIZE_ENVELOPE_MAX_USD = 40.0

#: Fractional-Kelly fraction applied to the paranoid edge. Conservative.
KELLY_FRACTION = 0.25

#: METAR lookback for the running-extreme computation.
METAR_FETCH_HOURS = 36.0


# ---------------------------------------------------------------------------
# Normal CDF (stdlib only — no scipy dependency in this lane).
# ---------------------------------------------------------------------------
def _phi(z: float) -> float:
    """Standard normal CDF via erf (no scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PostPeakWindow:
    """Result of the post-peak window determination for one city/date."""

    is_post_peak: bool
    reason: str
    high_so_far: Optional[float]
    rounded_max_bin_value: Optional[int]
    last_obs_time: Optional[datetime]
    minutes_since_max_advance: Optional[float]
    clock_hour: Optional[float]
    peak_hour: Optional[float]
    sample_count: int


@dataclass(frozen=True)
class BinProbability:
    """Obs-conditioned and paranoid P(this bin is the day's settled max)."""

    label: str
    bin_low: Optional[float]
    bin_high: Optional[float]
    p_obs: float          # honest obs-conditioned P(bin is day max)
    p_paranoid: float     # P under the unfair +1-notch / widened-sigma model
    spike_break_threshold: Optional[float]  # the max value that would put settlement INTO this bin


@dataclass(frozen=True)
class HarvestOpportunity:
    """A ranked, recorded BUY-NO opportunity. NOT an order."""

    city: str
    target_date: str
    bin_label: str
    bin_low: Optional[float]
    bin_high: Optional[float]
    no_token_id: str
    condition_id: str
    no_ask: float
    fee_rate: float
    p_obs_no: float           # P(NO wins) honest = 1 - p_obs(bin is max)
    p_paranoid_no: float      # P(NO wins) paranoid = 1 - p_paranoid(bin is max)
    edge_cents: float         # honest post-cost edge in cents
    paranoid_edge_cents: float
    size_usd: float
    kelly_fraction: float
    spike_break_threshold: Optional[float]
    depth_shares_at_ask: float
    depth_capped: bool
    rounded_max_bin_value: Optional[int]
    minutes_since_max_advance: Optional[float]
    station_id: str
    decision_time: str

    def as_record(self) -> dict[str, Any]:
        """Flat dict for recording into a back-test ledger."""
        return asdict(self)


# ---------------------------------------------------------------------------
# 1. Post-peak window determination
# ---------------------------------------------------------------------------
def determine_post_peak_window(
    *,
    city: City,
    target_date: date | str,
    reports: Iterable[MetarReport],
    semantics: SettlementSemantics,
    now_utc: datetime,
    peak_lock_window: timedelta = PEAK_LOCK_WINDOW,
) -> PostPeakWindow:
    """Decide whether (city, target_date) is in the POST-PEAK window.

    POST-PEAK requires BOTH:
      A. local time is past the city's typical daily peak hour
         (city.historical_peak_hour + PEAK_HOUR_MARGIN_HOURS), AND
      B. the REAL ICAO settlement-station METAR running max has NOT advanced
         within the trailing ``peak_lock_window`` (flat or declining >= ~1h).

    Uses the city's configured settlement station (city.wu_station) via the
    METAR reports — the airport METAR is the settlement truth, never a city-grid
    forecast.
    """
    target = (
        date.fromisoformat(str(target_date)[:10])
        if not isinstance(target_date, date)
        else target_date
    )
    reports = list(reports)

    extremes = running_extremes_for_local_day(
        reports, city=city, target_date=target, as_of=now_utc
    )
    if extremes.sample_count == 0 or extremes.high_so_far is None:
        return PostPeakWindow(
            is_post_peak=False,
            reason="no_metar_samples",
            high_so_far=None,
            rounded_max_bin_value=None,
            last_obs_time=None,
            minutes_since_max_advance=None,
            clock_hour=None,
            peak_hour=float(getattr(city, "historical_peak_hour", 15.0)),
            sample_count=0,
        )

    rounded_max = int(semantics.round_single(float(extremes.high_so_far)))

    # (A) local hour vs typical peak.
    tz = ZoneInfo(str(getattr(city, "timezone")))
    local_now = now_utc.astimezone(tz)
    clock_hour = local_now.hour + local_now.minute / 60.0
    peak_hour = float(getattr(city, "historical_peak_hour", 15.0))
    past_peak_hour = clock_hour >= (peak_hour + PEAK_HOUR_MARGIN_HOURS)

    # (B) running-max lock: find the last time the running max ADVANCED to its
    # current high. If the most recent advance is older than peak_lock_window,
    # the max is locked.
    minutes_since_advance, last_obs_time = _minutes_since_max_advance(
        reports, city=city, target_date=target, now_utc=now_utc
    )
    max_locked = (
        minutes_since_advance is not None
        and minutes_since_advance >= peak_lock_window.total_seconds() / 60.0
    )

    if not past_peak_hour:
        reason = f"before_peak_hour(local={clock_hour:.1f}<peak={peak_hour:.1f})"
    elif not max_locked:
        reason = (
            "max_not_locked("
            f"mins_since_advance={minutes_since_advance})"
            if minutes_since_advance is not None
            else "max_advance_time_unknown"
        )
    else:
        reason = "post_peak"

    return PostPeakWindow(
        is_post_peak=bool(past_peak_hour and max_locked),
        reason=reason,
        high_so_far=float(extremes.high_so_far),
        rounded_max_bin_value=rounded_max,
        last_obs_time=last_obs_time,
        minutes_since_max_advance=minutes_since_advance,
        clock_hour=clock_hour,
        peak_hour=peak_hour,
        sample_count=extremes.sample_count,
    )


def _minutes_since_max_advance(
    reports: Iterable[MetarReport],
    *,
    city: City,
    target_date: date,
    now_utc: datetime,
) -> tuple[Optional[float], Optional[datetime]]:
    """Minutes since the running max last advanced (strictly increased).

    Walks the city-local-day reports in time order, tracking the running max;
    records the obs_time at which the max last strictly increased. Returns
    (minutes_since_that_advance, last_obs_time). A monotone-declining tail
    yields a large value (locked); a still-climbing series yields ~0.
    """
    tz = ZoneInfo(str(getattr(city, "timezone")))
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    station = str(getattr(city, "wu_station", "") or "").strip().upper()

    # Reuse the unit law via running_extremes_for_local_day is not granular
    # enough (it returns only the aggregate); we need the per-report path, so we
    # reconstruct the time-sorted in-day settlement-unit values the same way.
    from src.data.day0_fast_obs import settlement_temp_for_report

    samples: list[tuple[datetime, float]] = []
    for report in reports:
        if report.station_id != station:
            continue
        if report.obs_time > now_utc:
            continue
        if report.obs_time.astimezone(tz).date() != target_date:
            continue
        value = settlement_temp_for_report(report, unit)
        if value is None:
            continue
        samples.append((report.obs_time, float(value)))

    if not samples:
        return None, None
    samples.sort(key=lambda item: item[0])

    running_max = -math.inf
    last_advance_time: Optional[datetime] = None
    for obs_time, value in samples:
        if value > running_max:
            running_max = value
            last_advance_time = obs_time
    last_obs_time = samples[-1][0]
    if last_advance_time is None:
        return None, last_obs_time
    minutes = (now_utc - last_advance_time).total_seconds() / 60.0
    return minutes, last_obs_time


# ---------------------------------------------------------------------------
# 2. Obs-conditioned + paranoid P(bin is the day's max)
# ---------------------------------------------------------------------------
def bin_probabilities_post_peak(
    *,
    bins: list[Bin],
    high_so_far: float,
    semantics: SettlementSemantics,
    sigma: float,
    paranoid: bool = False,
) -> list[BinProbability]:
    """Obs-conditioned P(each bin is the day's settled max), given a LOCKED max.

    Model (conditioned on the observed running max ``high_so_far``, NOT a forecast):
      - The day's final settled max M >= high_so_far (the observed max is a lower
        bound; settlement can only equal or exceed it).
      - Remaining-day upside is a one-sided Gaussian tail: the additional rise R
        above ``high_so_far`` is modelled as |N(0, sigma)| in settlement units,
        so the surviving probability of reaching a value v is
            P(M >= v) = 2 * (1 - Phi((v - high_so_far) / sigma))   for v > high_so_far
            P(M >= v) = 1                                          for v <= high_so_far
      - A bin's P(it is the max) = P(M lands in the bin's settlement preimage)
        = P(M >= preimage_low) - P(M >= preimage_high_exclusive), telescoped over
        the bin's integer settlement values using the contract preimage offsets.

    Bins strictly below the rounded max are P=0 (the observed max already exceeded
    them; settlement cannot land below the observed max). This is the source of the
    near-certain NO.

    PARANOID mode (``paranoid=True``): the unfair model shifts ``high_so_far`` DOWN
    by one free notch (i.e. grants a plausible +1 single-notch future rise as if it
    had already happened) AND widens sigma by ``PARANOID_SIGMA_MULT``. This is the
    spike-resilience guard: only opportunities still +EV under THIS model survive.
    """
    if sigma <= 0:
        sigma = 1e-6

    if paranoid:
        # Grant a free single-notch rise: treat the effective floor as one notch
        # ABOVE the observed max (the max is pretended to already be +1), and widen
        # the tail. This makes bins one notch above the real max NO-LONGER
        # near-impossible, so their NO opportunity is rejected unless the ask is
        # cheap enough to survive even this unfair repricing.
        eff_high = float(high_so_far) + PARANOID_FREE_RISE
        eff_sigma = float(sigma) * PARANOID_SIGMA_MULT
    else:
        eff_high = float(high_so_far)
        eff_sigma = float(sigma)

    low_off, high_off = settlement_preimage_offsets(semantics.rounding_rule)

    def _p_m_geq(v: float) -> float:
        """P(final settled max M >= v) under the one-sided upside tail."""
        if v <= eff_high:
            return 1.0
        z = (v - eff_high) / eff_sigma
        return max(0.0, min(1.0, 2.0 * (1.0 - _phi(z))))

    out: list[BinProbability] = []
    for b in bins:
        # The bin's settlement preimage in continuous temperature space.
        # Integer settlement value t falls in this bin iff bin.contains(t).
        # The preimage of {a..b} is [a + low_off, b + high_off).
        lo = b.low
        hi = b.high
        # Continuous preimage edges (shoulders → +/- inf).
        if lo is None:
            preimage_lo = -math.inf
        else:
            preimage_lo = float(lo) + low_off
        if hi is None:
            preimage_hi = math.inf
        else:
            preimage_hi = float(hi) + high_off

        # P(M in [preimage_lo, preimage_hi)) = P(M >= preimage_lo) - P(M >= preimage_hi)
        p_lo = _p_m_geq(preimage_lo)
        p_hi = _p_m_geq(preimage_hi)
        p_bin = max(0.0, p_lo - p_hi)

        # spike_break_threshold: the smallest observed-max value that would put
        # settlement INTO this bin (the lower preimage edge). For a bin at/above
        # the current max this is the temperature the city must still reach.
        spike_break = None if lo is None else float(lo) + low_off

        # One pass fills exactly one of (p_obs, p_paranoid); _merge_obs_paranoid
        # zips the honest and paranoid passes into a single record.
        out.append(
            BinProbability(
                label=b.label,
                bin_low=b.low,
                bin_high=b.high,
                p_obs=0.0 if paranoid else p_bin,
                p_paranoid=p_bin if paranoid else 0.0,
                spike_break_threshold=spike_break,
            )
        )
    return out


def _merge_obs_paranoid(
    obs: list[BinProbability], paranoid: list[BinProbability]
) -> list[BinProbability]:
    """Zip the honest and paranoid passes into one list carrying both P values."""
    merged: list[BinProbability] = []
    for o, p in zip(obs, paranoid):
        merged.append(
            BinProbability(
                label=o.label,
                bin_low=o.bin_low,
                bin_high=o.bin_high,
                p_obs=o.p_obs,
                p_paranoid=p.p_paranoid,
                spike_break_threshold=o.spike_break_threshold,
            )
        )
    return merged


# ---------------------------------------------------------------------------
# 3 + 4. Edge, paranoid guard, sizing
# ---------------------------------------------------------------------------
def evaluate_bin_opportunity(
    *,
    city: City,
    target_date: str,
    bin_prob: BinProbability,
    no_token_id: str,
    condition_id: str,
    no_ask: float,
    depth_shares_at_ask: float,
    fee_rate: float,
    window: PostPeakWindow,
    station_id: str,
    now_utc: datetime,
    edge_margin_cents: float = DEFAULT_EDGE_MARGIN_CENTS,
    near_impossible_p_max: float = NEAR_IMPOSSIBLE_P_MAX,
) -> Optional[HarvestOpportunity]:
    """Turn one bin into a ranked BUY-NO opportunity, or None if it fails a gate.

    Gates (all must pass):
      G1. The bin is NEAR-IMPOSSIBLE under the honest obs-conditioned model
          (p_obs(bin is max) <= near_impossible_p_max). NO is near-certain.
      G2. Honest post-cost edge exceeds the margin:
            edge_cents = ((1 - fee_at_ask) - no_ask) * 100 > edge_margin_cents
          where fee_at_ask is the per-share taker fee phi(1, no_ask, fee_rate)
          expressed in price units (same space as the ask).
      G3. PARANOID GUARD: still +EV under the unfair +1-notch / widened-sigma
          model — paranoid_edge_cents > 0. This is the London-survives /
          Paris-Munich-rejected separator.

    Size = fractional-Kelly on the PARANOID edge, clamped to [25,40] USD, then
    capped by book depth at the NO ask.
    """
    if not (0.0 < no_ask < 1.0):
        return None

    p_obs_bin_is_max = float(bin_prob.p_obs)
    p_paranoid_bin_is_max = float(bin_prob.p_paranoid)

    # G1: near-impossible under the honest model.
    if p_obs_bin_is_max > near_impossible_p_max:
        return None

    # Per-share taker fee at the NO ask, in price units. phi = q*r*p*(1-p);
    # for one share q=1 this is r*p*(1-p), already in price space.
    fee_at_ask = float(fee_rate) * no_ask * (1.0 - no_ask)

    # NO pays out 1 if the bin does NOT settle; cost = ask + fee.
    # Honest post-cost edge per share (price units): (1 - fee) - ask, but the
    # honest fair value of NO is (1 - p_obs_bin_is_max). We require BOTH the raw
    # cheapness margin (G2) and the paranoid +EV (G3). G2 uses the cheapness form
    # the live trade used: ((1 - fee) - ask).
    edge_cents = ((1.0 - fee_at_ask) - no_ask) * 100.0
    if edge_cents <= edge_margin_cents:
        return None

    # G3 paranoid: fair NO value under the unfair model = 1 - p_paranoid(bin is max).
    paranoid_fair_no = 1.0 - p_paranoid_bin_is_max
    paranoid_edge_cents = (paranoid_fair_no - (no_ask + fee_at_ask)) * 100.0
    if paranoid_edge_cents <= 0.0:
        return None

    # Sizing: fractional-Kelly on the PARANOID edge (the conservative one).
    # Kelly fraction for a binary at price ask paying 1: f* = (p - ask)/(1 - ask),
    # with p = paranoid_fair_no (NO win prob under the unfair model).
    p_no = paranoid_fair_no
    denom = 1.0 - no_ask
    f_star = 0.0 if denom <= 0 else max(0.0, (p_no - no_ask) / denom)
    kelly_size_unclamped = f_star * KELLY_FRACTION  # as a fraction of the envelope

    # Map the Kelly fraction onto the $25-40 envelope: full Kelly (f_star=1) →
    # SIZE_ENVELOPE_MAX, scaled linearly, floored at MIN when any positive size.
    raw_size = SIZE_ENVELOPE_MAX_USD * kelly_size_unclamped / KELLY_FRACTION
    if raw_size <= 0.0:
        return None
    size_usd = min(SIZE_ENVELOPE_MAX_USD, max(SIZE_ENVELOPE_MIN_USD, raw_size))

    # Depth cap: shares affordable at the ask, vs shares available at best ask.
    shares_wanted = size_usd / no_ask
    depth_capped = False
    if depth_shares_at_ask > 0 and shares_wanted > depth_shares_at_ask:
        shares_wanted = depth_shares_at_ask
        size_usd = shares_wanted * no_ask
        depth_capped = True

    return HarvestOpportunity(
        city=str(getattr(city, "name", "") or ""),
        target_date=str(target_date),
        bin_label=bin_prob.label,
        bin_low=bin_prob.bin_low,
        bin_high=bin_prob.bin_high,
        no_token_id=str(no_token_id),
        condition_id=str(condition_id),
        no_ask=float(no_ask),
        fee_rate=float(fee_rate),
        p_obs_no=1.0 - p_obs_bin_is_max,
        p_paranoid_no=paranoid_fair_no,
        edge_cents=round(edge_cents, 3),
        paranoid_edge_cents=round(paranoid_edge_cents, 3),
        size_usd=round(size_usd, 2),
        kelly_fraction=round(f_star * KELLY_FRACTION, 4),
        spike_break_threshold=bin_prob.spike_break_threshold,
        depth_shares_at_ask=float(depth_shares_at_ask),
        depth_capped=depth_capped,
        rounded_max_bin_value=window.rounded_max_bin_value,
        minutes_since_max_advance=window.minutes_since_max_advance,
        station_id=str(station_id),
        decision_time=now_utc.astimezone(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# 5. Top-level scan
# ---------------------------------------------------------------------------
def scan_event_for_opportunities(
    *,
    event: dict,
    reports: list[MetarReport],
    no_ask_provider: Callable[[str], tuple[float, float]],
    fee_rate: float,
    now_utc: datetime,
    edge_margin_cents: float = DEFAULT_EDGE_MARGIN_CENTS,
) -> list[HarvestOpportunity]:
    """Scan ONE parsed weather event for post-peak BUY-NO opportunities.

    ``event`` is a dict from ``market_scanner.find_weather_markets`` (carries
    ``city``, ``target_date``, ``temperature_metric``, ``outcomes`` /
    ``support_topology``). Only daily-HIGH markets are eligible (the post-peak max
    edge does not apply to daily-low markets).

    ``no_ask_provider(no_token_id) -> (best_ask, depth_shares)`` supplies the LIVE
    NO ask and book depth. The caller injects the live PolymarketClient-backed
    provider (production) or a stub (tests / back-test replay).
    """
    from src.data.market_scanner import build_market_support_topology

    city: City = event.get("city")
    if city is None:
        return []
    metric = str(event.get("temperature_metric") or "high")
    if metric != "high":
        return []  # post-peak max edge is daily-HIGH only.

    target_date = str(event.get("target_date") or "")
    if not target_date:
        return []

    semantics = SettlementSemantics.for_city(city)

    # Determine the post-peak window from the REAL ICAO settlement station METAR.
    window = determine_post_peak_window(
        city=city,
        target_date=target_date,
        reports=reports,
        semantics=semantics,
        now_utc=now_utc,
    )
    if not window.is_post_peak or window.high_so_far is None:
        logger.debug(
            "POST_PEAK_SKIP city=%s date=%s reason=%s",
            getattr(city, "name", "?"), target_date, window.reason,
        )
        return []

    # Build the canonical bin partition from the event topology.
    try:
        topo = build_market_support_topology(event, unit=city.settlement_unit)
    except Exception as exc:  # noqa: BLE001 — one bad event must not kill the scan
        logger.warning(
            "POST_PEAK_TOPOLOGY_FAILED city=%s date=%s exc=%s: %s",
            getattr(city, "name", "?"), target_date, type(exc).__name__, exc,
        )
        return []
    bins = topo.support_bins
    if not bins:
        return []

    # Per-city sensor sigma drives the remaining-day upside tail.
    from src.signal.ensemble_signal import sigma_instrument_for_city

    sigma = float(sigma_instrument_for_city(city).value)

    obs_probs = bin_probabilities_post_peak(
        bins=bins,
        high_so_far=window.high_so_far,
        semantics=semantics,
        sigma=sigma,
        paranoid=False,
    )
    paranoid_probs = bin_probabilities_post_peak(
        bins=bins,
        high_so_far=window.high_so_far,
        semantics=semantics,
        sigma=sigma,
        paranoid=True,
    )
    merged = _merge_obs_paranoid(obs_probs, paranoid_probs)

    station_id = str(getattr(city, "wu_station", "") or "").strip().upper()
    opportunities: list[HarvestOpportunity] = []

    for outcome, bin_prob in zip(topo.support_outcomes, merged):
        if not outcome.get("executable"):
            continue
        no_token_id = str(outcome.get("no_token_id") or "")
        condition_id = str(outcome.get("condition_id") or outcome.get("market_id") or "")
        if not no_token_id or not condition_id:
            continue
        try:
            no_ask, depth = no_ask_provider(no_token_id)
        except Exception as exc:  # noqa: BLE001 — illiquid / missing book → skip bin
            logger.debug(
                "POST_PEAK_NO_BOOK token=%s exc=%s: %s",
                no_token_id, type(exc).__name__, exc,
            )
            continue
        opp = evaluate_bin_opportunity(
            city=city,
            target_date=target_date,
            bin_prob=bin_prob,
            no_token_id=no_token_id,
            condition_id=condition_id,
            no_ask=float(no_ask),
            depth_shares_at_ask=float(depth),
            fee_rate=fee_rate,
            window=window,
            station_id=station_id,
            now_utc=now_utc,
            edge_margin_cents=edge_margin_cents,
        )
        if opp is not None:
            opportunities.append(opp)

    return opportunities


def live_no_ask_provider(client: Any) -> Callable[[str], tuple[float, float]]:
    """Build a live NO-ask/depth provider backed by a PolymarketClient.

    Returns a callable ``no_token_id -> (best_ask, depth_shares_at_ask)`` using the
    public CLOB book (``get_best_ask``). Read-only; performs no order placement.
    """

    def _provider(no_token_id: str) -> tuple[float, float]:
        best_ask, ask_size = client.get_best_ask(no_token_id)
        return float(best_ask), float(ask_size)

    return _provider


def scan_active_markets(
    *,
    now_utc: Optional[datetime] = None,
    client: Any = None,
    fee_rate: Optional[float] = None,
    min_hours_to_resolution: float = 0.0,
    edge_margin_cents: float = DEFAULT_EDGE_MARGIN_CENTS,
) -> list[HarvestOpportunity]:
    """End-to-end LIVE scan: discover active high-temperature markets, fetch the
    settlement-station METAR, surface + size ranked post-peak BUY-NO opportunities.

    DOES NOT place any orders. Returns a list of ``HarvestOpportunity`` sorted by
    paranoid edge (descending) — the recorded, ranked surface the operator's
    separate verified execution step consumes.

    Production wiring injects a live ``PolymarketClient`` (NO ask + depth) and the
    venue fee rate; tests inject stubs via ``scan_event_for_opportunities``.
    """
    from src.data.market_scanner import find_weather_markets
    from src.strategy.fees import venue_fee_rate

    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    if fee_rate is None:
        fee_rate = float(venue_fee_rate())

    if client is None:
        from src.data.polymarket_client import PolymarketClient

        client = PolymarketClient()
    provider = live_no_ask_provider(client)

    events = find_weather_markets(min_hours_to_resolution=min_hours_to_resolution)
    high_events = [e for e in events if str(e.get("temperature_metric") or "high") == "high"]
    if not high_events:
        return []

    # One batched METAR fetch covering every settlement station in scope.
    stations: set[str] = set()
    for event in high_events:
        city = event.get("city")
        if city is None:
            continue
        source = fast_obs_source_for_city(city)
        if source is not None:
            stations.add(source.station_id)
    reports = fetch_metar_reports(stations, hours=METAR_FETCH_HOURS) if stations else []

    all_opps: list[HarvestOpportunity] = []
    for event in high_events:
        try:
            all_opps.extend(
                scan_event_for_opportunities(
                    event=event,
                    reports=reports,
                    no_ask_provider=provider,
                    fee_rate=fee_rate,
                    now_utc=now_utc,
                    edge_margin_cents=edge_margin_cents,
                )
            )
        except Exception as exc:  # noqa: BLE001 — one event must not kill the scan
            logger.warning(
                "POST_PEAK_EVENT_FAILED event=%s exc=%s: %s",
                event.get("event_id"), type(exc).__name__, exc,
            )

    all_opps.sort(key=lambda o: o.paranoid_edge_cents, reverse=True)
    return all_opps
