# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: post-peak harvester build 2026-06-14 (London 22C BUY NO live
#   proof 2026-06-13). RED-on-revert relationship tests for the repricing-latency
#   edge: locked max -> impossible bin surfaced; still-climbing -> not surfaced;
#   paranoid guard rejects a one-notch-away bin; back-test grades realized NO win.
"""Relationship tests for the post-peak microstructure harvester.

Contracts under test:
  C1. LOCKED MAX -> SURFACE: a city past its peak with a settlement-station METAR
      max flat/declining for >=1h surfaces a near-impossible LOWER bin's cheap NO.
  C2. STILL CLIMBING -> NO SURFACE: a city whose METAR max advanced within the
      lock window is NOT post-peak; nothing is surfaced.
  C3. PARANOID GUARD: a bin exactly one notch above the locked max is rejected
      (the unfair +1-notch model makes it reachable, flipping its NO non-+EV) even
      though a far-below bin survives.
  C4. BACK-TEST GRADING: realized NO win-rate is graded against settlement truth;
      a NO on a bin the day did NOT settle into is graded a win with positive P&L.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.day0_fast_obs import MetarReport
from src.strategy.post_peak_harvester import (
    DEFAULT_EDGE_MARGIN_CENTS,
    NEAR_IMPOSSIBLE_P_MAX,
    bin_probabilities_post_peak,
    determine_post_peak_window,
    evaluate_bin_opportunity,
    scan_event_for_opportunities,
)
from src.strategy.post_peak_backtest import run_backtest
from src.types.market import Bin

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures: a synthetic London-like C-settled city + METAR series.
# ---------------------------------------------------------------------------
def _london() -> City:
    # EGLC is London City airport ICAO; C-settled, wu_icao. Faithful-station so
    # the fast-obs source resolves (the live London 22C trade settled here).
    return City(
        name="London",
        lat=51.505,
        lon=0.055,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="europe",
        wu_station="EGLC",
        slug_names=("london",),
        settlement_source_type="wu_icao",
        historical_peak_hour=15.0,
    )


def _metar(station: str, obs_time: datetime, temp_c: float) -> MetarReport:
    # C-settled city consumes whole-C verbatim; no T-group needed (unit law).
    return MetarReport(
        station_id=station,
        obs_time=obs_time,
        receipt_time=obs_time + timedelta(minutes=4),
        temp_c=temp_c,
        metar_type="METAR",
        raw=f"EGLC {temp_c:.0f}C",
    )


def _locked_max_reports(city: City, target_local_date) -> tuple[list[MetarReport], datetime]:
    """A series that climbs to 22C by ~13:00 local then DECLINES — max locked.

    now_utc is set to ~17:00 local, well past the 15:00 peak and >1h after the
    last max advance.
    """
    tz = city.timezone
    from zoneinfo import ZoneInfo

    z = ZoneInfo(tz)
    # Build hourly reports 09:00..17:00 local; max 22C at 13:00 then decline.
    series = {
        9: 16.0, 10: 18.0, 11: 20.0, 12: 21.0, 13: 22.0,
        14: 21.0, 15: 20.0, 16: 19.0, 17: 18.0,
    }
    reports = []
    for hour, temp in series.items():
        local = datetime(
            target_local_date.year, target_local_date.month, target_local_date.day,
            hour, 0, tzinfo=z,
        )
        reports.append(_metar("EGLC", local.astimezone(UTC), temp))
    now_utc = datetime(
        target_local_date.year, target_local_date.month, target_local_date.day,
        17, 5, tzinfo=z,
    ).astimezone(UTC)
    return reports, now_utc


def _still_climbing_reports(city: City, target_local_date) -> tuple[list[MetarReport], datetime]:
    """A series still advancing: max set at the LATEST report. now ~15:30 local."""
    from zoneinfo import ZoneInfo

    z = ZoneInfo(city.timezone)
    series = {9: 16.0, 10: 18.0, 11: 20.0, 12: 21.0, 13: 22.0, 14: 23.0, 15: 24.0}
    reports = []
    for hour, temp in series.items():
        local = datetime(
            target_local_date.year, target_local_date.month, target_local_date.day,
            hour, 0, tzinfo=z,
        )
        reports.append(_metar("EGLC", local.astimezone(UTC), temp))
    now_utc = datetime(
        target_local_date.year, target_local_date.month, target_local_date.day,
        15, 5, tzinfo=z,
    ).astimezone(UTC)
    return reports, now_utc


# ---------------------------------------------------------------------------
# C1 + C2: window determination.
# ---------------------------------------------------------------------------
def test_locked_max_is_post_peak():
    city = _london()
    target = datetime(2026, 6, 13).date()
    sem = SettlementSemantics.for_city(city)
    reports, now_utc = _locked_max_reports(city, target)

    window = determine_post_peak_window(
        city=city, target_date=target, reports=reports, semantics=sem, now_utc=now_utc
    )
    assert window.is_post_peak, window.reason
    assert window.rounded_max_bin_value == 22
    assert window.minutes_since_max_advance is not None
    assert window.minutes_since_max_advance >= 60.0


def test_still_climbing_is_not_post_peak():
    city = _london()
    target = datetime(2026, 6, 13).date()
    sem = SettlementSemantics.for_city(city)
    reports, now_utc = _still_climbing_reports(city, target)

    window = determine_post_peak_window(
        city=city, target_date=target, reports=reports, semantics=sem, now_utc=now_utc
    )
    assert not window.is_post_peak
    assert "max_not_locked" in window.reason or "before_peak_hour" in window.reason


# ---------------------------------------------------------------------------
# C1 (surface) + C3 (paranoid guard): bin-level opportunity.
# ---------------------------------------------------------------------------
def _c_bins() -> list[Bin]:
    """C-point bins 18..25 plus open shoulders, matching a London high market."""
    bins = [Bin(low=None, high=17, unit="C", label="17°C or below")]
    bins += [Bin(low=t, high=t, unit="C", label=f"{t}°C") for t in range(18, 26)]
    bins.append(Bin(low=26, high=None, unit="C", label="26°C or above"))
    return bins


def test_impossible_lower_bin_surfaces_cheap_no():
    """A bin well BELOW the locked 22C max is impossible -> its cheap NO surfaces."""
    city = _london()
    sem = SettlementSemantics.for_city(city)
    bins = _c_bins()
    high_so_far = 22.0
    sigma = 0.5

    obs = bin_probabilities_post_peak(
        bins=bins, high_so_far=high_so_far, semantics=sem, sigma=sigma, paranoid=False
    )
    paranoid = bin_probabilities_post_peak(
        bins=bins, high_so_far=high_so_far, semantics=sem, sigma=sigma, paranoid=True
    )
    # The "19°C" bin (3 notches below max) is impossible under BOTH models.
    idx_19 = next(i for i, b in enumerate(bins) if b.label == "19°C")
    assert obs[idx_19].p_obs == pytest.approx(0.0, abs=1e-9)
    assert paranoid[idx_19].p_paranoid == pytest.approx(0.0, abs=1e-9)

    window = SimpleNamespace(rounded_max_bin_value=22, minutes_since_max_advance=120.0)
    merged = SimpleNamespace(
        label="19°C", bin_low=19, bin_high=19,
        p_obs=obs[idx_19].p_obs, p_paranoid=paranoid[idx_19].p_paranoid,
        spike_break_threshold=18.5,
    )
    opp = evaluate_bin_opportunity(
        city=city, target_date="2026-06-13", bin_prob=merged,
        no_token_id="tok_no_19", condition_id="cid_19",
        no_ask=0.4667,  # the live London fill price
        depth_shares_at_ask=200.0, fee_rate=0.02, window=window,
        station_id="EGLC", now_utc=datetime(2026, 6, 13, 16, 0, tzinfo=UTC),
    )
    assert opp is not None
    assert opp.bin_low == 19 and opp.bin_high == 19
    assert opp.edge_cents > DEFAULT_EDGE_MARGIN_CENTS
    assert opp.paranoid_edge_cents > 0.0
    assert 25.0 <= opp.size_usd <= 40.0


def test_paranoid_guard_rejects_marginal_two_notch_spike_bin():
    """The two-notch (+2°C) bin above a locked max is near-impossible under the
    HONEST model (passes G1) but reachable under the unfair +1-notch paranoid
    model. The paranoid guard (G3) is what keeps it ONLY when the NO ask is cheap
    enough to survive that unfair repricing — London survives, Paris/Munich don't.

    This is the load-bearing guard test: it surfaces at the cheap London ask and
    is rejected by the GUARD (not G1) at a marginal ask.
    """
    city = _london()
    sem = SettlementSemantics.for_city(city)
    bins = _c_bins()
    high_so_far = 22.0
    sigma = 0.5

    obs = bin_probabilities_post_peak(
        bins=bins, high_so_far=high_so_far, semantics=sem, sigma=sigma, paranoid=False
    )
    paranoid = bin_probabilities_post_peak(
        bins=bins, high_so_far=high_so_far, semantics=sem, sigma=sigma, paranoid=True
    )
    idx_24 = next(i for i, b in enumerate(bins) if b.label == "24°C")
    # 24C is near-impossible under the honest model (passes G1)...
    assert obs[idx_24].p_obs <= NEAR_IMPOSSIBLE_P_MAX
    # ...but the unfair +1-notch model gives it real mass.
    assert paranoid[idx_24].p_paranoid > 0.3

    window = SimpleNamespace(rounded_max_bin_value=22, minutes_since_max_advance=120.0)
    merged = SimpleNamespace(
        label="24°C", bin_low=24, bin_high=24,
        p_obs=obs[idx_24].p_obs, p_paranoid=paranoid[idx_24].p_paranoid,
        spike_break_threshold=23.5,
    )

    def _eval(ask: float):
        return evaluate_bin_opportunity(
            city=city, target_date="2026-06-13", bin_prob=merged,
            no_token_id="tok_no_24", condition_id="cid_24",
            no_ask=ask, depth_shares_at_ask=200.0, fee_rate=0.02, window=window,
            station_id="EGLC", now_utc=datetime(2026, 6, 13, 16, 0, tzinfo=UTC),
        )

    # CHEAP ask (London case): survives the paranoid guard -> surfaced.
    cheap = _eval(0.30)
    assert cheap is not None
    assert cheap.paranoid_edge_cents > 0.0
    assert cheap.bin_low == 24

    # MARGINAL ask (Paris/Munich case): the paranoid guard flips it negative ->
    # rejected, EVEN THOUGH it passed G1 and the honest edge is positive.
    honest_edge_at_marginal = ((1.0 - 0.02 * 0.55 * (1 - 0.55)) - 0.55) * 100.0
    assert honest_edge_at_marginal > DEFAULT_EDGE_MARGIN_CENTS  # honest model would accept
    # Direct proof the GUARD CONDITION fires: paranoid fair NO < marginal cost.
    fee_at_055 = 0.02 * 0.55 * (1 - 0.55)
    paranoid_fair_no = 1.0 - paranoid[idx_24].p_paranoid
    assert (paranoid_fair_no - (0.55 + fee_at_055)) <= 0.0  # paranoid edge non-positive
    assert _eval(0.55) is None  # so the harvester rejects it


# ---------------------------------------------------------------------------
# End-to-end through scan_event_for_opportunities with a stub NO-ask provider.
# ---------------------------------------------------------------------------
def _synthetic_event(city: City, target: str) -> dict:
    """A minimal Gamma-shaped event with C-point bins 18..25 + shoulders."""
    import json

    def _child(question: str, cid: str) -> dict:
        return {
            "question": question,
            "conditionId": cid,
            "questionID": f"q{cid}",
            "id": f"gm_{cid}",
            "clobTokenIds": json.dumps([f"yes_{cid}", f"no_{cid}"]),
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.05", "0.95"]),
            "active": False,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
        }

    markets = []
    # Open-low shoulder "17°C or below" so the partition is MECE (left edge -inf).
    markets.append(
        _child("Will the high temperature in London be 17°C or below on June 13?", "cid_lo")
    )
    # Exact integer bins 18..25.
    for t in range(18, 26):
        markets.append(
            _child(
                f"Will the high temperature in London be {t}°C on June 13?",
                f"cid_{t}",
            )
        )
    # Open-high shoulder "26°C or above" so the right edge is +inf.
    markets.append(
        _child("Will the high temperature in London be 26°C or above on June 13?", "cid_hi")
    )
    return {
        "event_id": "evt_london_0613",
        "slug": "highest-temperature-in-london-on-june-13-2026",
        "title": "Highest temperature in London on June 13?",
        "city": city,
        "target_date": target,
        "temperature_metric": "high",
        "markets": markets,
    }


def test_scan_event_surfaces_only_impossible_lower_bins():
    city = _london()
    target = "2026-06-13"
    event = _synthetic_event(city, target)
    reports, now_utc = _locked_max_reports(city, datetime(2026, 6, 13).date())

    # Stub provider: every NO ask cheap (0.30) with deep book. Honest model gates
    # which bins are near-impossible; paranoid guard gates the one-notch-up bin.
    def _provider(no_token_id: str) -> tuple[float, float]:
        return 0.30, 500.0

    opps = scan_event_for_opportunities(
        event=event, reports=reports, no_ask_provider=_provider,
        fee_rate=0.02, now_utc=now_utc,
    )
    # bin_low/bin_high identify the bin; the label is the full Gamma question.
    surfaced_lows = {o.bin_low for o in opps}
    # Bins strictly below the locked 22C max are impossible -> surfaced (19,20,21).
    assert 19 in surfaced_lows
    assert 20 in surfaced_lows
    assert 21 in surfaced_lows
    # The 22C bin (the current max itself) is NOT surfaced (it IS the likely max).
    assert 22 not in surfaced_lows
    # The 23C bin (ONE notch above max) is rejected by the PARANOID GUARD: the
    # unfair +1-notch model makes 23C reachable, flipping its NO non-+EV.
    assert 23 not in surfaced_lows
    # The far-impossible immediately-below bins carry the largest paranoid edge.
    # (scan_event_for_opportunities returns bin-order; scan_active_markets is what
    # ranks across events — see test_scan_active_markets_ranks_by_paranoid_edge.)
    by_low = {o.bin_low: o.paranoid_edge_cents for o in opps}
    assert by_low[19] >= by_low[24]  # 1-below-max impossible beats 2-above-max


def test_scan_event_still_climbing_surfaces_nothing():
    city = _london()
    target = "2026-06-13"
    event = _synthetic_event(city, target)
    reports, now_utc = _still_climbing_reports(city, datetime(2026, 6, 13).date())

    def _provider(no_token_id: str) -> tuple[float, float]:
        return 0.30, 500.0

    opps = scan_event_for_opportunities(
        event=event, reports=reports, no_ask_provider=_provider,
        fee_rate=0.02, now_utc=now_utc,
    )
    assert opps == []


def test_scan_active_markets_ranks_by_paranoid_edge(monkeypatch):
    """scan_active_markets sorts the surfaced opportunities by paranoid edge desc."""
    import src.strategy.post_peak_harvester as h

    city = _london()
    target = "2026-06-13"
    event = _synthetic_event(city, target)
    reports, now_utc = _locked_max_reports(city, datetime(2026, 6, 13).date())

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets", lambda **kw: [event]
    )
    monkeypatch.setattr(h, "fetch_metar_reports", lambda stations, hours=36.0: reports)

    client = SimpleNamespace(get_best_ask=lambda tok: (0.30, 500.0))

    opps = h.scan_active_markets(
        now_utc=now_utc, client=client, fee_rate=0.02,
    )
    assert opps
    edges = [o.paranoid_edge_cents for o in opps]
    assert edges == sorted(edges, reverse=True)


# ---------------------------------------------------------------------------
# C4: back-test grading.
# ---------------------------------------------------------------------------
def test_backtest_grades_no_win_and_pnl():
    city = _london()
    target = "2026-06-13"
    event = _synthetic_event(city, target)
    reports, now_utc = _locked_max_reports(city, datetime(2026, 6, 13).date())

    def _provider(no_token_id: str) -> tuple[float, float]:
        return 0.4667, 500.0

    opps = scan_event_for_opportunities(
        event=event, reports=reports, no_ask_provider=_provider,
        fee_rate=0.02, now_utc=now_utc,
    )
    assert opps, "expected surfaced opportunities to grade"

    # Settlement truth: the day settled at 22C (matching the locked max). Every
    # surfaced NO is on a bin BELOW 22C, so every NO WINS.
    def _settlement(city_name: str, tdate: str) -> int:
        return 22

    report = run_backtest(opps, _settlement)
    assert report.n_settled == len(opps)
    assert report.n_no_won == len(opps)
    assert report.realized_no_win_rate == pytest.approx(1.0)
    assert report.total_pnl_cents_per_share > 0.0
    assert report.weighted_pnl_usd > 0.0

    # And the edge proof gap is non-negative (realized >= predicted obs P(NO)).
    assert report.edge_gap is not None
    assert report.edge_gap >= -1e-9


def test_backtest_grades_no_loss_when_settles_in_bin():
    """A NO on a bin the day DID settle into is graded a loss with negative P&L."""
    from src.strategy.post_peak_harvester import HarvestOpportunity

    opp = HarvestOpportunity(
        city="London", target_date="2026-06-13", bin_label="22°C",
        bin_low=22, bin_high=22, no_token_id="no_22", condition_id="cid_22",
        no_ask=0.40, fee_rate=0.02, p_obs_no=0.5, p_paranoid_no=0.5,
        edge_cents=10.0, paranoid_edge_cents=5.0, size_usd=40.0, kelly_fraction=0.1,
        spike_break_threshold=21.5, depth_shares_at_ask=100.0, depth_capped=False,
        rounded_max_bin_value=22, minutes_since_max_advance=120.0,
        station_id="EGLC", decision_time="2026-06-13T16:00:00+00:00",
    )

    def _settlement(city_name: str, tdate: str) -> int:
        return 22  # settles IN the 22C bin -> NO loses.

    report = run_backtest([opp], _settlement)
    assert report.n_settled == 1
    assert report.n_no_won == 0
    assert report.realized_no_win_rate == pytest.approx(0.0)
    assert report.total_pnl_cents_per_share < 0.0
