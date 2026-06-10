# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: Polymarket Fee Structure V2 (effective 2026-03-30);
#   API changelog 2026-03-31 (feeSchedule on market object);
#   /tmp/fee_economics_study.md; config/reality_contracts/economic.yaml FEE_RATE_WEATHER
"""FIX 1: fee source — prefer the Gamma feeSchedule (V2 rate) over the stale
/fee-rate endpoint, fail-closed to the HIGHER value.

Live evidence (verified 2026-06-09):
  * Gamma weather market feeSchedule = {exponent:1, rate:0.05, takerOnly:true,
    rebateRate:0.25}  -> correct V2 taker rate 0.05 (5%).
  * CLOB /fee-rate?token_id=... returns base_fee=1000 (1000 bps = 0.10) for the
    SAME weather token -> a 2x overestimate of the taker fee.
"""

from __future__ import annotations

import pytest

from src.data.market_scanner import _fee_details_gamma_first, _gamma_fee_schedule_raw


class _StaleFeeRateClob:
    """A clob whose /fee-rate returns the stale base_fee=1000 (0.10)."""

    def __init__(self, *, base_fee: float | None = 1000.0, raise_exc: bool = False):
        self._base_fee = base_fee
        self._raise = raise_exc

    def get_fee_rate_details(self, token_id: str) -> dict:
        if self._raise:
            raise RuntimeError("fee-rate endpoint unavailable")
        return {"base_fee": self._base_fee, "source": "clob_fee_rate", "token_id": token_id}


_GAMMA_WEATHER = {
    "feeType": "weather_fees",
    "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.25},
}


def test_gamma_fee_schedule_preferred_over_stale_fee_rate():
    # /fee-rate says 0.10 (stale base_fee=1000); Gamma feeSchedule says 0.05.
    # The V2 Gamma rate must win — fees must NOT be doubled.
    details = _fee_details_gamma_first(
        _StaleFeeRateClob(base_fee=1000.0),
        "weather-token",
        _GAMMA_WEATHER,
    )
    assert details["fee_rate_fraction"] == pytest.approx(0.05)
    assert details["source"] == "gamma_fee_schedule"
    assert details["maker_rebate_rate"] == pytest.approx(0.25)


def test_fallback_to_fee_rate_when_gamma_schedule_absent():
    # No feeSchedule anywhere -> fall back to /fee-rate (the higher, safe value).
    details = _fee_details_gamma_first(
        _StaleFeeRateClob(base_fee=1000.0),
        "weather-token",
        {"id": "no-fee-schedule-here"},
    )
    assert details["fee_rate_fraction"] == pytest.approx(0.10)
    assert details["source"] == "clob_fee_rate"


def test_gamma_schedule_authoritative_even_when_fee_rate_is_higher():
    # The Gamma feeSchedule is the V2 authority. Even when /fee-rate reports a
    # HIGHER stale value, the parseable Gamma rate wins — it must NOT be inflated
    # back to the stale endpoint value (that is the 2x-overestimate bug).
    clob = _StaleFeeRateClob(base_fee=2000.0)  # 0.20 stale
    details = _fee_details_gamma_first(clob, "tok", _GAMMA_WEATHER)
    assert details["fee_rate_fraction"] == pytest.approx(0.05)
    assert details["source"] == "gamma_fee_schedule"


def test_gamma_schedule_stands_when_fee_rate_endpoint_unavailable():
    # Gamma feeSchedule present, /fee-rate raises -> Gamma rate stands (do not
    # abort capture just because the redundant endpoint is down).
    details = _fee_details_gamma_first(
        _StaleFeeRateClob(raise_exc=True),
        "weather-token",
        _GAMMA_WEATHER,
    )
    assert details["fee_rate_fraction"] == pytest.approx(0.05)
    assert details["source"] == "gamma_fee_schedule"


def test_gamma_fee_schedule_raw_accepts_snake_and_camel_case():
    schedule, fee_type = _gamma_fee_schedule_raw(
        {"fee_schedule": {"exponent": 1, "rate": 0.05}, "fee_type": "weather_fees"}
    )
    assert schedule == {"exponent": 1, "rate": 0.05}
    assert fee_type == "weather_fees"

    schedule2, fee_type2 = _gamma_fee_schedule_raw({"feeSchedule": {"rate": 0.04}})
    assert schedule2 == {"rate": 0.04}
    assert fee_type2 is None

    assert _gamma_fee_schedule_raw({"no": "schedule"}) == (None, None)
